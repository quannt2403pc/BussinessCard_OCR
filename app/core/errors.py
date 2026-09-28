"""Xử lý lỗi toàn cục: trang lỗi thân thiện cho người dùng, JSON giữ nguyên hợp đồng cho API.

Chủ sở hữu: Q | Task: 9.4

Ba việc:

1. **Một chỗ quyết định HTML hay JSON**, dựa trên đường dẫn + `Accept`, chứ không để mỗi router
   tự nhớ.
2. **Lỗi nghiệp vụ không lọt ra ngoài dưới dạng 500.** Router tự bắt ở những chỗ đã nghĩ tới,
   nhưng "những chỗ đã nghĩ tới" là danh sách không ai bảo trì được. Bảng ánh xạ ở đây khai một
   lần cho toàn ứng dụng; router nào đã tự bắt thì vẫn thắng.
3. **Màn hình "chưa kết nối OAuth" riêng** — lỗi hay gặp nhất của bản demo, người dùng cần đúng
   một nút đi tới `/settings` chứ không phải một đoạn traceback.

⚠️ **`request_id` đọc từ `scope["state"]`, không từ `ContextVar`**: với ngoại lệ chưa ai bắt,
handler chạy ở `ServerErrorMiddleware` — ngoài `RequestContextMiddleware`, tức sau khi ContextVar
đã bị `reset()`.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.status import (
    HTTP_400_BAD_REQUEST,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_413_CONTENT_TOO_LARGE,
    HTTP_422_UNPROCESSABLE_CONTENT,
    HTTP_429_TOO_MANY_REQUESTS,
    HTTP_500_INTERNAL_SERVER_ERROR,
    HTTP_502_BAD_GATEWAY,
    HTTP_503_SERVICE_UNAVAILABLE,
)

from app.core.logging import REQUEST_ID_HEADER, current_request_id
from app.core.security import wants_html
from app.core.templates import templates
from app.services.cliproxy_client import CliProxyError, CliProxyUnavailableError
from app.services.embeddings import EmbedderUnavailableError, EmbeddingError
from app.services.llm import (
    LLMBlockedError,
    LLMError,
    LLMInvalidModelError,
    LLMNotConnectedError,
)

logger = logging.getLogger(__name__)

#: Tiêu đề tiếng Việt cho từng mã HTTP. Thiếu mã nào thì rơi vào `_FALLBACK_TITLE`.
_TITLES: dict[int, str] = {
    HTTP_400_BAD_REQUEST: "Yêu cầu không hợp lệ",
    HTTP_403_FORBIDDEN: "Không có quyền truy cập",
    HTTP_404_NOT_FOUND: "Không tìm thấy",
    HTTP_409_CONFLICT: "Đang có việc khác chạy dở",
    HTTP_413_CONTENT_TOO_LARGE: "Tệp quá lớn",
    HTTP_422_UNPROCESSABLE_CONTENT: "Dữ liệu gửi lên không hợp lệ",
    HTTP_429_TOO_MANY_REQUESTS: "Quá nhiều yêu cầu",
    HTTP_500_INTERNAL_SERVER_ERROR: "Lỗi không mong đợi",
    HTTP_502_BAD_GATEWAY: "Dịch vụ AI trả lời không hợp lệ",
    HTTP_503_SERVICE_UNAVAILABLE: "Dịch vụ chưa sẵn sàng",
}
_FALLBACK_TITLE = "Đã có lỗi xảy ra"

#: Gợi ý việc-cần-làm theo mã. Một trang lỗi không nói được bước tiếp theo thì chỉ là chỗ cụt.
_HINTS: dict[int, str] = {
    HTTP_404_NOT_FOUND: "Đường dẫn không tồn tại, hoặc bản ghi đã bị xoá.",
    HTTP_409_CONFLICT: "Chờ việc đang chạy kết thúc rồi thử lại.",
    HTTP_413_CONTENT_TOO_LARGE: "Chọn ảnh nhẹ hơn rồi tải lên lại.",
    HTTP_429_TOO_MANY_REQUESTS: "Đợi một lát rồi thử lại.",
    HTTP_500_INTERNAL_SERVER_ERROR: (
        "Lỗi nằm ở phía hệ thống, không phải do thao tác của bạn. "
        "Gửi mã tra cứu bên dưới cho người phụ trách để tra log."
    ),
    HTTP_502_BAD_GATEWAY: "Thử lại sau ít phút; nếu vẫn vậy thì kiểm tra CLIProxy ở /settings.",
    HTTP_503_SERVICE_UNAVAILABLE: "Kiểm tra các dịch vụ phụ trợ rồi thử lại.",
}

#: Lỗi nghiệp vụ → mã HTTP, dùng khi router **không** tự bắt.
#: Thứ tự quan trọng: lớp con đứng trước lớp cha (Starlette chọn handler theo MRO).
_DOMAIN_STATUS: tuple[tuple[type[Exception], int], ...] = (
    (LLMNotConnectedError, HTTP_503_SERVICE_UNAVAILABLE),
    (LLMInvalidModelError, HTTP_503_SERVICE_UNAVAILABLE),
    (LLMBlockedError, HTTP_502_BAD_GATEWAY),
    (LLMError, HTTP_502_BAD_GATEWAY),
    (EmbedderUnavailableError, HTTP_503_SERVICE_UNAVAILABLE),
    (EmbeddingError, HTTP_502_BAD_GATEWAY),
    (CliProxyUnavailableError, HTTP_503_SERVICE_UNAVAILABLE),
    (CliProxyError, HTTP_502_BAD_GATEWAY),
)


def register_exception_handlers(app: FastAPI) -> None:
    """Gắn toàn bộ handler vào app. Gọi một lần trong `app/main.py`."""
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    for exc_type, status_code in _DOMAIN_STATUS:
        app.add_exception_handler(exc_type, _domain_handler(status_code))
    # `Exception` do `ServerErrorMiddleware` gọi; nó gửi response của ta rồi **ném lại** ngoại lệ
    # để server vẫn ghi traceback — đừng cố "nuốt" cho sạch log.
    app.add_exception_handler(Exception, _handle_unexpected)


def request_id_of(request: Request) -> str:
    """Mã tra cứu in lên trang lỗi và trả về trong header."""
    state: dict[str, Any] = request.scope.get("state") or {}
    return str(state.get("request_id") or current_request_id() or "-")


# --------------------------------------------------------------------------- handler


async def _handle_http_exception(request: Request, exc: Exception) -> Response:
    """`HTTPException` do chính router ném ra — giữ nguyên mã và nội dung, chỉ đổi cách trình bày."""
    assert isinstance(exc, StarletteHTTPException)
    detail = exc.detail if isinstance(exc.detail, str) else "Yêu cầu không xử lý được."
    if _wants_html(request):
        return _html_error(request, exc.status_code, detail, headers=exc.headers)
    # Giữ **đúng** hình dạng mặc định của FastAPI: UI và test đang đọc `{"detail": …}`.
    return _json_error(request, exc.status_code, detail, headers=exc.headers)


async def _handle_validation_error(request: Request, exc: Exception) -> Response:
    """422 — tham số URL hoặc body sai kiểu."""
    assert isinstance(exc, RequestValidationError)
    if _wants_html(request):
        return _html_error(
            request,
            HTTP_422_UNPROCESSABLE_CONTENT,
            "Tham số trên đường dẫn không đúng định dạng — kiểm tra lại liên kết vừa bấm.",
        )
    # Trình duyệt thì ra trang; API giữ nguyên bản mặc định của FastAPI (danh sách lỗi từng
    # trường), vì UI dựa vào đó để tô đúng ô nhập sai.
    return await request_validation_exception_handler(request, exc)


def _domain_handler(status_code: int) -> Callable[[Request, Exception], Awaitable[Response]]:
    """Sinh handler cho một lỗi nghiệp vụ. Ghi log ở mức WARNING, không phải ERROR.

    Những lỗi này là **tình huống đã lường trước**; để ERROR hết thì mức ERROR mất nghĩa.
    """

    async def handler(request: Request, exc: Exception) -> Response:
        logger.warning("%s: %s", type(exc).__name__, exc)
        message = str(exc) or _TITLES.get(status_code, _FALLBACK_TITLE)
        if _wants_html(request):
            return _html_error(request, status_code, message)
        return _json_error(request, status_code, message)

    return handler


async def _handle_unexpected(request: Request, exc: Exception) -> Response:
    """Lỗi không lường trước: log đầy đủ traceback, trả ra ngoài đúng một câu + mã tra cứu.

    **Không** đưa `str(exc)` cho người dùng: thông điệp của lỗi Python hay chứa đường dẫn file,
    câu SQL, có khi cả chuỗi kết nối. Hai đầu nối với nhau bằng `request_id`.
    """
    logger.exception("Lỗi chưa bắt được ở %s %s", request.method, request.url.path)
    message = _HINTS[HTTP_500_INTERNAL_SERVER_ERROR]
    if _wants_html(request):
        return _html_error(request, HTTP_500_INTERNAL_SERVER_ERROR, message)
    return _json_error(request, HTTP_500_INTERNAL_SERVER_ERROR, message)


# --------------------------------------------------------------------------- dựng response


def _wants_html(request: Request) -> bool:
    """Trả trang HTML hay JSON — logic nằm ở `core/security.py::wants_html`.

    Chuyển sang đó vì cổng đăng nhập cần **đúng** quyết định này; giữ tên cũ để phần còn lại của
    file không phải đổi.
    """
    return wants_html(request)


def _html_error(
    request: Request,
    status_code: int,
    message: str,
    *,
    headers: Mapping[str, str] | None = None,
) -> Response:
    request_id = request_id_of(request)
    response = templates.TemplateResponse(
        request,
        "error.html",
        {
            "status_code": status_code,
            "title": _TITLES.get(status_code, _FALLBACK_TITLE),
            "message": message,
            "hint": _HINTS.get(status_code),
            "request_id": request_id,
            # Màn hình riêng cho ca hay gặp nhất của demo. Nhận diện bằng chính câu mà
            # `services/llm.py` sinh ra, nên cả lỗi router tự ánh xạ lẫn lỗi lọt xuống đều khớp.
            "show_oauth": "/settings" in message,
            "active_nav": None,
        },
        status_code=status_code,
        headers={**(headers or {}), REQUEST_ID_HEADER: request_id},
    )
    return response


def _json_error(
    request: Request,
    status_code: int,
    detail: str,
    *,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    request_id = request_id_of(request)
    return JSONResponse(
        {"detail": detail},
        status_code=status_code,
        headers={**(headers or {}), REQUEST_ID_HEADER: request_id},
    )
