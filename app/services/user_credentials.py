import logging
import time
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
    model = await model_catalog.resolve(db, user.id, feature, avoid=await paid_only_models(user))
    return f"{credential_prefix(user.id)}/{model}"


async def model_for_user_id(db: AsyncSession, user_id: uuid.UUID, feature: Feature) -> str:
    """Như `model_for()` nhưng chỉ có `user_id` — dùng ở luồng nền (chat, enrich job)."""
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise LLMNotConnectedError(NOT_CONNECTED)
    return await model_for(db, user, feature)


#: Cooldown đổi theo từng phút (mỗi lần Google từ chối là một dòng mới), nhưng `model_for()` chạy
#: trên **mọi** lượt quét, lượt enrich và lượt hỏi. Nhớ lại 30 giây: đủ ngắn để một model vừa hết
#: hạn nghỉ được dùng lại gần như ngay, đủ dài để upload 50 ảnh không thành 50 lần hỏi CLIProxy.
PAID_ONLY_TTL_SECONDS = 30.0

_paid_only_cache: dict[str, tuple[float, frozenset[str]]] = {}


async def paid_only_models(user: User) -> frozenset[str]:
    """Model mà **tài khoản Google của người này** bị từ chối vì gói cước (I-42).

    Đọc từ `cooldowns` của đúng credential họ đang dùng, không phải từ một bảng trong mã. Lý do
    đã đo được: `gemini-3-flash` trả `403 payment_required` với `quanpyke1@gmail.com` nhưng chạy
    tốt với `quanpyke@gmail.com` — "Pro" là thuộc tính của **cặp** *(tài khoản, model)*.

    Hỏng thì trả rỗng chứ không ném: không đọc được cooldown là chuyện của CLIProxy, không đáng
    làm hỏng lượt quét. Hệ quả xấu nhất là người dùng gặp lại đúng lỗi cũ — kèm câu giải thích
    đúng của `llm._explain_no_credential()`.
    """
    name = user.cliproxy_auth_file
    if not name:
        return frozenset()

    now = time.monotonic()
    cached = _paid_only_cache.get(name)
    if cached is not None and now - cached[0] < PAID_ONLY_TTL_SECONDS:
        return cached[1]

    try:
        async with CliProxyClient() as proxy:
            files = own_files(await proxy.auth_files(), user)
    except CliProxyError as exc:
        logger.info("Không đọc được cooldown của %s: %s", name, exc)
        return frozenset()

    blocked = (
        frozenset[str]().union(*(f.paid_only_models() for f in files)) if files else frozenset()
    )
    _paid_only_cache[name] = (now, blocked)
    return blocked


def forget_paid_only(user: User | None = None) -> None:
    """Xoá bộ nhớ cooldown. Gọi sau khi người dùng đổi credential, và trong test."""
    if user is None or not user.cliproxy_auth_file:
        _paid_only_cache.clear()
    else:
        _paid_only_cache.pop(user.cliproxy_auth_file, None)


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
    usable = [f for f in files if f.usable]
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


async def _delete_quietly(proxy: CliProxyClient, name: str) -> None:
    try:
        await proxy.delete_auth_file(name)
    except CliProxyResponseError as exc:
        if exc.status_code != 404:
            raise
