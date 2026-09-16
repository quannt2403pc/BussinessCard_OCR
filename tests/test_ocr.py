"""Test pipeline OCR: đọc câu trả lời của model, thử lại, quyết định trạng thái.

Chủ sở hữu: Q | Task: 6.1 | xem Task.md

Trọng tâm là **những cách model trả lời sai mà vẫn HTTP 200** — đó là loại lỗi đã gặp thật khi
chạy vision ở task 2.3/3.4, và là loại mà mock ở tầng HTTP (fixture `cliproxy`) mới kiểm được.
Chất lượng trích xuất trên ảnh thật là việc của `docs/accuracy.md` (task 10.4), không phải ở đây.
"""

from __future__ import annotations

import json

import pytest

from app.models.card import CardStatus
from app.schemas.card import CardExtraction
from app.services import ocr
from app.services.llm import LLMBlockedError, LLMError
from app.services.ocr import OcrParseError, OcrResult, parse_response, status_and_notes

#: Ảnh giả: `services/llm.py` chỉ kiểm rỗng/kiểu MIME, không mở ảnh ra xem.
FAKE_JPEG = b"\xff\xd8\xff\xe0 fake jpeg"

CARD_JSON = {
    "full_name": "Nguyễn Văn A",
    "job_title": "Giám đốc kinh doanh",
    "company_name_raw": "Công ty TNHH ABC",
    "email": "A.Nguyen@ABC.VN",
    "phone": "0912 345 678",
    "address": "123 Lê Lợi, Quận 1, TP.HCM",
    "website": "abc.vn",
    "language_detected": "vi",
    "confidence": {"full_name": 0.98, "phone": 0.7},
}


def dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


# --------------------------------------------------------------------------- đọc JSON


def test_parse_json_thuan():
    card = parse_response(dumps(CARD_JSON))

    assert card.full_name == "Nguyễn Văn A"
    assert card.company_name_raw == "Công ty TNHH ABC"
    # Hậu xử lý 3.6 chạy ngay trong `parse_response`: email hạ chữ thường, SĐT về E.164.
    assert card.email == "a.nguyen@abc.vn"
    assert card.phone == "+84912345678"
    assert card.website == "https://abc.vn"


def test_parse_json_boc_trong_hang_rao_code():
    """I-15: `gemini-3-flash` bọc JSON trong ```json dù prompt cấm — gặp thật ở task 2.3."""
    card = parse_response(f"```json\n{dumps(CARD_JSON)}\n```")

    assert card.full_name == "Nguyễn Văn A"


def test_parse_json_co_loi_dan_phia_truoc():
    card = parse_response(f"Đây là thông tin tôi đọc được:\n{dumps(CARD_JSON)}\nHy vọng giúp ích.")

    assert card.company_name_raw == "Công ty TNHH ABC"


def test_parse_json_du_dau_phay_cuoi():
    card = parse_response('{"full_name": "Kim Min-jun", "job_title": "CTO",}')

    assert card.full_name == "Kim Min-jun"


def test_parse_json_boc_trong_mang_mot_phan_tu():
    card = parse_response(f"[{dumps(CARD_JSON)}]")

    assert card.full_name == "Nguyễn Văn A"


def test_parse_json_khoa_lech_duoc_keo_ve_dung_truong():
    """Model đổi tên khoá theo hứng — `_ALIASES` kéo về, không để rớt trường."""
    card = parse_response(
        '{"name": "Tanaka Yuki", "company": "株式会社ABC", "tel": "03-1234-5678"}'
    )

    assert card.full_name == "Tanaka Yuki"
    assert card.company_name_raw == "株式会社ABC"
    assert card.phone is not None


def test_parse_json_chuoi_rong_thanh_none():
    card = parse_response('{"full_name": "A", "email": "N/A", "website": "không có"}')

    assert card.email is None
    assert card.website is None


def test_parse_khong_phai_json_thi_giu_nguyen_van_cau_tra_loi():
    with pytest.raises(OcrParseError) as exc:
        parse_response("Xin lỗi, tôi không đọc được ảnh này.")

    # Mất chuỗi gốc là mất luôn khả năng biết model đã trả cái gì (docs/bugs-f1-f3.md).
    assert "không đọc được" in exc.value.raw_text


def test_parse_json_dung_cu_phap_nhung_sai_kieu():
    with pytest.raises(OcrParseError) as exc:
        parse_response('{"full_name": "A", "is_business_card": "chắc chắn rồi"}')

    assert "schema" in str(exc.value)


# --------------------------------------------------------------------------- trạng thái


def make_result(**overrides) -> OcrResult:
    extraction = CardExtraction.model_validate({**CARD_JSON, **overrides})
    return OcrResult(
        extraction=extraction,
        raw_json=CARD_JSON,
        raw_text="{}",
        model="gemini-3-flash",
        elapsed_ms=1,
    )


def test_trang_thai_khi_ocr_hong_la_pending():
    status, notes = status_and_notes(None, "CLIProxy không phản hồi")

    assert status is CardStatus.PENDING
    assert "CLIProxy không phản hồi" in (notes or "")


def test_trang_thai_khi_quet_xong_la_cho_nguoi_duyet():
    status, notes = status_and_notes(make_result(), None)

    assert status is CardStatus.NEEDS_REVIEW
    assert notes is None


def test_anh_khong_phai_danh_thiep_van_cho_duyet_nhung_co_ghi_chu():
    status, notes = status_and_notes(make_result(is_business_card=False), None)

    assert status is CardStatus.NEEDS_REVIEW
    assert "không phải danh thiếp" in (notes or "")


# --------------------------------------------------------------------------- gọi model thật (mock HTTP)


async def test_extract_card_lot_ngay_luot_dau(cliproxy):
    cliproxy.reply(dumps(CARD_JSON))

    result = await ocr.extract_card(FAKE_JPEG)

    assert result.attempts == 1
    assert result.extraction.full_name == "Nguyễn Văn A"
    assert result.model == "gemini-3-flash"
    assert len(cliproxy.calls) == 1


async def test_extract_card_thu_lai_khi_luot_dau_tra_chu(cliproxy):
    """Một lượt gọi lại kèm lời nhắc gắt hơn — `MAX_ATTEMPTS = 2`."""
    cliproxy.reply("Ảnh hơi mờ, để tôi mô tả bằng lời nhé…", dumps(CARD_JSON))

    result = await ocr.extract_card(FAKE_JPEG)

    assert result.attempts == 2
    assert len(cliproxy.calls) == 2
    # Lượt thứ hai phải có thêm lời nhắc, nếu không thì gọi lại y hệt cũng vô ích.
    assert ocr.RETRY_HINT in cliproxy.calls[1].request.content.decode()


async def test_extract_card_hong_ca_hai_luot_thi_bao_loi(cliproxy):
    cliproxy.reply("không đọc được", "vẫn không đọc được")

    with pytest.raises(OcrParseError):
        await ocr.extract_card(FAKE_JPEG)

    assert len(cliproxy.calls) == ocr.MAX_ATTEMPTS


async def test_extract_card_khong_nuot_loi_bi_chan(cliproxy):
    """`LLMBlockedError` phải đi thẳng ra router: "bị chặn" khác hẳn "quét hỏng" (task 9.4)."""
    cliproxy.reply_payload({"candidates": [{"finishReason": "SAFETY"}]})

    with pytest.raises(LLMBlockedError):
        await ocr.extract_card(FAKE_JPEG)

    # Bị chặn thì không thử lại — thử lại cũng bị chặn y như vậy.
    assert len(cliproxy.calls) == 1


async def test_extract_card_tu_choi_anh_rong_truoc_khi_goi_mang(cliproxy):
    with pytest.raises(LLMError):
        await ocr.extract_card(b"")

    assert len(cliproxy.calls) == 0
