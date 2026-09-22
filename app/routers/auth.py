from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.templates import templates
from app.models.user import User
from app.repositories import user as user_repo
from app.repositories.user import EmailTakenError
from app.schemas.user import (
    PASSWORD_MIN_LENGTH,
    UserOut,
    WeakPasswordError,
    clean_display_name,
)
from app.services import auth
from app.services.auth import optional_user

router = APIRouter(tags=["auth"])

LOGIN_FAILED = "Email hoặc mật khẩu không đúng."
SAVED_MESSAGES = {
    "profile": "Đã lưu tên hiển thị.",
    "password": "Đã đổi mật khẩu. Các phiên đăng nhập khác đã bị đăng xuất.",
}

Session = Annotated[AsyncSession, Depends(get_db)]
MaybeUser = Annotated[User | None, Depends(optional_user)]


def _render(
    request: Request, name: str, context: dict[str, Any], status_code: int = 200
) -> HTMLResponse:
    context = {"password_min_length": PASSWORD_MIN_LENGTH, **context}
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


def _signed_in(user: User, target: str) -> RedirectResponse:
    response = _redirect(target)
    auth.set_session_cookie(response, user)
    return response


@router.get("/auth/login", response_class=HTMLResponse)
async def login_page(request: Request, user: MaybeUser, next: str | None = None) -> Response:
    if user is not None:
        return _redirect(auth.safe_next(next))
    return _render(request, "auth/login.html", {"next": auth.safe_next(next), "email": ""})


@router.post("/auth/login", response_class=HTMLResponse)
async def login(
    request: Request,
    db: Session,
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/",
) -> Response:
    user = await auth.authenticate(db, email, password)
    if user is None:
        return _render(
            request,
            "auth/login.html",
            {"next": auth.safe_next(next), "email": email.strip(), "error": LOGIN_FAILED},
            status.HTTP_401_UNAUTHORIZED,
        )
    return _signed_in(user, auth.safe_next(next))


@router.get("/auth/register", response_class=HTMLResponse)
async def register_page(request: Request, user: MaybeUser, next: str | None = None) -> Response:
    if user is not None:
        return _redirect(auth.safe_next(next))
    return _render(
        request,
        "auth/register.html",
        {"next": auth.safe_next(next), "email": "", "display_name": ""},
    )


@router.post("/auth/register", response_class=HTMLResponse)
async def register(
    request: Request,
    db: Session,
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    password_confirm: Annotated[str, Form()] = "",
    display_name: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/",
) -> Response:
    context = {"next": auth.safe_next(next), "email": email.strip(), "display_name": display_name}
    if password != password_confirm:
        context["error"] = "Hai lần nhập mật khẩu không khớp."
        return _render(request, "auth/register.html", context, status.HTTP_400_BAD_REQUEST)
    try:
        user = await auth.register(
            db, email=email, password=password, display_name=display_name or None
        )
    except EmailTakenError:
        context["error"] = "Email này đã được đăng ký. Hãy đăng nhập."
        return _render(request, "auth/register.html", context, status.HTTP_409_CONFLICT)
    except ValueError as exc:
        context["error"] = str(exc)
        return _render(request, "auth/register.html", context, status.HTTP_400_BAD_REQUEST)
    await user_repo.record_login(db, user)
    return _signed_in(user, auth.safe_next(next))


@router.post("/auth/logout")
async def logout() -> Response:
    response = _redirect("/auth/login")
    auth.clear_session_cookie(response)
    return response


@router.get("/auth/me", response_model=UserOut)
async def me(user: Annotated[User, Depends(auth.current_user)]) -> User:
    return user


@router.get("/account", response_class=HTMLResponse)
async def account_page(request: Request, user: MaybeUser, saved: str | None = None) -> Response:
    if user is None:
        return _redirect(f"/auth/login?next={quote('/account')}")
    return _render(
        request,
        "auth/account.html",
        {"user": user, "notice": SAVED_MESSAGES.get(saved or "")},
    )


@router.post("/account/profile", response_class=HTMLResponse)
async def update_profile(
    request: Request,
    db: Session,
    user: MaybeUser,
    display_name: Annotated[str, Form()] = "",
) -> Response:
    if user is None:
        return _redirect(f"/auth/login?next={quote('/account')}")
    try:
        name = clean_display_name(display_name)
    except ValueError as exc:
        return _render(
            request,
            "auth/account.html",
            {"user": user, "profile_error": str(exc)},
            status.HTTP_400_BAD_REQUEST,
        )
    await user_repo.set_display_name(db, user, name)
    return _redirect("/account?saved=profile")


@router.post("/account/password", response_class=HTMLResponse)
async def update_password(
    request: Request,
    db: Session,
    user: MaybeUser,
    current_password: Annotated[str, Form()] = "",
    new_password: Annotated[str, Form()] = "",
    new_password_confirm: Annotated[str, Form()] = "",
) -> Response:
    if user is None:
        return _redirect(f"/auth/login?next={quote('/account')}")
    error: str | None = None
    if new_password != new_password_confirm:
        error = "Hai lần nhập mật khẩu mới không khớp."
    else:
        try:
            await auth.change_password(db, user, current_password, new_password)
        except auth.WrongPasswordError:
            error = "Mật khẩu hiện tại không đúng."
        except WeakPasswordError as exc:
            error = str(exc)
    if error is not None:
        return _render(
            request,
            "auth/account.html",
            {"user": user, "password_error": error},
            status.HTTP_400_BAD_REQUEST,
        )
    return _signed_in(user, "/account?saved=password")
