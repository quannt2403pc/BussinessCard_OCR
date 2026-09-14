"""Chuẩn hoá SĐT/email đa định dạng & đa quốc gia (hậu xử lý F1).

Chủ sở hữu: Q | Task: 4.10 | xem Task.md

Kiểm `app/services/normalize.py` (task 3.6). Toàn bộ là hàm thuần — không DB, không mạng, không
cần fixture của `conftest.py` (task 6.1) — nên chạy được ngay bằng `pytest` trần.

Hai thứ file này canh, vì đó là hai chỗ hỏng thật sự làm dữ liệu sai mà không ai thấy:

1. **Mã vùng suy từ `language_detected`.** `0912345678` là số Việt hay số Nhật hoàn toàn phụ
   thuộc trường này. Suy sai thì E.164 ra một số trông rất chuẩn nhưng gọi không được.
2. **"Không chuẩn hoá được" phải trả bản đã dọn, không trả `None`.** Nguyên tắc "không vứt dữ
   liệu" ở đầu `normalize.py`: người dùng còn phải review ở task 5.1, mất dữ liệu là mất hẳn.
"""

from __future__ import annotations

import pytest

from app.services import normalize

# --------------------------------------------------------------------------- squash_spaces


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Nguyễn Văn A  ", "Nguyễn Văn A"),
        ("Nguyễn\tVăn\nA", "Nguyễn Văn A"),
        # U+3000 (khoảng trắng full-width) lọt vào từ ảnh Nhật/Trung. `\s` của Python KHÔNG bắt
        # được ký tự này — quên xử lý tay thì tên công ty mang một ký tự lạ và mọi so khớp đều trượt.
        ("株式会社　さくら", "株式会社 さくら"),
        ("A\xa0B", "A B"),  # NBSP
        ("   ", None),
        ("", None),
        (None, None),
    ],
)
def test_squash_spaces(raw: str | None, expected: str | None) -> None:
    assert normalize.squash_spaces(raw) == expected


# --------------------------------------------------------------------------- email


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("A.Nguyen@ABC.VN", "a.nguyen@abc.vn"),
        ("  MAILTO:A.Nguyen@ABC.VN ", "a.nguyen@abc.vn"),
        ("Email: b@c.vn", "b@c.vn"),
        ("E-mail : b@c.vn", "b@c.vn"),
        ("<a@b.io>", "a@b.io"),
        ("a@b.io.", "a@b.io"),
        # Full-width `＠` và `．` trên danh thiếp Nhật: nhìn trên màn hình vẫn ra một địa chỉ
        # đúng, nhưng không gửi thư được. NFKC quy về ASCII.
        ("taro＠example．co．jp", "taro@example.co.jp"),
        # OCR đọc dấu chấm thành dấu phẩy — chỉ vá trong phần tên miền, nơi dấu phẩy chắc chắn sai.
        ("x@y,com", "x@y.com"),
        (None, None),
    ],
)
def test_normalize_email(raw: str | None, expected: str | None) -> None:
    assert normalize.normalize_email(raw) == expected


def test_chuoi_khong_co_do_cua_CardExtraction_chu_khong_phai_cua_file_nay() -> None:
    """`"N/A"` → `None` là việc của `CardExtraction` (`schemas/card.py`), không phải của đây.

    Ghi lại ranh giới này thành test vì nó rất dễ bị cài đặt hai lần: `normalize.py` chỉ làm
    sạch định dạng, còn dịch mọi cách model viết "không có" thành `None` nằm ở cửa khẩu dữ liệu
    phía trước. Trùng lặp thì sau này sửa danh sách marker ở một chỗ, chỗ kia vẫn chạy luật cũ.
    """
    assert normalize.normalize_email("N/A") == "n/a"


def test_email_sai_dinh_dang_van_duoc_giu_lai() -> None:
    """Không đúng dạng email thì **giữ bản đã dọn**, không trả `None`.

    Trả `None` ở đây là im lặng vứt đi thứ duy nhất người dùng có để sửa tay ở màn hình review.
    """
    assert normalize.normalize_email(" Khong-Phai-Email ") == "khong-phai-email"


# --------------------------------------------------------------------------- website


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("www.abc.vn", "https://www.abc.vn"),
        ("HTTP://Example.COM", "http://example.com"),
        ("abc.vn/", "https://abc.vn/"),
        # Hoa/thường của tên miền không quan trọng, của đường dẫn thì có: máy chủ Linux phân
        # biệt `/Partners` với `/partners`.
        ("HTTPS://ABC.VN/Partners", "https://abc.vn/Partners"),
        ("   ", None),
        (None, None),
    ],
)
def test_normalize_website(raw: str | None, expected: str | None) -> None:
    assert normalize.normalize_website(raw) == expected


# --------------------------------------------------------------------------- mã vùng


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("vi", "VN"),
        ("VI", "VN"),
        ("ko", "KR"),
        ("ja", "JP"),
        ("zh", "CN"),
        ("zh-TW", "TW"),
        ("zh_tw", "TW"),
        # Tiếng Anh không gắn với nước nào. Mặc định "US" sẽ đọc `0912345678` thành số Mỹ.
        ("en", None),
        ("xx", None),
        (None, None),
    ],
)
def test_region_for_language(language: str | None, expected: str | None) -> None:
    assert normalize.region_for_language(language) == expected


# --------------------------------------------------------------------------- SĐT: 5 nước


@pytest.mark.parametrize(
    ("raw", "language", "expected"),
    [
        # --- Việt Nam ---
        ("0912 345 678", "vi", "+84912345678"),
        ("(84) 24 7300 7300", "vi", "+842473007300"),
        # Mã nước không có dấu `+` — dạng rất hay gặp trên danh thiếp Việt.
        ("84 24 7300 7300", "vi", "+842473007300"),
        ("+84 28 3822 0000", "vi", "+842838220000"),
        ("ĐT: 024 7300 7300", "vi", "+842473007300"),
        # --- Nhật ---
        ("090-1234-5678", "ja", "+819012345678"),
        ("電話 03-1234-5678", "ja", "+81312345678"),
        # --- Hàn ---
        ("02-1234-5678", "ko", "+82212345678"),
        ("010 1234 5678", "ko", "+821012345678"),
        # --- Trung ---
        ("手机：138 0013 8000", "zh", "+8613800138000"),
        # --- Anh/quốc tế: không có vùng, chỉ đọc được số đã có dấu `+` ---
        ("+1 (415) 555-2671", "en", "+14155552671"),
    ],
)
def test_normalize_phone_5_nuoc(raw: str, language: str, expected: str) -> None:
    """Đúng năm ngôn ngữ trong phạm vi (Plan.md mục 1.3), mỗi nước một quy tắc số 0 đứng đầu."""
    region = normalize.region_for_language(language)
    assert normalize.normalize_phone(raw, region=region) == expected


def test_cung_mot_chuoi_doc_khac_nhau_theo_ngon_ngu() -> None:
    """Đây là lý do `language_detected` phải đi kèm: một chuỗi số, ba nước ba số hợp lệ khác nhau.

    Cả ba đều là số **hợp lệ** ở nước tương ứng, nên không có cách nào phát hiện ra mình suy sai
    mã vùng bằng cách nhìn kết quả — sai là sai im lặng.
    """
    assert normalize.normalize_phone("0312345678", region="VN") == "+84312345678"
    assert normalize.normalize_phone("0312345678", region="JP") == "+81312345678"
    assert normalize.normalize_phone("0312345678", region="KR") == "+82312345678"


def test_khong_co_vung_thi_khong_doan_bua() -> None:
    """Không biết vùng thì giữ nguyên bản đã dọn chứ không gán đại một mã nước."""
    assert normalize.normalize_phone("0912345678", region=None) == "0912345678"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Số không hợp lệ: giữ bản đã dọn để người dùng sửa, KHÔNG ép về E.164. Ép bừa thì màn
        # hình review hiện một số trông chuẩn mà gọi không được — lỗi âm thầm, tệ hơn hẳn.
        ("123", "123"),
        ("Tel:", None),
        ("khong phai so", None),
        (None, None),
    ],
)
def test_phone_khong_doc_duoc(raw: str | None, expected: str | None) -> None:
    assert normalize.normalize_phone(raw, region="VN") == expected


# --------------------------------------------------------------------------- tách nhiều số


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Tel: 024-7300-7300 / 0912345678", ["024-7300-7300", "0912345678"]),
        ("090-1234-5678; 03-1234-5678", ["090-1234-5678", "03-1234-5678"]),
        ("024 7300 7300\n0912345678", ["024 7300 7300", "0912345678"]),
        # Mảnh dưới 7 chữ số là số máy lẻ viết rời hoặc rác OCR — giữ lại sẽ sinh ra một
        # `phone_alt` vô nghĩa.
        ("024 7300 7300 / 102", ["024 7300 7300"]),
        ("", []),
        (None, []),
    ],
)
def test_split_phones(raw: str | None, expected: list[str]) -> None:
    assert normalize.split_phones(raw) == expected


# --------------------------------------------------------------------------- cả bộ trường


def test_normalize_card_fields_ca_bo() -> None:
    """Đúng lời gọi mà `services/ocr.py` thực hiện sau khi parse JSON (task 3.4)."""
    raw = {
        "full_name": " Lê  Văn B ",
        "job_title": "Giám đốc\tkinh doanh",
        "company_name_raw": "Công ty  TNHH ABC",
        "email": "L@ABC.VN",
        "phone": "Tel: 024 7300 7300 / 0912345678",
        "phone_alt": None,
        "website": "abc.vn",
        "address": "12 Lý\nThường Kiệt",
    }
    out = normalize.normalize_card_fields(raw, language="vi")

    assert out["full_name"] == "Lê Văn B"
    assert out["job_title"] == "Giám đốc kinh doanh"
    assert out["email"] == "l@abc.vn"
    assert out["website"] == "https://abc.vn"
    assert out["address"] == "12 Lý Thường Kiệt"
    # Số thứ hai trong ô `phone` lấp vào `phone_alt` — danh thiếp hay in "Tel / Mobile" cùng dòng.
    assert out["phone"] == "+842473007300"
    assert out["phone_alt"] == "+84912345678"

    # Không sửa tại chỗ: bản gốc còn phải ghi vào `ocr_raw_json` (task 3.5).
    assert raw["phone"] == "Tel: 024 7300 7300 / 0912345678"
    assert raw["full_name"] == " Lê  Văn B "


def test_khong_nhan_doi_so_trung_nhau() -> None:
    """Model chép cùng một số vào cả `phone` lẫn `phone_alt` là chuyện thường."""
    out = normalize.normalize_card_fields(
        {"phone": "0912 345 678", "phone_alt": "+84912345678"}, language="vi"
    )
    assert out["phone"] == "+84912345678"
    assert out["phone_alt"] is None


def test_khong_co_ngon_ngu_thi_khong_ep_ma_nuoc() -> None:
    out = normalize.normalize_card_fields({"phone": "0912 345 678"}, language=None)
    assert out["phone"] == "0912 345 678"


def test_chi_dung_toi_truong_duoc_gui() -> None:
    """Trường không có trong dict đầu vào thì không được tự mọc ra — trừ cặp `phone`/`phone_alt`
    vốn luôn được ghi lại thành một cặp (xem docstring `normalize_card_fields`)."""
    out = normalize.normalize_card_fields({"full_name": " A "}, language="vi")
    assert set(out) == {"full_name", "phone", "phone_alt"}
