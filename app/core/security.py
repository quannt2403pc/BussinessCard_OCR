"""Chặn truy cập ở tầng app: chưa đăng nhập thì không vào được gì ngoài `/auth/*` + `/health`.

Chủ sở hữu: Q | Task: 12.4 — tiêu chí **A8**

**Mặc định là ĐÓNG, và đó là toàn bộ lý do file này tồn tại.** Gắn `Depends(current_user)` vào
từng route lại mặc định MỞ: route mới mà quên khai dependency thì nó công khai và không ai thấy
gì bất thường. Ở đây quên khai dependency chỉ làm route thiếu đối tượng `User` — lỗi lập trình,
thấy ngay lần chạy đầu.

Hai lớp, hai việc khác nhau:

1. `RequireLoginMiddleware` — **cổng vào**. Chưa đăng nhập: request HTML nhận `303` về
   `/auth/login?next=…`, request API nhận `401` JSON. Đăng nhập rồi thì đặt một *ảnh chụp* thông
   tin người dùng vào `request.state`.
2. `current_user` — **dependency lấy đối tượng `User`**. Chỉ ném `401`, không bao giờ `303`.

Giá phải trả: một request đã đăng nhập tốn **hai** câu tra `users` theo khoá chính. Chuyển object
ORM từ middleware sang route thì nó thuộc một session đã đóng — đúng loại lỗi `MissingGreenlet`.

⚠️ **`/docs`, `/openapi.json`, `/redoc` cũng bị chặn** — chúng không nằm trong danh sách miễn.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.core.db import SessionLocal, get_db
from app.models.user import User
from app.repositories import user as user_repo
from app.services import auth

logger = logging.getLogger(__name__)

#: Đường dẫn vào được khi **chưa** đăng nhập — đúng danh sách của tiêu chí A8, không thêm gì.
#: ⚠️ `/account` **không** nằm ở đây: nó là trang dữ liệu của một người, không phải cửa vào.
EXEMPT_PATHS: frozenset[str] = frozenset({"/health"})
EXEMPT_PREFIXES: tuple[str, ...] = ("/auth/", "/static/")

#: Khoá trong `request.state` giữ ảnh chụp người dùng. Dùng `scope["state"]` chứ không ContextVar
#: vì handler lỗi chạy ngoài phạm vi ContextVar.
STATE_KEY = "auth_user"

UNAUTHENTICATED_DETAIL = "Chưa đăng nhập. Gọi POST /auth/login để lấy cookie phiên."


@dataclass(frozen=True, slots=True)
class AuthUser:
    """Ảnh chụp **bất biến** của người đang đăng nhập, dùng để hiển thị.

    Cố ý không phải object ORM: nó đi kèm request qua nhiều tầng và một object ORM rời session sẽ
    hết hạn ở một chỗ không ai ngờ.
    """

    id: uuid.UUID
    email: str
    display_name: str | None

    @property
    def label(self) -> str:
        """Tên để in lên nav — chưa đặt tên hiển thị thì dùng email."""
        return self.display_name or self.email


def is_exempt(path: str) -> bool:
    """Đường dẫn này có được vào khi chưa đăng nhập không?"""
    return path in EXEMPT_PATHS or path.startswith(EXEMPT_PREFIXES)


def auth_user(request: Request) -> AuthUser | None:
    """Ảnh chụp người dùng do middleware đặt, hoặc `None` khi chưa qua middleware."""
    state: dict[str, Any] = request.scope.get("state") or {}
    value = state.get(STATE_KEY)
    return value if isinstance(value, AuthUser) else None


def template_context(request: Request) -> dict[str, Any]:
    """Context processor của Jinja: đưa `current_user` vào **mọi** template.

    Nhờ nó `base.html` in được tên + nút Đăng xuất mà không route HTML nào phải truyền thêm biến.
    """
    return {"current_user": auth_user(request)}


class RequireLoginMiddleware(BaseHTTPMiddleware):
    """Cổng vào: mọi đường dẫn không được miễn đều phải có phiên đăng nhập hợp lệ.

    Tự mở session DB riêng chứ không dùng `get_db`: middleware chạy **trước** khi FastAPI giải
    dependency.

    Không `raise HTTPException` mà trả response thẳng: ngoại lệ ném từ middleware **không** đi
    qua exception handler của `core/errors.py` nên nó sẽ thành 500.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if is_exempt(request.url.path):
            return await call_next(request)

        async with SessionLocal() as db:
            user = await auth.resolve_user(db, request.cookies.get(auth.SESSION_COOKIE))
            snapshot = (
                AuthUser(id=user.id, email=user.email, display_name=user.display_name)
                if user is not None
                else None
            )

        if snapshot is None:
            return _unauthenticated(request)

        request.state.auth_user = snapshot
        return await call_next(request)


async def current_user(request: Request, db: Annotated[AsyncSession, Depends(get_db)]) -> User:
    """Dependency: `User` của người đang đăng nhập, gắn vào session của chính request này.

    Đường nhanh dùng lại ảnh chụp của middleware. Đường chậm — không có middleware — tự đọc
    cookie, nhờ vậy test dựng một app rỗng chỉ gắn một router vẫn xác thực đúng.
    """
    snapshot = auth_user(request)
    if snapshot is not None:
        user = await user_repo.get_by_id(db, snapshot.id)
        if user is not None and user.is_active:
            return user
        # Tài khoản bị xoá/khoá ngay giữa request: để lọt là người vừa bị khoá vẫn ghi được dữ
        # liệu cho tới khi họ tự đóng tab.
        logger.info("Phiên trỏ tới tài khoản không còn dùng được: %s", snapshot.id)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, UNAUTHENTICATED_DETAIL)

    user = await auth.resolve_user(db, request.cookies.get(auth.SESSION_COOKIE))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, UNAUTHENTICATED_DETAIL)
    return user


#: Kiểu dùng lại trong khai báo route: `user: CurrentUser`.
CurrentUser = Annotated[User, Depends(current_user)]


def wants_html(request: Request) -> bool:
    """Trả trang HTML hay JSON cho request này.

    Hai điều kiện **cùng lúc**, cố ý chặt: đường dẫn không phải `/api/...` *và* client nhận HTML.
    Chỉ xét `Accept` thì `fetch()` của chính UI ta nhận về HTML và `response.json()` vỡ tại chỗ;
    chỉ xét đường dẫn thì `curl /cards/abc` nhận cả trang Tailwind.

    Nằm ở đây vì có **hai** chỗ cần đúng một quyết định này: trang lỗi và cổng đăng nhập.
    """
    if request.url.path.startswith("/api/"):
        return False
    return "text/html" in request.headers.get("accept", "")


def _unauthenticated(request: Request) -> Response:
    """`303` về trang đăng nhập cho trình duyệt, `401` JSON cho API."""
    if not wants_html(request):
        return JSONResponse(
            {"detail": UNAUTHENTICATED_DETAIL}, status_code=status.HTTP_401_UNAUTHORIZED
        )

    target = request.url.path
    if request.url.query:
        target = f"{target}?{request.url.query}"
    # `quote` cả dấu `?`/`&` của query: không mã hoá thì `?next=/cards?q=a` bị cắt ở dấu `?` thứ
    # hai. `safe_next()` kiểm lại giá trị này lúc chuyển hướng về nên không mở được open redirect.
    return RedirectResponse(
        f"/auth/login?next={quote(target, safe='/')}", status_code=status.HTTP_303_SEE_OTHER
    )
