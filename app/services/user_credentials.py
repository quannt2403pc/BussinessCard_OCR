import logging
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.user import User
from app.services import model_catalog
from app.services.cliproxy_client import (
    AuthFile,
    CliProxyClient,
    CliProxyError,
    CliProxyResponseError,
)
from app.services.llm import LLMNotConnectedError
from app.services.model_catalog import Feature

logger = logging.getLogger(__name__)

NOT_CONNECTED = (
    "Tài khoản của bạn chưa kết nối AI — vào /settings bấm “Kết nối AI” "
    "bằng tài khoản Google của chính bạn."
)
PENDING_LIMIT = 200

_pending: dict[str, tuple[uuid.UUID, dict[str, str]]] = {}


class CredentialNotFoundError(Exception):
    pass


class CredentialTakenError(Exception):
    def __init__(self, label: str) -> None:
        super().__init__(label)
        self.label = label


def credential_prefix(user_id: uuid.UUID) -> str:
    return f"u{user_id.hex[:12]}"


def default_model_for(user: User) -> str:
    """Tiền tố của người dùng + model **mặc định của hệ thống**, không tra lựa chọn của ai.

    Chỉ dùng cho nút *Kiểm tra kết nối* (2.4). Câu hỏi nút đó trả lời là **"credential OAuth của
    tôi có gọi được model không"**, chứ không phải "model tôi chọn cho chức năng X có chạy không":
    người dùng có thể đặt ba model khác nhau cho ba chức năng, thử cả ba là ba lời gọi thật cho
    một cái nút chẩn đoán. Giữ nó ở model mặc định thì kết quả luôn có một nghĩa duy nhất.
    """
    if not user.cliproxy_auth_file:
        raise LLMNotConnectedError(NOT_CONNECTED)
    return f"{credential_prefix(user.id)}/{settings.llm_model}"


async def model_for(db: AsyncSession, user: User, feature: Feature) -> str:
    """Tên model gửi cho CLIProxy: `"<tiền tố của người này>/<model của chức năng này>"`.

    Hai nửa, hai nguồn khác nhau và đừng trộn lẫn:

    * **tiền tố** quyết định lời gọi đi bằng *credential OAuth của ai* (12.1, ADR đa người dùng) —
      thiếu nó thì CLIProxy xoay vòng và người A tiêu quota của người B;
    * **tên model** quyết định *model nào* trả lời, nay chọn được riêng cho từng chức năng
      (`EX-14`, `docs/adr-model-per-feature.md`).

    Từ `EX-14` hàm này **bất đồng bộ và cần `db`**: lựa chọn model nằm trong bảng
    `user_model_prefs`. Trước đó nó chỉ ghép `settings.llm_model` nên không cần gì cả.
    """
    if not user.cliproxy_auth_file:
        raise LLMNotConnectedError(NOT_CONNECTED)
    model = await model_catalog.resolve(db, user.id, feature)
    return f"{credential_prefix(user.id)}/{model}"


async def model_for_user_id(db: AsyncSession, user_id: uuid.UUID, feature: Feature) -> str:
    """Như `model_for()` nhưng chỉ có `user_id` — dùng ở luồng nền (chat, enrich job)."""
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise LLMNotConnectedError(NOT_CONNECTED)
    return await model_for(db, user, feature)


def own_files(files: Sequence[AuthFile], user: User) -> list[AuthFile]:
    if not user.cliproxy_auth_file:
        return []
    return [f for f in files if f.name == user.cliproxy_auth_file]


def _stamp(auth_file: AuthFile) -> str:
    return str(auth_file.raw.get("modtime") or auth_file.raw.get("updated_at") or "")


def remember_session(state: str, user_id: uuid.UUID, files: Sequence[AuthFile]) -> None:
    if len(_pending) >= PENDING_LIMIT:
        _pending.pop(next(iter(_pending)))
    _pending[state] = (user_id, {f.name: _stamp(f) for f in files})


def owns_session(state: str, user_id: uuid.UUID) -> bool:
    owner = _pending.get(state)
    return owner is None or owner[0] == user_id


def forget_session(state: str) -> None:
    _pending.pop(state, None)


def pick_new_file(files: Sequence[AuthFile], before: dict[str, str] | None) -> AuthFile | None:
    """Credential vừa xuất hiện sau lượt OAuth này.

    Lọc bằng `alive` chứ **không** phải `usable` (I-47): credential mới tinh thường chưa có
    `project_id` — `claim()` gán ngay sau đây. Dùng `usable` thì không cái nào lọt qua và người
    dùng không kết nối nổi, dù luồng OAuth vừa chạy hoàn hảo.
    """
    usable = [f for f in files if f.alive]
    if before is not None:
        usable = [f for f in usable if before.get(f.name) != _stamp(f)]
    return max(usable, key=_stamp, default=None)


async def claim(db: AsyncSession, user: User, proxy: CliProxyClient, state: str) -> AuthFile:
    pending = _pending.pop(state, None)
    if pending is not None and pending[0] != user.id:
        raise CredentialNotFoundError(state)
    before = pending[1] if pending is not None else None

    chosen = pick_new_file(await proxy.auth_files(), before)
    if chosen is None:
        raise CredentialNotFoundError(state)

    holder = await db.scalar(
        select(User.id).where(User.cliproxy_auth_file == chosen.name, User.id != user.id)
    )
    if holder is not None:
        await proxy.set_prefix(chosen.name, credential_prefix(holder))
        raise CredentialTakenError(chosen.label)

    await proxy.set_prefix(chosen.name, credential_prefix(user.id))
    await _ensure_project_id(proxy, chosen)
    previous = user.cliproxy_auth_file
    if previous and previous != chosen.name:
        await _delete_quietly(proxy, previous)
    user.cliproxy_auth_file = chosen.name
    await db.commit()
    return chosen


async def release(db: AsyncSession, user: User, proxy: CliProxyClient) -> list[str]:
    name = user.cliproxy_auth_file
    if not name:
        return []
    await _delete_quietly(proxy, name)
    user.cliproxy_auth_file = None
    await db.commit()
    return [name]


async def _ensure_project_id(proxy: CliProxyClient, auth_file: AuthFile) -> None:
    """Credential vừa nhận mà thiếu `project_id` thì gán cho nó (I-47).

    Làm ngay lúc `claim()` chứ không đợi tới lời gọi model đầu tiên: người dùng vừa đi hết luồng
    OAuth của 13.7 — mở tab Google, đồng ý, chép URL về dán — và phần thưởng cho tất cả công ấy
    **không được phép** là một câu `400` ở lần quét đầu tiên.

    Hỏng thì bỏ qua, không ném: credential đã lấy được là việc chính và nó đã xong. Gán hụt thì
    lời gọi sau vẫn báo đúng lý do (`llm._translate_error`), còn ném ở đây là vứt cả lượt đăng
    nhập vừa rồi đi vì một bước phụ.
    """
    if not settings.cliproxy_project_id or not auth_file.missing_project_id:
        return
    try:
        await proxy.set_project_id(auth_file.name, settings.cliproxy_project_id)
    except CliProxyError as exc:
        logger.warning("Không gán được project_id cho %s: %s", auth_file.name, exc)
    else:
        logger.info(
            "Gán project_id %r cho credential %s (I-47)",
            settings.cliproxy_project_id,
            auth_file.name,
        )


async def _delete_quietly(proxy: CliProxyClient, name: str) -> None:
    try:
        await proxy.delete_auth_file(name)
    except CliProxyResponseError as exc:
        if exc.status_code != 404:
            raise
