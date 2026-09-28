"""Khung giao diện dùng chung: header, bộ nhận diện, bong bóng chat, sprite icon.

Chủ sở hữu: Q | Task: 14.5, 14.7, 14.10, EX-08/EX-09/EX-11

Ba thứ này nằm trong `base.html` — thứ mọi test khác đi qua mà không ai khẳng định gì về nó. Ba
lỗi im lặng mà bộ test dưới đây bắt được:

1. Xoá nhầm logo / favicon khi dọn `static/` → tab trình duyệt trống trơn.
2. Gỡ trang `/assistant` mà quên `301`, hoặc quên kéo bộ lọc phạm vi và chế độ phóng to sang
   bong bóng → link đã chia sẻ hoá 404 và người dùng mất hai tính năng.
3. Gõ sai tên icon → `<use>` trỏ vào một `#id` không tồn tại, trình duyệt vẽ ô trống **không báo
   lỗi console**.
"""

import re
import uuid
from pathlib import Path

import pytest

from app.core.templates import BASE_DIR, TEMPLATES_DIR
from app.models.user import User

pytestmark = pytest.mark.anyio

STATIC_DIR: Path = BASE_DIR / "static"

#: File của **Q**. Phần của T cố ý không đưa vào đây để test không đỏ vì việc của người khác.
Q_TEMPLATES: tuple[Path, ...] = (
    TEMPLATES_DIR / "base.html",
    TEMPLATES_DIR / "_macros.html",
    TEMPLATES_DIR / "_assistant_widget.html",
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


async def test_nav_con_3_muc(app_client, user_a: User) -> None:
    """Nav rút còn 3 mục: `Bảng số liệu` gộp vào trang chủ, `Trợ lý AI` thành bong bóng.

    Khẳng định bằng `href` chứ không bằng nhãn: chữ "Trợ lý AI" vẫn còn trên trang ở tiêu đề
    panel bong bóng, nên tìm theo nhãn là test xanh trong khi nav vẫn còn nguyên mục cũ.
    """
    async with app_client(user_a) as http:
        html = (await http.get("/cards")).text

    for label in ("Danh thiếp", "Doanh nghiệp", "Cài đặt"):
        assert label in html
    assert 'href="/assistant"' not in html
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


async def test_bong_bong_giu_du_hai_thu_keo_tu_trang_cu(app_client, user_a: User) -> None:
    """EX-08/EX-09: gỡ trang riêng mà bỏ quên hai thứ này là **cắt tính năng**, không phải dọn.

    1. Bộ lọc phạm vi — thiếu nó thì không còn đường hỏi "chỉ trong danh thiếp".
    2. Nút phóng to — panel 380×560 không đủ đọc câu trả lời dài kèm danh sách trích dẫn.
    """
    async with app_client(user_a) as http:
        html = (await http.get("/")).text

    assert 'data-a="scope"' in html
    assert 'value="company_profile"' in html
    assert 'data-w="wide"' in html
    assert 'aria-label="Phóng to"' in html


async def test_duong_dan_assistant_cu_chuyen_huong_ve_trang_chu(app_client, user_a: User) -> None:
    """EX-09: trang riêng đã gỡ, nhưng URL cũ **không được** thành 404.

    Tài liệu demo và hai dòng gợi ý của `scripts/seed.py` đều trỏ vào `/assistant`.
    """
    async with app_client(user_a) as http:
        response = await http.get("/assistant")

    assert response.status_code == 301
    assert response.headers["location"] == "/"


async def test_link_chia_se_hoi_thoai_van_mo_dung_hoi_thoai(app_client, user_a: User) -> None:
    """`?session=` là đường chia sẻ hội thoại — nó phải sống sót qua EX-09.

    Bong bóng đọc `?chat=` rồi mở đúng phiên đó, nên chuỗi tham số phải đi trọn từ URL cũ sang
    URL mới chứ không rơi mất ở bước chuyển hướng.
    """
    session_id = uuid.uuid4()
    async with app_client(user_a) as http:
        response = await http.get(f"/assistant?session={session_id}")

    assert response.status_code == 301
    assert response.headers["location"] == f"/?chat={session_id}"


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


def test_sprite_co_du_cap_icon_cua_nut_phong_to() -> None:
    """Nút phóng to đổi icon **bằng JS**, nên test icon ở trên không nhìn thấy hai tên này.

    Gõ sai tên ở đó vẫn là lỗi im lặng y hệt, chỉ khác là không regex nào bắt được.
    """
    sprite = (STATIC_DIR / "img" / "icons.svg").read_text(encoding="utf-8")
    for name in ("i-expand", "i-collapse"):
        assert f'<symbol id="{name}"' in sprite, name


def test_sprite_co_du_icon_trang_thai_cua_man_hinh_quet() -> None:
    """Bốn badge của *Tiến trình quét* dựng tên icon trong JS, ngoài tầm regex `icon("…")`.

    Gõ sai tên là `<use>` trỏ vào id rỗng — không lỗi, không console, chỉ là badge mất icon.
    """
    sprite = (STATIC_DIR / "img" / "icons.svg").read_text(encoding="utf-8")
    for name in ("i-clock", "i-spinner", "i-check-circle", "i-error"):
        assert f'<symbol id="{name}"' in sprite, name


def test_khong_dung_emoji_lam_icon() -> None:
    """`docs/ui-kit.md` chốt **một bộ icon duy nhất** (Lucide qua sprite).

    Emoji là bộ thứ hai lẻn vào: font hệ thống vẽ nên cùng một trạng thái ra ba hình khác nhau
    trên Windows / macOS / Android, không nhận `currentColor`, và trình đọc màn hình đọc ✅ thành
    "dấu kiểm màu trắng đậm" ngay trước chữ "xong".

    Bỏ qua chú thích Jinja và JS: emoji trong đó không hiện lên màn hình. Cách lọc này có thể bỏ
    sót nhưng không báo nhầm — đúng chiều an toàn cho một test chặn hồi quy.
    """
    emoji = ("⏳", "🔄", "✅", "❌", "🟢", "🔴", "⭐", "🎉", "👍", "📄", "🔍")
    for path in sorted(TEMPLATES_DIR.rglob("*.html")):
        text = re.sub(r"\{#.*?#\}", "", path.read_text(encoding="utf-8"), flags=re.S)
        text = re.sub(r"//.*", "", text)
        found = [e for e in emoji if e in text]
        assert not found, f"{path.name}: dùng emoji làm icon {found} — xem docs/ui-kit.md mục 7"


def test_file_cua_q_khong_con_mau_sky() -> None:
    """Task 14.8: đổi tông là việc cơ học, nhưng sót một chỗ thì nó lạc màu giữa trang."""
    for path in Q_TEMPLATES:
        assert "sky-" not in path.read_text(encoding="utf-8"), path.name


def test_o_thong_bao_cua_q_deu_doc_duoc_bang_trinh_doc_man_hinh() -> None:
    """`<div id="message">` trần là ô mà trình đọc màn hình **không** biết đã đổi.

    Đây là thứ quan trọng nhất trên trang với người dùng khiếm thị — mọi kết quả đều báo ở đó.
    """
    for path in Q_TEMPLATES:
        html = path.read_text(encoding="utf-8")
        for match in re.finditer(r'<div id="(message|dlg-message)"(.*?)>', html, re.S):
            assert "aria-live" in match.group(2), (
                f"{path.name}: ô #{match.group(1)} thiếu aria-live"
            )


# --------------------------------------------------------------- hàng đợi chịu mạng yếu (NEXT-09)


async def test_batch_giu_anh_tren_may_truoc_khi_gui(app_client, user_a: User) -> None:
    """Trang quét phải khai đủ bộ đồ nghề của `NEXT-09`.

    Hành vi lúc mất sóng chỉ chạy được trong trình duyệt thật; ca này giữ **hợp đồng template**:
    đổi tên id hay bỏ khối hàng đợi thì đoạn JavaScript kia hỏng im lặng — trang vẫn mở được, ảnh
    vẫn chọn được, chỉ là không còn gì giữ chúng lại khi rớt mạng.
    """
    async with app_client(user_a) as http:
        html = (await http.get("/cards/upload")).text

    for marker in ('id="offline-banner"', 'id="queue-box"', 'id="queue-list"', 'id="btn-retry"'):
        assert marker in html, marker
    assert (
        "indexedDB.open" in html
    )  # hàng đợi phải nằm ở chỗ giữ được Blob, không phải localStorage
    assert 'addEventListener("online"' in html


async def test_batch_noi_ro_anh_duoc_giu_tren_may(app_client, user_a: User) -> None:
    """Lời hứa với người dùng phải hiện ngay trên trang, không nằm trong mã nguồn."""
    async with app_client(user_a) as http:
        html = (await http.get("/cards/upload")).text

    assert "giữ trên máy trước" in html
    assert "tự gửi tiếp khi có mạng" in html


async def test_mot_man_hinh_quet_duy_nhat_va_duong_cu_van_mo_duoc(app_client, user_a: User) -> None:
    """`I-38`: `/cards/upload` nhận nhiều ảnh, và `/cards/batch` chuyển hướng về đó.

    Bỏ `multiple` là im lặng quay về đúng cái phiền mà `I-38` gỡ. Còn `/cards/batch` nằm trong
    tài liệu hướng dẫn nên nó phải mở ra một thứ gì đó chứ không phải 404.
    """
    async with app_client(user_a) as http:
        html = (await http.get("/cards/upload")).text
        moved = await http.get("/cards/batch")

    assert 'id="input-file" type="file" accept="image/*" multiple' in html
    # Nút chụp ảnh của màn hình cũ phải sống sót qua lần gộp: đó là đường của người đứng ở hội
    # chợ, chính là cảnh `NEXT-09` sinh ra để phục vụ.
    assert 'capture="environment"' in html

    assert moved.status_code == 301
    assert moved.headers["location"] == "/cards/upload"
