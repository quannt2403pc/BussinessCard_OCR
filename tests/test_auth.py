import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Literal

import httpx
import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from itsdangerous import TimestampSigner, URLSafeTimedSerializer
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models.user import User
from app.routers import auth as auth_router
from app.schemas.user import WeakPasswordError, check_password, normalize_email
from app.services import auth

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
PASSWORD = "matkhau123"
NEW_PASSWORD = "matkhaumoi456"


@pytest.fixture(autouse=True)
def fast_hasher(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(auth, "hasher", PasswordHasher(time_cost=1, memory_cost=8, parallelism=1))
    auth.dummy_hash.cache_clear()
    yield
    auth.dummy_hash.cache_clear()


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(auth_router.router)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def register(
    client: httpx.AsyncClient,
    email: str = "an@example.com",
    password: str = PASSWORD,
    confirm: str | None = None,
    next_url: str = "/",
) -> httpx.Response:
    return await client.post(
        "/auth/register",
        data={
            "email": email,
            "password": password,
            "password_confirm": password if confirm is None else confirm,
            "display_name": "  Nguyễn   Văn An ",
            "next": next_url,
        },
    )


async def login(
    client: httpx.AsyncClient, email: str, password: str, next_url: str = "/"
) -> httpx.Response:
    return await client.post(
        "/auth/login", data={"email": email, "password": password, "next": next_url}
    )


async def count_users(db: AsyncSession) -> int:
    return await db.scalar(select(func.count()).select_from(User)) or 0


@pytest.mark.parametrize(
    "password",
    ["abc123", "chiconchu", "12345678901", "x" * 129 + "1"],
)
def test_weak_passwords_are_rejected(password: str) -> None:
    with pytest.raises(WeakPasswordError):
        check_password(password, "an@example.com")


def test_password_same_as_email_is_rejected() -> None:
    with pytest.raises(WeakPasswordError):
        check_password("an1@example.com", "AN1@example.com")


def test_valid_password_passes() -> None:
    assert check_password(PASSWORD, "an@example.com") == PASSWORD


def test_email_is_normalized() -> None:
    assert normalize_email("  An@Example.COM ") == "an@example.com"
    with pytest.raises(ValueError):
        normalize_email("khong-phai-email")


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("/companies?page=2", "/companies?page=2"),
        (None, "/"),
        ("", "/"),
        ("https://evil.example", "/"),
        ("//evil.example", "/"),
        ("/\\evil.example", "/"),
        ("/auth/logout", "/"),
    ],
)
def test_safe_next_blocks_open_redirect(target: str | None, expected: str) -> None:
    assert auth.safe_next(target) == expected


def test_session_token_round_trip_and_tampering() -> None:
    user = User(email="an@example.com", password_hash=auth.hash_password(PASSWORD))
    user.id = uuid.uuid4()
    token = auth.sign_session(user)
    claims = auth.read_session(token)
    assert claims is not None and claims.user_id == user.id
    assert auth.read_session(token[:-2] + ("AA" if token[-2:] != "AA" else "BB")) is None
    assert auth.read_session("rac") is None
    foreign = URLSafeTimedSerializer("khoa-khac", salt=auth.SESSION_SALT).dumps(
        {"uid": str(user.id), "pv": "x"}
    )
    assert auth.read_session(foreign) is None


def test_session_token_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    user = User(email="an@example.com", password_hash=auth.hash_password(PASSWORD))
    user.id = uuid.uuid4()
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: 1_000_000)
    token = auth.sign_session(user)
    max_age = int(auth.session_max_age().total_seconds())
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: 1_000_000 + max_age - 1)
    assert auth.read_session(token) is not None
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: 1_000_000 + max_age + 1)
    assert auth.read_session(token) is None


async def test_register_signs_in_with_hardened_cookie(
    db_session: AsyncSession, client: httpx.AsyncClient
) -> None:
    response = await register(client, email="An@Example.com", next_url="/companies")
    assert response.status_code == 303
    assert response.headers["location"] == "/companies"
    cookie = response.headers["set-cookie"].lower()
    assert auth.SESSION_COOKIE in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert f"max-age={7 * 24 * 3600}" in cookie

    me = await client.get("/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "an@example.com"
    assert body["display_name"] == "Nguyễn Văn An"
    assert body["last_login_at"] is not None

    stored = await db_session.scalar(select(User).where(User.email == "an@example.com"))
    assert stored is not None
    assert stored.password_hash.startswith("$argon2")
    assert PASSWORD not in stored.password_hash


async def test_register_duplicate_email_case_insensitive(
    db_session: AsyncSession, client: httpx.AsyncClient
) -> None:
    await register(client, email="an@example.com")
    client.cookies.clear()
    response = await register(client, email="  AN@example.COM ")
    assert response.status_code == 409
    assert "đã được đăng ký" in response.text
    assert auth.SESSION_COOKIE not in response.headers.get("set-cookie", "")
    assert await count_users(db_session) == 1


@pytest.mark.parametrize(
    ("password", "confirm", "fragment"),
    [
        ("abc12", None, "ít nhất 8 ký tự"),
        ("chiconchuthoi", None, "cả chữ lẫn số"),
        (PASSWORD, "khac123456", "không khớp"),
    ],
)
async def test_register_rejects_bad_passwords(
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    password: str,
    confirm: str | None,
    fragment: str,
) -> None:
    response = await register(client, password=password, confirm=confirm)
    assert response.status_code == 400
    assert fragment in response.text
    assert await count_users(db_session) == 0


async def test_register_rejects_invalid_email(
    db_session: AsyncSession, client: httpx.AsyncClient
) -> None:
    response = await register(client, email="khong-phai-email")
    assert response.status_code == 400
    assert "Email không hợp lệ" in response.text
    assert await count_users(db_session) == 0


async def test_wrong_email_and_wrong_password_look_the_same(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await register(client)
    client.cookies.clear()
    calls: list[str | bytes] = []

    class CountingHasher(PasswordHasher):
        def verify(self, hash: str | bytes, password: str | bytes) -> Literal[True]:
            calls.append(hash)
            return super().verify(hash, password)

    counting = CountingHasher(time_cost=1, memory_cost=8, parallelism=1)
    dummy = auth.dummy_hash()
    monkeypatch.setattr(auth, "hasher", counting)

    wrong_password = await login(client, "an@example.com", "sai-mat-khau-1")
    unknown_email = await login(client, "khong-co@example.com", "sai-mat-khau-1")
    malformed_email = await login(client, "khong-phai-email", "sai-mat-khau-1")

    for response in (wrong_password, unknown_email, malformed_email):
        assert response.status_code == 401
        assert auth_router.LOGIN_FAILED in response.text
        assert "set-cookie" not in response.headers
    assert len(calls) == 3
    assert calls[1] == calls[2] == dummy


async def test_login_redirects_to_safe_next_only(client: httpx.AsyncClient) -> None:
    await register(client)
    client.cookies.clear()
    good = await login(client, "AN@example.com", PASSWORD, next_url="/cards?page=2")
    assert good.status_code == 303
    assert good.headers["location"] == "/cards?page=2"
    client.cookies.clear()
    evil = await login(client, "an@example.com", PASSWORD, next_url="//evil.example/x")
    assert evil.headers["location"] == "/"


async def test_login_page_redirects_when_already_signed_in(client: httpx.AsyncClient) -> None:
    await register(client)
    response = await client.get("/auth/login", params={"next": "/companies"})
    assert response.status_code == 303
    assert response.headers["location"] == "/companies"


async def test_logout_clears_session(client: httpx.AsyncClient) -> None:
    await register(client)
    assert (await client.get("/auth/me")).status_code == 200
    response = await client.post("/auth/logout")
    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"
    assert (await client.get("/auth/me")).status_code == 401


async def test_forged_and_missing_cookies_are_rejected(client: httpx.AsyncClient) -> None:
    assert (await client.get("/auth/me")).status_code == 401
    client.cookies.set(auth.SESSION_COOKIE, "gia-mao.abc.def")
    assert (await client.get("/auth/me")).status_code == 401


async def test_inactive_user_cannot_sign_in_or_keep_session(
    db_session: AsyncSession, client: httpx.AsyncClient
) -> None:
    await register(client)
    await db_session.execute(update(User).values(is_active=False))
    await db_session.flush()
    assert (await client.get("/auth/me")).status_code == 401
    client.cookies.clear()
    assert (await login(client, "an@example.com", PASSWORD)).status_code == 401


async def test_bootstrap_account_cannot_sign_in(
    db_session: AsyncSession, client: httpx.AsyncClient
) -> None:
    db_session.add(
        User(
            email="owner@bizcard.local",
            password_hash="!khong-dang-nhap-duoc-cho-toi-khi-dat-lai-mat-khau",
        )
    )
    await db_session.flush()
    response = await login(client, "owner@bizcard.local", "!khong-dang-nhap-duoc")
    assert response.status_code == 401


async def test_account_page_requires_login(client: httpx.AsyncClient) -> None:
    response = await client.get("/account")
    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login?next=/account"


async def test_account_page_shows_user(client: httpx.AsyncClient) -> None:
    await register(client)
    response = await client.get("/account")
    assert response.status_code == 200
    assert "an@example.com" in response.text
    assert "Nguyễn Văn An" in response.text


async def test_update_display_name(db_session: AsyncSession, client: httpx.AsyncClient) -> None:
    await register(client)
    response = await client.post("/account/profile", data={"display_name": "  Anh   An "})
    assert response.status_code == 303
    assert response.headers["location"] == "/account?saved=profile"
    assert (await client.get("/auth/me")).json()["display_name"] == "Anh An"
    page = await client.get("/account", params={"saved": "profile"})
    assert "Đã lưu tên hiển thị" in page.text

    cleared = await client.post("/account/profile", data={"display_name": "   "})
    assert cleared.status_code == 303
    assert (await client.get("/auth/me")).json()["display_name"] is None

    too_long = await client.post("/account/profile", data={"display_name": "x" * 121})
    assert too_long.status_code == 400


async def test_change_password_revokes_other_sessions(client: httpx.AsyncClient) -> None:
    await register(client)
    old_cookie = client.cookies.get(auth.SESSION_COOKIE)
    assert old_cookie

    response = await client.post(
        "/account/password",
        data={
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
            "new_password_confirm": NEW_PASSWORD,
        },
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/account?saved=password"
    assert (await client.get("/auth/me")).status_code == 200

    client.cookies.set(auth.SESSION_COOKIE, old_cookie)
    assert (await client.get("/auth/me")).status_code == 401

    client.cookies.clear()
    assert (await login(client, "an@example.com", PASSWORD)).status_code == 401
    assert (await login(client, "an@example.com", NEW_PASSWORD)).status_code == 303


@pytest.mark.parametrize(
    ("current", "new", "confirm", "fragment"),
    [
        ("sai-mat-khau-9", NEW_PASSWORD, NEW_PASSWORD, "hiện tại không đúng"),
        (PASSWORD, "ngan1", "ngan1", "ít nhất 8 ký tự"),
        (PASSWORD, NEW_PASSWORD, "khac987654", "không khớp"),
    ],
)
async def test_change_password_errors(
    client: httpx.AsyncClient, current: str, new: str, confirm: str, fragment: str
) -> None:
    await register(client)
    response = await client.post(
        "/account/password",
        data={"current_password": current, "new_password": new, "new_password_confirm": confirm},
    )
    assert response.status_code == 400
    assert fragment in response.text
    assert (await client.get("/auth/me")).status_code == 200
