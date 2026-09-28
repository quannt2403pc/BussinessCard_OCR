"""Logging có ngữ cảnh: request id, thời gian xử lý, thời gian gọi LLM + token.

Chủ sở hữu: Q | Task: 9.5

Một lượt quét đi qua `cards.py` → `image.py` → `ocr.py` → `llm.py` → CLIProxy, mỗi chặng một
logger. Hai người cùng bấm nút thì các dòng log đan xen và không có cách nào tách ra.
`request_id` là sợi chỉ đó: sinh một lần ở middleware, đi theo `ContextVar`, in ra ở **mọi** dòng.

Ba quyết định đáng nêu:

1. **Middleware ASGI thuần, không `BaseHTTPMiddleware`** — bản của Starlette bọc mỗi request
   trong một task riêng và `ContextVar` đặt trong đó không chảy ngược ra ngoài.
2. **`/health` và `/static/*` chỉ ghi ở mức DEBUG** — healthcheck gọi mỗi 10 giây, để INFO thì
   sau một đêm log chỉ còn healthcheck.
3. **Tắt `uvicorn.access`** — dòng của nó không có request id lẫn thời gian xử lý.
"""

from __future__ import annotations

import logging
import time
import uuid
from contextvars import ContextVar
from typing import Any

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

#: Header client gửi lên để nối log của họ với log của ta. Không có thì tự sinh.
REQUEST_ID_HEADER = "x-request-id"

#: Độ dài id tự sinh. 12 ký tự hex đủ phân biệt trong một phiên demo mà vẫn liếc mắt đọc được.
_ID_LENGTH = 12

#: Đường dẫn ồn: ghi ở DEBUG thay vì INFO (xem quyết định 2 ở đầu file).
_QUIET_PATHS = ("/health", "/static/")

_request_id: ContextVar[str] = ContextVar("request_id", default="")

logger = logging.getLogger("app.access")


def current_request_id() -> str:
    """Request id của lượt đang chạy, chuỗi rỗng nếu gọi ngoài request (script, test)."""
    return _request_id.get()


def new_request_id() -> str:
    return uuid.uuid4().hex[:_ID_LENGTH]


class RequestIdFilter(logging.Filter):
    """Gắn `request_id` vào mọi bản ghi log để formatter luôn in ra được.

    Là `Filter` chứ không phải formatter riêng: filter gắn được cho **handler**, tức phủ cả logger
    của thư viện (uvicorn, sqlalchemy, httpx).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get() or "-"
        return True


class RequestContextMiddleware:
    """Sinh request id, đo thời gian xử lý, trả id về client qua header `X-Request-ID`.

    Trả id ra header để khi người dùng báo "trang hỏng lúc 9h20" thì có đúng một chuỗi để `grep`.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_id(scope) or new_request_id()
        token = _request_id.set(request_id)
        # Chép thêm vào `scope["state"]` chứ không chỉ để trong `ContextVar`: ngoại lệ chưa ai bắt
        # được xử lý ở `ServerErrorMiddleware`, **bên ngoài** middleware này, tức sau khi `finally`
        # đã `reset()` mất ContextVar — và màn hình 500 lại đúng là chỗ người dùng cần đọc id.
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            path = scope.get("path", "")
            # `finally` chứ không phải sau lời gọi: ngoại lệ chưa ai bắt vẫn phải để lại một dòng.
            logger.log(
                logging.DEBUG if path.startswith(_QUIET_PATHS) else logging.INFO,
                "%s %s → %s · %.0fms",
                scope.get("method", "?"),
                path,
                status_code,
                elapsed_ms,
            )
            _request_id.reset(token)


def log_llm_call(
    model: str,
    elapsed_seconds: float,
    usage: dict[str, Any] | None,
    *,
    error: str | None = None,
) -> None:
    """Một dòng cho mỗi lời gọi LLM: model, thời gian, token vào/ra.

    Gọi từ `services/llm.py::generate_content()` — **điểm thắt duy nhất** mà cả F1, F2 và F3 đều
    đi qua.

    Token đọc từ `usageMetadata` và **có thể không có**. Thiếu thì in `—` chứ không bịa số 0 —
    0 token và "không biết bao nhiêu token" là hai chuyện khác nhau.
    """
    counts = usage or {}
    prompt = counts.get("promptTokenCount")
    output = counts.get("candidatesTokenCount")
    thoughts = counts.get("thoughtsTokenCount")
    total = counts.get("totalTokenCount")

    # `gemini-3-*` có bước "thinking" tính tiền riêng: token nghĩ có thể nhiều hơn token trả lời.
    thinking = f" · nghĩ {thoughts}" if thoughts else ""
    suffix = f" · LỖI: {error}" if error else ""
    logger.info(
        "LLM %s · %.2fs · token vào %s / ra %s%s / tổng %s%s",
        model,
        elapsed_seconds,
        _or_dash(prompt),
        _or_dash(output),
        thinking,
        _or_dash(total),
        suffix,
    )


def setup_logging(level: str = "INFO") -> None:
    """Cấu hình logger gốc. Gọi một lần lúc khởi động.

    Idempotent: gọi lại chỉ cập nhật mức log, không nhân đôi handler — `--reload` nạp lại module
    rất nhiều lần.
    """
    resolved = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(resolved)

    handler = _our_handler(root)
    if handler is None:
        handler = logging.StreamHandler()
        handler.set_name("bizcard")
        handler.addFilter(RequestIdFilter())
        root.addHandler(handler)
    handler.setLevel(resolved)
    handler.setFormatter(
        logging.Formatter(
            "%(levelname)-8s %(asctime)s [%(request_id)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )

    # uvicorn gắn handler riêng và **tắt propagate**, nên không đụng tới thì log của nó không đi
    # qua formatter ở trên (mất request id).
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    # Xem quyết định 3 ở đầu file: dòng access của ta thay hẳn dòng của uvicorn.
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False


# --------------------------------------------------------------------------- nội bộ


def _incoming_id(scope: Scope) -> str:
    """Đọc `X-Request-ID` client gửi lên, cắt bớt nếu dài và bỏ ký tự lạ.

    Không tin thẳng giá trị của client: chuỗi này đi vào từng dòng log, để nguyên thì ai gửi một
    header chứa xuống dòng là chèn được dòng log giả.
    """
    for raw_name, raw_value in scope.get("headers", []):
        if raw_name.decode("latin-1").lower() != REQUEST_ID_HEADER:
            continue
        value = raw_value.decode("latin-1").strip()
        cleaned = "".join(ch for ch in value if ch.isalnum() or ch in "-_")[:64]
        return cleaned
    return ""


def _our_handler(root: logging.Logger) -> logging.StreamHandler | None:  # type: ignore[type-arg]
    for handler in root.handlers:
        if handler.get_name() == "bizcard" and isinstance(handler, logging.StreamHandler):
            return handler
    return None


def _or_dash(value: object) -> str:
    return "—" if value is None else str(value)
