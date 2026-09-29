import hashlib
import secrets
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from functools import cache
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.models.user import User
from app.models.workspace import WORKSPACE_NAME_MAX_LENGTH
from app.repositories import user as user_repo
from app.repositories import workspace as workspace_repo
from app.schemas.user import check_password, clean_display_name, normalize_email

SESSION_COOKIE = "bizcard_session"
SESSION_SALT = "bizcard-session"

hasher = PasswordHasher()


class WrongPasswordError(Exception):
    pass


@dataclass(frozen=True)
class SessionClaims:
    user_id: uuid.UUID
    fingerprint: str


def session_max_age() -> timedelta:
    return timedelta(days=settings.session_max_age_days)


def hash_password(password: str) -> str:
    return hasher.hash(password)


@cache
def dummy_hash() -> str:
    return hasher.hash(secrets.token_urlsafe(24))


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return hasher.verify(password_hash, password)
    except InvalidHashError:
        _burn(password)
        return False
    except VerificationError:
        return False


def _burn(password: str) -> None:
    with suppress(VerificationError):
        hasher.verify(dummy_hash(), password)


def password_fingerprint(password_hash: str) -> str:
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt=SESSION_SALT)


def sign_session(user: User) -> str:
    payload = {"uid": str(user.id), "pv": password_fingerprint(user.password_hash)}
    return _serializer().dumps(payload)


def read_session(token: str) -> SessionClaims | None:
    try:
        payload = _serializer().loads(token, max_age=int(session_max_age().total_seconds()))
        return SessionClaims(uuid.UUID(payload["uid"]), str(payload["pv"]))
    except (BadSignature, KeyError, TypeError, ValueError):
        return None


def set_session_cookie(response: Response, user: User) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        sign_session(user),
        max_age=int(session_max_age().total_seconds()),
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
    )


def safe_next(target: str | None) -> str:
    if not target or not target.startswith("/") or target.startswith(("//", "/\\")):
        return "/"
    if target.startswith("/auth/"):
        return "/"
    return target


async def register(
    db: AsyncSession, *, email: str, password: str, display_name: str | None = None
) -> User:
    """Tạo tài khoản, **kèm luôn một workspace riêng mang tên người đó** (`I-45`).

    Từ `NEXT-05` mọi dữ liệu thuộc về workspace, nên tài khoản không có workspace thì
    `core/workspace.py::require_workspace` trả `409` ở gần như mọi đường: danh thiếp, công ty,
    số liệu, trợ lý. Revision `0015` đã vá cho những tài khoản **có sẵn lúc chạy migration**,
    nhưng chỗ này thì không — nên mọi người đăng ký sau đó rơi vào một ứng dụng không dùng được.

    Tạo ngay tại đây chứ không đợi người dùng tự bấm: người vừa đăng ký chưa biết "workspace" là
    gì, mà thứ họ gặp đầu tiên lại là một trang báo lỗi.
    """
    email = normalize_email(email)
    check_password(password, email)
    user = await user_repo.create(
        db,
        email=email,
        password_hash=hash_password(password),
        display_name=clean_display_name(display_name),
    )
    await workspace_repo.create(db, name=personal_workspace_name(user), owner_id=user.id)
    await db.commit()
    await db.refresh(user)
    return user


def personal_workspace_name(user: User) -> str:
    """Tên workspace mặc định của một người: tên hiển thị, không có thì phần trước `@` của email.

    Giống **từng chữ** quy tắc backfill của revision `0015`, để tài khoản cũ và tài khoản mới
    không ra hai kiểu tên khác nhau cho cùng một thứ.
    """
    name = (user.display_name or "").strip() or user.email.split("@", 1)[0]
    return name[:WORKSPACE_NAME_MAX_LENGTH]


async def authenticate(db: AsyncSession, email: str, password: str) -> User | None:
    try:
        normalized = normalize_email(email)
    except ValueError:
        _burn(password)
        return None
    user = await user_repo.get_by_email(db, normalized)
    if user is None:
        _burn(password)
        return None
    if not verify_password(user.password_hash, password) or not user.is_active:
        return None
    rehashed = hash_password(password) if hasher.check_needs_rehash(user.password_hash) else None
    await user_repo.record_login(db, user, password_hash=rehashed)
    return user


async def change_password(db: AsyncSession, user: User, current: str, new: str) -> None:
    if not verify_password(user.password_hash, current):
        raise WrongPasswordError
    check_password(new, user.email)
    await user_repo.set_password(db, user, hash_password(new))


async def resolve_user(db: AsyncSession, token: str | None) -> User | None:
    if not token:
        return None
    claims = read_session(token)
    if claims is None:
        return None
    user = await user_repo.get_by_id(db, claims.user_id)
    if user is None or not user.is_active:
        return None
    if not secrets.compare_digest(claims.fingerprint, password_fingerprint(user.password_hash)):
        return None
    return user


async def optional_user(
    request: Request, db: Annotated[AsyncSession, Depends(get_db)]
) -> User | None:
    return await resolve_user(db, request.cookies.get(SESSION_COOKIE))


async def current_user(user: Annotated[User | None, Depends(optional_user)]) -> User:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Chưa đăng nhập.")
    return user
