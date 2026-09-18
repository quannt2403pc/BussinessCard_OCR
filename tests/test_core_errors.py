"""Test xử lý lỗi toàn cục (9.4) + middleware request id / log LLM (9.5).

Chủ sở hữu: Q | Task: 9.4, 9.5 | xem Task.md

Không dùng `app.main.app` cho phần lỗi nghiệp vụ: muốn kiểm handler thì phải có route **cố ý
ném ra** từng loại ngoại lệ, mà thêm route vào app thật là đổi hành vi của cả ứng dụng chỉ để
chiều một bài test. Thay vào đó dựng một app nhỏ **ghép y hệt cách `main.py` ghép** (middleware
→ handler → static mount), nên vẫn là đường đi thật chứ không phải một bản mô phỏng.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from app.core.errors import register_exception_handlers
from app.core.logging import (
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
    RequestIdFilter,
    current_request_id,
    log_llm_call,
)
from app.services.embeddings import EmbedderUnavailableError
from app.services.llm import LLMError, LLMNotConnectedError

# `asyncio_mode = "auto"` trong pyproject.toml đã lo phần async — không khai `pytestmark` ở đây,
# nếu không 4 test đồng bộ bên dưới sẽ bị đánh dấu asyncio và pytest cảnh báo từng cái một.

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

#: `Accept` của trình duyệt thật. Quyết định HTML-hay-JSON đọc đúng header này.
BROWSER = {"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


def build_app() -> FastAPI:
    """App ghép đúng thứ tự của `main.py`, kèm vài route chỉ để ném lỗi."""
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/boom/oauth")
    async def _oauth() -> None:
        raise LLMNotConnectedError(
            "Chưa kết nối OAuth: CLIProxy không có credential nào. "
            "Vào /settings bấm 'Kết nối CLIProxy (OAuth)'."
        )

    @app.get("/boom/llm")
    async def _llm() -> None:
        raise LLMError("CLIProxy từ chối lời gọi model: hỏng gì đó")

    @app.get("/boom/embedder")
    async def _embedder() -> None:
        raise EmbedderUnavailableError("embedder không phản hồi")

    @app.get("/boom/unexpected")
    async def _unexpected() -> None:
        raise RuntimeError("chuỗi bí mật: postgresql://bizcard:change-me@db:5432/bizcard")

    @app.get("/api/boom/unexpected")
    async def _api_unexpected() -> None:
        raise RuntimeError("chuỗi bí mật: postgresql://bizcard:change-me@db:5432/bizcard")

    @app.get("/api/boom/http")
    async def _api_http() -> None:
        raise HTTPException(404, detail="not found")

    @app.get("/api/boom/oauth")
    async def _api_oauth() -> None:
        raise LLMNotConnectedError("Chưa kết nối OAuth. Vào /settings bấm nút.")

    @app.get("/page/{card_id}")
    async def _page(card_id: int) -> dict[str, int]:
        return {"id": card_id}

    return app


def client(*, raise_app_exceptions: bool = True) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=build_app(), raise_app_exceptions=raise_app_exceptions)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


# --------------------------------------------------------------------------- 9.5 request id


async def test_moi_response_deu_co_request_id():
    async with client() as http:
        response = await http.get("/page/7")
    assert response.status_code == 200
    assert len(response.headers[REQUEST_ID_HEADER]) == 12


async def test_hai_request_khac_nhau_thi_id_khac_nhau():
    async with client() as http:
        first = await http.get("/page/1")
        second = await http.get("/page/2")
    assert first.headers[REQUEST_ID_HEADER] != second.headers[REQUEST_ID_HEADER]


async def test_giu_lai_request_id_client_gui_len():
    """Client đã có id của mình thì dùng lại — nối được log hai bên."""
    async with client() as http:
        response = await http.get("/page/1", headers={REQUEST_ID_HEADER: "abc-123_XYZ"})
    assert response.headers[REQUEST_ID_HEADER] == "abc-123_XYZ"


async def test_request_id_cua_client_bi_loc_ky_tu_la():
    """Chuỗi này đi thẳng vào từng dòng log — để nguyên là chèn được dòng log giả."""
    async with client() as http:
        response = await http.get("/page/1", headers={REQUEST_ID_HEADER: "abc\tINFO gia mao def"})
    value = response.headers[REQUEST_ID_HEADER]
    assert " " not in value and "\t" not in value
    assert value == "abcINFOgiamaodef"


async def test_ngoai_request_thi_khong_co_request_id():
    assert current_request_id() == ""


def test_filter_luon_gan_request_id_vao_ban_ghi():
    """Không có filter thì formatter có `%(request_id)s` sẽ ném `KeyError` ở mọi dòng log."""
    record = logging.LogRecord("x", logging.INFO, "f", 1, "msg", None, None)
    assert RequestIdFilter().filter(record) is True
    assert record.request_id == "-"


# --------------------------------------------------------------------------- 9.5 log LLM


def test_log_llm_ghi_du_token(caplog):
    with caplog.at_level(logging.INFO, logger="app.access"):
        log_llm_call(
            "gemini-3-flash",
            2.5,
            {
                "promptTokenCount": 1200,
                "candidatesTokenCount": 300,
                "thoughtsTokenCount": 450,
                "totalTokenCount": 1950,
            },
        )
    line = caplog.text
    assert "gemini-3-flash" in line and "2.50s" in line
    assert "1200" in line and "300" in line and "1950" in line
    # Token "nghĩ" tách riêng: với gemini-3 nó có thể nhiều hơn cả token trả lời (task 8.6).
    assert "nghĩ 450" in line


def test_log_llm_khong_bia_so_khi_thieu_usage(caplog):
    """CLIProxy chuyển tiếp nguyên response của provider, `usageMetadata` có thể vắng."""
    with caplog.at_level(logging.INFO, logger="app.access"):
        log_llm_call("gemini-3-flash", 0.1, None)
    assert "—" in caplog.text
    assert "0" not in caplog.text.split("token")[1]


def test_log_llm_ghi_ca_luot_hong(caplog):
    with caplog.at_level(logging.INFO, logger="app.access"):
        log_llm_call("gemini-3-flash", 120.0, None, error="CliProxyUnavailableError")
    assert "120.00s" in caplog.text
    assert "CliProxyUnavailableError" in caplog.text


# --------------------------------------------------------------------------- 9.4 HTML vs JSON


async def test_api_nhan_json_giu_nguyen_hop_dong():
    """`{"detail": …}` là hình dạng UI và test khác đang đọc — không được đổi."""
    async with client() as http:
        response = await http.get("/api/boom/http", headers=BROWSER)
    assert response.status_code == 404
    assert response.json() == {"detail": "not found"}


async def test_trang_html_nhan_trang_loi_co_nav():
    async with client() as http:
        response = await http.get("/khong-ton-tai", headers=BROWSER)
    assert response.status_code == 404
    assert "text/html" in response.headers["content-type"]
    assert "Không tìm thấy" in response.text
    # Trang lỗi kế thừa base.html → vẫn còn nav để đi tiếp, không phải ngõ cụt.
    assert "BusinessCard" in response.text


async def test_fetch_cua_chinh_ui_van_nhan_json():
    """`fetch()` gửi `Accept: */*`. Trả HTML cho nó là `response.json()` vỡ tại chỗ."""
    async with client() as http:
        response = await http.get("/khong-ton-tai", headers={"accept": "*/*"})
    assert response.status_code == 404
    assert response.json()["detail"]


async def test_request_id_co_trong_ca_response_loi():
    async with client() as http:
        response = await http.get("/khong-ton-tai", headers=BROWSER)
    assert response.headers[REQUEST_ID_HEADER] in response.text


# --------------------------------------------------------------------------- 9.4 lỗi nghiệp vụ


async def test_chua_ket_noi_oauth_ra_503_va_man_hinh_rieng():
    async with client() as http:
        response = await http.get("/boom/oauth", headers=BROWSER)
    assert response.status_code == 503
    assert "Chưa kết nối CLIProxy" in response.text
    assert 'href="/settings"' in response.text


async def test_chua_ket_noi_oauth_qua_api_ra_503_json():
    async with client() as http:
        response = await http.get("/api/boom/oauth", headers=BROWSER)
    assert response.status_code == 503
    assert "/settings" in response.json()["detail"]


async def test_loi_llm_khac_ra_502_khong_phai_man_hinh_oauth():
    async with client() as http:
        response = await http.get("/boom/llm", headers=BROWSER)
    assert response.status_code == 502
    assert "Chưa kết nối CLIProxy" not in response.text


async def test_embedder_chet_ra_503():
    async with client() as http:
        response = await http.get("/boom/embedder", headers=BROWSER)
    assert response.status_code == 503


async def test_validation_cua_api_giu_hinh_dang_mac_dinh_fastapi():
    """UI dựa vào danh sách lỗi từng trường để tô đúng ô nhập sai."""
    async with client() as http:
        response = await http.get("/page/khong-phai-so", headers={"accept": "*/*"})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


async def test_validation_tren_trinh_duyet_ra_trang_loi():
    async with client() as http:
        response = await http.get("/page/khong-phai-so", headers=BROWSER)
    assert response.status_code == 422
    assert "text/html" in response.headers["content-type"]


# --------------------------------------------------------------------------- 9.4 lỗi 500


async def test_loi_khong_luong_truoc_khong_ro_noi_dung_ngoai_lê():
    """Thông điệp lỗi Python hay chứa đường dẫn file, câu SQL, có khi cả chuỗi kết nối."""
    async with client(raise_app_exceptions=False) as http:
        response = await http.get("/boom/unexpected", headers=BROWSER)
    assert response.status_code == 500
    assert "postgresql://" not in response.text
    assert "change-me" not in response.text
    assert "Lỗi không mong đợi" in response.text


async def test_loi_500_qua_api_cung_khong_ro_gi():
    async with client(raise_app_exceptions=False) as http:
        response = await http.get("/api/boom/unexpected", headers={"accept": "*/*"})
    assert response.status_code == 500
    assert "postgresql://" not in response.text


async def test_trang_500_van_in_ma_tra_cuu():
    """Người dùng đọc mã này để báo lỗi; nó phải khớp `request_id` trong log của cùng lượt.

    Đây là ca duy nhất `request_id` KHÔNG đọc được từ `ContextVar`: handler chạy ở
    `ServerErrorMiddleware`, ngoài phạm vi middleware, sau khi ContextVar đã `reset()`.
    """
    async with client(raise_app_exceptions=False) as http:
        response = await http.get("/boom/unexpected", headers=BROWSER)
    assert "Mã tra cứu" in response.text
    assert "<code" in response.text
    assert ">-<" not in response.text  # "-" nghĩa là không lấy được id
