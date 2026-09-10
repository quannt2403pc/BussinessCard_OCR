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
from fastapi.templating import Jinja2Templates

from app.core.config import settings
from app.routers import ROUTER_MODULES, iter_routers

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Chuẩn bị thư mục upload và ghi log những router đã sẵn sàng."""
    # uvicorn chỉ gắn handler cho logger của nó, log của app sẽ rơi vào hư không.
    # Cấu hình logging đầy đủ (request id, thời gian gọi LLM) là task 9.5.
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(message)s")

    settings.upload_dir.mkdir(parents=True, exist_ok=True)

    active = {name for name, _ in iter_routers()}
    pending = [name for name in ROUTER_MODULES if name not in active]
    logger.info("Router đã gắn: %s", ", ".join(sorted(active)) or "(chưa có)")
    if pending:
        logger.info("Router còn là stub: %s", ", ".join(pending))

    yield


app = FastAPI(
    title=settings.app_name,
    description="Số hoá danh thiếp & hồ sơ doanh nghiệp đối tác — bản demo localhost.",
    version="0.1.0",
    lifespan=lifespan,
)

# Router khai sẵn cho cả 7 module; module nào chưa có `router` thì bỏ qua (xem routers/__init__.py).
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
    """Trang chủ — hiện tại chỉ render khung `base.html` (task 1.3)."""
    return templates.TemplateResponse(request, "base.html", {"active_nav": "home"})
