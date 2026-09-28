"""Điểm vào FastAPI. Khai báo sẵn TOÀN BỘ router stub từ D1 để về sau không ai phải sửa file này.

Chủ sở hữu: Q | Task: 1.2 | xem Task.md
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.logging import RequestContextMiddleware, setup_logging
from app.core.security import RequireLoginMiddleware
from app.core.templates import templates
from app.routers import ROUTER_MODULES, iter_routers

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Chuẩn bị thư mục upload và ghi log những router đã sẵn sàng."""
    # Cấu hình logging ở đây chứ không ở cấp module: `lifespan` **không chạy** khi test gọi app
    # qua `httpx.ASGITransport`.
    setup_logging(settings.log_level)

    settings.upload_dir.mkdir(parents=True, exist_ok=True)

    active = {name for name, _ in iter_routers()}
    pending = [name for name in ROUTER_MODULES if name not in active]
    logger.info("Router đã gắn: %s", ", ".join(sorted(active)) or "(chưa có)")
    if pending:
        logger.info("Router còn là stub: %s", ", ".join(pending))

    yield


# Swagger chỉ sống trên máy dev (I-43). `/docs`, `/redoc`, `/openapi.json` vốn đã nằm sau cổng
# đăng nhập, nhưng trên `ocrximi.io.vn` thì ai đăng ký cũng là "đã đăng nhập", mà `/openapi.json`
# mô tả **toàn bộ bề mặt API**.
#
# ⚠️ `openapi_url=None` phải đi kèm: để lại mỗi `/openapi.json` thì `/docs` chỉ là trang rỗng còn
# toàn bộ lược đồ vẫn tải về được.
_docs_enabled = settings.docs_enabled

app = FastAPI(
    title=settings.app_name,
    description="Số hoá danh thiếp & hồ sơ doanh nghiệp đối tác — bản demo localhost.",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

# Cổng đăng nhập: chặn mọi đường dẫn ngoài `/auth/*`, `/static/*`, `/health`.
#
# ⚠️ **Thứ tự hai dòng dưới đây là một phần của thiết kế, đừng đảo.** `add_middleware` chèn vào
# đầu danh sách nên dòng thêm sau cùng nằm ngoài cùng: `RequestContextMiddleware` phải bọc ngoài
# cổng đăng nhập, nếu không mọi lượt chặn biến mất khỏi log.
app.add_middleware(RequireLoginMiddleware)

# Request id + đo thời gian xử lý. Thêm trước khi gắn router để mọi request — kể cả request bị
# từ chối bằng 404 — đều có một dòng log và một `X-Request-ID` trả về.
app.add_middleware(RequestContextMiddleware)

# Xử lý lỗi toàn cục: HTML cho người dùng, JSON cho API, cùng một chỗ quyết định.
register_exception_handlers(app)

# Router khai sẵn cho cả 7 module; module nào chưa có `router` thì bỏ qua.
for _name, _router in iter_routers():
    app.include_router(_router)

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    """Healthcheck của container `api` trong docker-compose. Tiêu chí hoàn thành D1."""
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse, tags=["ui"])
async def home(request: Request) -> HTMLResponse:
    """Trang chủ: số liệu, việc đang chờ duyệt và 3 hành động chính.

    Số liệu **không** lấy ở đây mà do trang tự gọi `GET /api/stats`: route này không có session DB
    (nó ở `main.py`, ngoài mọi router), và nhét truy vấn vào đây là mở lại đúng cái vòng import mà
    `core/templates.py` sinh ra để tránh.
    """
    return templates.TemplateResponse(request, "home.html", {"active_nav": "home"})
