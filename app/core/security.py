"""Chặn truy cập ở tầng app: chưa đăng nhập thì không vào được gì ngoài `/auth/*` + `/health`.

Chủ sở hữu: Q | Task: 12.4 | xem Task.md — tiêu chí **A8** (Plan.md mục 8)

**Mặc định là ĐÓNG, và đó là toàn bộ lý do file này tồn tại.** Cách hiển nhiên hơn — gắn
`Depends(current_user)` vào từng route — lại mặc định MỞ: route mới viết mà quên khai dependency
thì nó công khai, không ai thấy gì bất thường, và lỗi chỉ lộ ra khi có người thử. Ở đây một
middleware chặn **mọi** đường dẫn không nằm trong danh sách miễn, nên quên khai dependency chỉ
làm route thiếu đối tượng `User` (lỗi lập trình, thấy ngay lần chạy đầu) chứ không mở cửa dữ liệu.

Hai lớp, hai việc khác nhau:

1. `RequireLoginMiddleware` — **cổng vào**. Chưa đăng nhập: request HTML nhận `303` về
   `/auth/login?next=…`, request API nhận `401` JSON (Plan.md mục 4). Đăng nhập rồi thì nó đặt
   một *ảnh chụp* thông tin người dùng vào `request.state` để `base.html` in được tên và nút
   Đăng xuất mà không route nào phải truyền thêm biến (xem `template_context`).
2. `current_user` — **dependency lấy đối tượng `User`** cho route nào cần `user.id` để lọc dữ
   liệu (task 12.5). Nó chỉ ném `401`, không bao giờ `303`: lúc nó chạy thì middleware đã lọc
   xong, nên một request thiếu quyền tới được đây là chuyện bất thường của nội bộ, không phải
   người dùng cần được đưa tới trang đăng nhập.

Giá phải trả, nói thẳng: một request đã đăng nhập tốn **hai** câu truy vấn `users` — một ở
middleware (kiểm tài khoản còn sống, mật khẩu chưa đổi) và một ở dependency (lấy `User` gắn vào
đúng session của request). Đều là tra theo khoá chính, cỡ vài chục micro giây. Cách tránh là
chuyển object ORM từ middleware sang route, nhưng object đó thuộc một session đã đóng — hết hạn
lúc nào là hỏng lúc ấy, đúng loại lỗi `MissingGreenlet` đã mất công truy ở 7.3. Không đáng.

⚠️ **`/docs`, `/openapi.json`, `/redoc` cũng bị chặn** — chúng không nằm trong danh sách miễn của
A8. Muốn xem Swagger thì đăng nhập trước; nav trong `base.html` chỉ hiện với người đã đăng nhập
nên không có liên kết cụt nào.
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

#: Đường dẫn vào được khi **chưa** đăng nhập. Đúng danh sách của tiêu chí A8, không thêm gì:
#: `/health` (CD dùng làm smoke test ở 13.8), `/auth/*` (form đăng nhập/đăng ký), `/static/*`
#: (CSS của chính trang đăng nhập).
#:
#: ⚠️ `/account` **không** nằm ở đây dù `routers/auth.py` khai nó: nó là trang dữ liệu của một
#: người cụ thể, không phải cửa vào.
EXEMPT_PATHS: frozenset[str] = frozenset({"/health"})
EXEMPT_PREFIXES: tuple[str, ...] = ("/auth/", "/static/")

#: Khoá trong `request.state` giữ ảnh chụp người dùng. Dùng `scope["state"]` chứ không ContextVar
#: vì cùng lý do đã ghi ở `core/errors.py`: handler lỗi chạy ngoài phạm vi ContextVar.
STATE_KEY = "auth_user"

UNAUTHENTICATED_DETAIL = "Chưa đăng nhập. Gọi POST /auth/login để lấy cookie phiên."


@dataclass(frozen=True, slots=True)
class AuthUser:
    """Ảnh chụp **bất biến** của người đang đăng nhập, dùng để hiển thị.

    Cố ý không phải object ORM: nó đi kèm request qua nhiều tầng (template, handler lỗi) và một
    object ORM rời session sẽ hết hạn ở một chỗ không ai ngờ. Ở đây chỉ có ba chuỗi đã đọc sẵn.
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

    Nhờ nó `base.html` in được tên + nút Đăng xuất mà không một route HTML nào phải truyền thêm
    biến — kể cả trang lỗi (`error.html`) và các trang của T. Route quên truyền biến thì nav mất
    nút Đăng xuất một cách im lặng; một chỗ khai duy nhất thì không có gì để quên.
    """
    return {"current_user": auth_user(request)}


class RequireLoginMiddleware(BaseHTTPMiddleware):
    """Cổng vào: mọi đường dẫn không được miễn đều phải có phiên đăng nhập hợp lệ.

    Tự mở session DB riêng (`SessionLocal`) chứ không dùng dependency `get_db`: middleware chạy
    **trước** khi FastAPI giải dependency, nên ở đây chưa có session nào tồn tại.

    Không `raise HTTPException` mà trả response thẳng: ngoại lệ ném từ middleware **không** đi qua
    exception handler của `core/errors.py` (handler chỉ bọc phần router), nên nó sẽ thành 500.
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

    Đường nhanh dùng lại ảnh chụp của middleware (một lần tra khoá chính). Đường chậm — không có
    middleware — tự đọc cookie: nhờ vậy test dựng một app rỗng chỉ gắn một router (lối làm của
    `tests/test_auth.py`) vẫn xác thực đúng thay vì được miễn oan.
    """
    snapshot = auth_user(request)
    if snapshot is not None:
        user = await user_repo.get_by_id(db, snapshot.id)
        if user is not None and user.is_active:
            return user
        # Tài khoản bị xoá/khoá ngay giữa request: hiếm, nhưng để lọt là người vừa bị khoá vẫn
        # ghi được dữ liệu cho tới khi họ tự đóng tab.
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
    Chỉ xét `Accept` thì `fetch()` của chính UI ta (gửi `Accept: */*`) nhận về HTML và
    `response.json()` vỡ tại chỗ; chỉ xét đường dẫn thì `curl /cards/abc` nhận cả trang Tailwind.

    Nằm ở đây thay vì `core/errors.py` vì từ 12.4 có **hai** chỗ cần đúng một quyết định này:
    trang lỗi (errors.py) và cổng đăng nhập (middleware trên). Hai bản sao lệch nhau thì một
    trong hai sẽ trả sai kiểu nội dung cho đúng cùng một loại client.
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
    # hai và người dùng bị đưa về `/cards` trắng sau khi đăng nhập. `safe_next()` của T kiểm lại
    # giá trị này lúc chuyển hướng về, nên vòng đi–về không mở được open redirect.
    return RedirectResponse(
        f"/auth/login?next={quote(target, safe='/')}", status_code=status.HTTP_303_SEE_OTHER
    )
