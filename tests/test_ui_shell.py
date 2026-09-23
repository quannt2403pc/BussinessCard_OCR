"""Khung giao diện dùng chung: header, bộ nhận diện, bong bóng chat, sprite icon.

Chủ sở hữu: Q | Task: 14.5, 14.7, 14.10 (D14) | xem Task.md

Vì sao có file này: ba thứ của D14 đều **không** có test nào chạm tới nếu không viết riêng —
chúng nằm trong `base.html`, tức là thứ mọi test khác đi qua mà không ai khẳng định gì về nó.
Ba lỗi cụ thể mà bộ test dưới đây bắt được:

1. Xoá nhầm `logo-mark-64.webp` / `favicon.ico` khi dọn `static/` → tab trình duyệt trống trơn,
   không một test nào đỏ.
2. Gắn bong bóng chat lên **chính** trang `/assistant` → hai thể hiện trợ lý tranh nhau
   `sessionStorage`, và lỗi chỉ lộ ra khi có người mở đúng trang đó rồi gõ câu hỏi.
3. Gõ sai tên icon (`icon("trashh")`) → `<use>` trỏ vào một `#id` không tồn tại, trình duyệt vẽ
   ô trống **không báo lỗi console**. Đây là lỗi im lặng đúng nghĩa.
"""

import re
from pathlib import Path

import pytest

from app.core.templates import BASE_DIR, TEMPLATES_DIR
from app.models.user import User

pytestmark = pytest.mark.anyio

STATIC_DIR: Path = BASE_DIR / "static"

#: File của **Q** đã quét ở task 14.8. `templates/companies/`, `templates/auth/`, `home.html` là
#: phần của T ở 14.9 — cố ý không đưa vào đây để test không đỏ vì việc của người khác.
Q_TEMPLATES: tuple[Path, ...] = (
    TEMPLATES_DIR / "base.html",
    TEMPLATES_DIR / "_macros.html",
    TEMPLATES_DIR / "_assistant_widget.html",
    TEMPLATES_DIR / "assistant.html",
    TEMPLATES_DIR / "settings.html",
    TEMPLATES_DIR / "error.html",
    *sorted((TEMPLATES_DIR / "cards").glob("*.html")),
)


# --------------------------------------------------------------------------- bộ nhận diện


def test_bo_nhan_dien_co_du_file() -> None:
    """Task 14.3/14.7: thiếu file nào thì header, tab trình duyệt hoặc thẻ chia sẻ link vỡ."""
    for name in (
        "img/LogoOCRXimi.png",
        "img/logo-mark-64.webp",
        "img/logo-mark-128.webp",
        "img/logo-full.webp",
        "img/favicon.ico",
        "img/apple-touch-icon.png",
        "img/og-image.png",
        "img/icons.svg",
        "js/assistant.js",
        "js/toast.js",
        "css/app.css",
    ):
        assert (STATIC_DIR / name).is_file(), f"thiếu static/{name}"


async def test_head_co_favicon_va_the_chia_se(app_client, user_a: User) -> None:
    async with app_client(user_a) as http:
        html = (await http.get("/")).text

    assert 'rel="icon"' in html and "favicon.ico" in html
    assert "apple-touch-icon" in html
    assert 'property="og:image"' in html and "og-image.png" in html
    # Tên đọc được, có dấu: trình đọc màn hình gặp "OCRXÌMI" sẽ đánh vần từng chữ cái.
    assert 'content="OCR Xì Mi"' in html
    assert 'alt="OCR Xì Mi"' in html


async def test_ten_san_pham_khong_con_la_ten_repo(app_client, user_a: User) -> None:
    """Task 14.7: `BusinessCard_OCR` từ nay chỉ còn là tên repo, không phải tên trên giao diện."""
    async with app_client(user_a) as http:
        html = (await http.get("/")).text

    assert "OCR Xì Mi" in html
    assert "BusinessCard OCR" not in html


# --------------------------------------------------------------------------- nav & trang chủ


async def test_nav_con_4_muc(app_client, user_a: User) -> None:
    """QĐ-2 của D14: `Bảng số liệu` gộp vào trang chủ, nav còn 4 mục.

    Trước D14 nav có 5 mục + 4 liên kết bên phải = 9 mục một hàng, tràn ngang ở 375px.
    """
    async with app_client(user_a) as http:
        html = (await http.get("/cards")).text

    for label in ("Danh thiếp", "Doanh nghiệp", "Trợ lý AI", "Cài đặt"):
        assert label in html
    assert "Bảng số liệu" not in html
    # Bốn thứ từng nằm rời trên nav nay gom vào một menu người dùng.
    assert 'aria-label="Menu tài khoản' in html
    assert 'aria-label="Menu điều hướng"' in html


async def test_trang_chu_khong_con_khoi_mac_dinh_cua_d1(app_client, user_a: User) -> None:
    """Task 14.7: đây chính là màn hình đầu tiên khách vào `ocrximi.io.vn` nhìn thấy."""
    async with app_client(user_a) as http:
        response = await http.get("/")

    assert response.status_code == 200
    assert "Khung dự án đã dựng xong" not in response.text
    assert "Danh thiếp gần đây" in response.text
    assert 'id="total-cards"' in response.text


# --------------------------------------------------------------------------- bong bóng chat


async def test_bong_bong_chat_co_tren_moi_trang(app_client, user_a: User) -> None:
    async with app_client(user_a) as http:
        for path in ("/", "/cards", "/companies", "/settings"):
            html = (await http.get(path)).text
            assert 'id="assistant-widget"' in html, path
            assert 'role="dialog"' in html, path


async def test_bong_bong_chat_vang_mat_o_trang_assistant(app_client, user_a: User) -> None:
    """Hai thể hiện trợ lý trên cùng một màn hình sẽ tranh nhau `sessionStorage` (task 14.5)."""
    async with app_client(user_a) as http:
        html = (await http.get("/assistant")).text

    assert 'id="assistant-widget"' not in html
    # …nhưng trang riêng vẫn phải còn nguyên: docs/demo-runbook.md và link `?session=` trỏ vào nó.
    assert 'id="assistant-root"' in html


async def test_khach_chua_dang_nhap_khong_thay_bong_bong(app_client) -> None:
    """Bong bóng gọi `/api/chat` — hiện nó cho khách là mời bấm vào một đường chắc chắn 401."""
    async with app_client() as http:
        html = (await http.get("/auth/login")).text

    assert 'id="assistant-widget"' not in html


# --------------------------------------------------------------------------- icon & màu


def test_moi_icon_duoc_goi_deu_co_trong_sprite() -> None:
    """Gõ sai tên icon là lỗi **im lặng**: `<use>` trỏ vào `#id` rỗng, console không báo gì."""
    sprite = (STATIC_DIR / "img" / "icons.svg").read_text(encoding="utf-8")
    available = set(re.findall(r'<symbol id="i-([a-z-]+)"', sprite))
    assert available, "không đọc được symbol nào trong icons.svg"

    used: set[str] = set()
    for path in TEMPLATES_DIR.rglob("*.html"):
        used |= set(re.findall(r'icon\("([a-z-]+)"', path.read_text(encoding="utf-8")))

    assert used, "không template nào gọi icon() — macro 14.2 chưa được dùng?"
    assert used <= available, f"icon không có trong sprite: {sorted(used - available)}"


def test_file_cua_q_khong_con_mau_sky() -> None:
    """Task 14.8: đổi tông là việc cơ học, nhưng sót một chỗ thì nó lạc màu giữa trang."""
    for path in Q_TEMPLATES:
        assert "sky-" not in path.read_text(encoding="utf-8"), path.name


def test_o_thong_bao_cua_q_deu_doc_duoc_bang_trinh_doc_man_hinh() -> None:
    """Task 14.8: `<div id="message">` trần là ô mà trình đọc màn hình **không** biết đã đổi.

    Đây là thứ quan trọng nhất trên trang với người dùng khiếm thị — kết quả upload/quét/lưu
    đều chỉ báo ở đúng ô này.
    """
    for path in Q_TEMPLATES:
        html = path.read_text(encoding="utf-8")
        for match in re.finditer(r'<div id="(message|dlg-message)"(.*?)>', html, re.S):
            assert "aria-live" in match.group(2), (
                f"{path.name}: ô #{match.group(1)} thiếu aria-live"
            )
