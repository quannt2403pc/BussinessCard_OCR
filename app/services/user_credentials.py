import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.user import User
from app.services.cliproxy_client import AuthFile, CliProxyClient, CliProxyResponseError
from app.services.llm import LLMNotConnectedError

NOT_CONNECTED = (
    "Tài khoản của bạn chưa kết nối AI — vào /settings bấm “Kết nối CLIProxy (OAuth)” "
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


def model_for(user: User) -> str:
    if not user.cliproxy_auth_file:
        raise LLMNotConnectedError(NOT_CONNECTED)
    return f"{credential_prefix(user.id)}/{settings.llm_model}"


async def model_for_user_id(db: AsyncSession, user_id: uuid.UUID) -> str:
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise LLMNotConnectedError(NOT_CONNECTED)
    return model_for(user)


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
