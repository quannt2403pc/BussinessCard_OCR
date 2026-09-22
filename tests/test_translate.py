"""Test Việt hoá sau khi quét: bảng tra cứu, lượt gọi model, và cách hai tầng ghép vào nhau.

Chủ sở hữu: Q | Task: EX-07 | xem Task.md

Chia đúng theo hai tầng của `services/translate.py`:

* **Tầng tra cứu** chạy không cần model — test được như một hàm thuần, và đó là lý do nó tồn
  tại: `株式会社` phải luôn ra *Công ty Cổ phần*, không phụ thuộc hôm nay model trả lời thế nào.
* **Tầng model** mock ở tầng HTTP bằng fixture `cliproxy`, cùng lối với `test_ocr.py`: thứ hay
  hỏng không phải chỗ ta gọi sai hàm mà là hình dạng câu trả lời (bọc ```json — I-15, thiếu
  trường, trả chữ không phải JSON).

Chất lượng bản dịch trên thẻ thật không đo ở đây mà ở `scripts/check_multilang_ocr.py` và
`docs/accuracy.md` phần C — cùng ranh giới mà `test_ocr.py` đã đặt cho phần đọc chữ.
"""

from __future__ import annotations

import json

import pytest

from app.services import ocr, translate
from app.services.llm import LLMNotConnectedError

#: Ảnh giả: `services/llm.py` chỉ kiểm rỗng/kiểu MIME, không mở ảnh ra xem.
FAKE_JPEG = b"\xff\xd8\xff\xe0 fake jpeg"

JP_CARD = {
    "full_name": "田中 太郎",
    "job_title": "営業部長",
    "company_name_raw": "東京テック株式会社",
    "address": "東京都渋谷区神宮前1-2-3",
}

JP_TRANSLATION = {
    "full_name_vi": "Tanaka Taro",
    "job_title_vi": "Trưởng phòng Kinh doanh",
    "company_name_vi": "Công ty Cổ phần Tokyo Tech",
    "address_vi": "1-2-3 Jingumae, Quận Shibuya, Tokyo",
    "source_language": "ja",
    "script": "Jpan",
    "name_method": "romaji",
    "note": None,
}


def dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


# --------------------------------------------------------------------------- bảng tra cứu


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("東京テック株式会社", ("Công ty Cổ phần", "東京テック")),
        ("株式会社さくらテクノロジー", ("Công ty Cổ phần", "さくらテクノロジー")),
        ("深圳市远景电子有限公司", ("Công ty TNHH", "深圳市远景电子")),
        ("한화정밀기계 주식회사", ("Công ty Cổ phần", "한화정밀기계")),
        ("FPT Software Co., Ltd.", ("Công ty TNHH", "FPT Software")),
        ("Samsung Electronics Co., Ltd", ("Công ty TNHH", "Samsung Electronics")),
        ("Vinamilk JSC", ("Công ty Cổ phần", "Vinamilk")),
        ("Siemens AG", ("Công ty Cổ phần", "Siemens")),
        ("PT Astra International", ("Công ty TNHH", "Astra International")),
        ("ABC Group", ("Tập đoàn", "ABC")),
    ],
)
def test_bóc_loại_hình_pháp_nhân(raw: str, expected: tuple[str, str]):
    assert translate.split_legal_form(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        # `Group` chỉ là loại hình khi đứng CUỐI. Khớp cả ở đầu thì đây thành "Tập đoàn Dynamics
        # Institute" — một công ty khác hẳn.
        "Group Dynamics Institute",
        # `Ltd` nằm giữa một từ khác: không khớp nguyên từ thì `Altdorf` mất luôn chữ đầu.
        "Altdorf Innovation",
        # Không có loại hình nào: tuyệt đối không đoán.
        "Sakura Technology",
        "東京テック",
    ],
)
def test_không_nhận_ra_loại_hình_thì_giữ_nguyên(raw: str):
    assert translate.split_legal_form(raw) == (None, raw)


def test_tra_cứu_chức_vụ_không_cần_model():
    result = translate.translate_offline({"job_title": "Sales Manager"})

    assert result.values["job_title_vi"] == "Trưởng phòng Kinh doanh"
    assert result.meta["source"] == "dictionary"


def test_tra_cứu_ghép_loại_hình_với_tên_latin():
    result = translate.translate_offline({"company_name_raw": "FPT Software Co., Ltd."})

    assert result.values["company_name_vi"] == "Công ty TNHH FPT Software"


def test_tra_cứu_không_ghép_khi_tên_riêng_còn_là_chữ_bản_địa():
    """`"Công ty Cổ phần 東京テック"` là một chuỗi nửa Việt nửa Nhật — thà để trống cho model."""
    result = translate.translate_offline({"company_name_raw": "東京テック株式会社"})

    assert result.values["company_name_vi"] is None


def test_thẻ_tiếng_việt_không_sinh_bản_dịch_nào():
    """Bản dịch trùng y hệt bản gốc phải thành `None`, nếu không giao diện in hai dòng như nhau."""
    result = translate.translate_offline(
        {"full_name": "Nguyễn Văn A", "job_title": "Giám đốc", "company_name_raw": "Công ty CP FPT"}
    )

    assert result.is_empty


def test_thẻ_rỗng_thì_bỏ_qua_hẳn():
    result = translate.translate_offline({"full_name": None, "email": "a@b.vn"})

    assert result.meta["source"] == "skipped"
    assert result.values == {}


# --------------------------------------------------------------------------- lượt gọi model


async def test_dịch_bằng_model(cliproxy):
    cliproxy.reply(dumps(JP_TRANSLATION))

    result = await translate.translate_card(JP_CARD, language="ja")

    assert result.values["full_name_vi"] == "Tanaka Taro"
    assert result.values["company_name_vi"] == "Công ty Cổ phần Tokyo Tech"
    assert result.values["address_vi"] == "1-2-3 Jingumae, Quận Shibuya, Tokyo"
    assert result.meta["source"] == "llm"
    assert result.meta["source_language"] == "ja"
    assert result.meta["name_method"] == "romaji"
    assert result.meta["stale"] is False


async def test_model_bọc_json_trong_hàng_rào_code(cliproxy):
    """I-15 lặp lại ở lượt gọi thứ hai — `services/llm_json.py` phải gỡ được."""
    cliproxy.reply(f"```json\n{dumps(JP_TRANSLATION)}\n```")

    result = await translate.translate_card(JP_CARD, language="ja")

    assert result.values["full_name_vi"] == "Tanaka Taro"


async def test_model_thiếu_trường_thì_bảng_tra_cứu_lấp(cliproxy):
    """Model chỉ dịch được tên → chức vụ và công ty vẫn có, và `source` nói rõ là bản ghép."""
    cliproxy.reply(dumps({"full_name_vi": "Tanaka Taro", "source_language": "ja"}))

    result = await translate.translate_card(
        {**JP_CARD, "company_name_raw": "FPT Japan Co., Ltd.", "job_title": "Sales Manager"},
        language="ja",
    )

    assert result.values["full_name_vi"] == "Tanaka Taro"
    assert result.values["job_title_vi"] == "Trưởng phòng Kinh doanh"
    assert result.values["company_name_vi"] == "Công ty TNHH FPT Japan"
    assert result.meta["source"] == "mixed"


async def test_model_trả_lời_bằng_lời_văn_thì_rơi_về_bảng_tra_cứu(cliproxy):
    cliproxy.reply("Xin chào, tôi có thể giúp gì cho bạn?")

    result = await translate.translate_card(
        {"job_title": "CEO", "company_name_raw": "ABC Group"}, language="en"
    )

    assert result.values["job_title_vi"] == "Tổng giám đốc"
    assert result.values["company_name_vi"] == "Tập đoàn ABC"
    assert result.meta["source"] == "dictionary"
    assert "error" in result.meta


async def test_chưa_kết_nối_oauth_không_làm_hỏng_lượt_dịch(cliproxy):
    """Luồng quét tự động: model không gọi được thì vẫn phải trả về `Translation`, không ném lỗi."""
    cliproxy.fail(401, {"error": {"message": "authentication_error"}})

    result = await translate.translate_card(JP_CARD, language="ja")

    assert result.meta["source"] in ("dictionary", "failed")
    assert result.values["job_title_vi"] == "Trưởng phòng Kinh doanh"  # tra cứu vẫn chạy


async def test_nút_dịch_lại_thì_lỗi_được_báo_ra(cliproxy):
    """`raise_on_error=True` giữ NGUYÊN loại lỗi để router chọn đúng 503 hay 502."""
    cliproxy.fail(401, {"error": {"message": "authentication_error"}})

    with pytest.raises(LLMNotConnectedError):
        await translate.translate_card(JP_CARD, language="ja", raise_on_error=True)


async def test_bản_dịch_dài_quá_cột_bị_cắt(cliproxy):
    cliproxy.reply(dumps({"company_name_vi": "A" * 400}))

    result = await translate.translate_card({"company_name_raw": "東京テック株式会社"})

    assert len(result.values["company_name_vi"] or "") == 255


async def test_tắt_bằng_cấu_hình_thì_không_gọi_model(cliproxy, monkeypatch):
    monkeypatch.setattr(translate.settings, "translate_after_ocr", False)

    result = await translate.translate_card({"job_title": "CEO"}, language="en")

    assert result.values["job_title_vi"] == "Tổng giám đốc"
    assert result.meta["source"] == "dictionary"
    assert len(cliproxy.calls) == 0


# --------------------------------------------------------------------------- ghép vào OCR


OCR_JSON = {
    "full_name": "田中 太郎",
    "job_title": "営業部長",
    "company_name_raw": "東京テック株式会社",
    "email": "tanaka@tokyotech.co.jp",
    "phone": "090-1234-5678",
    "address": "東京都渋谷区神宮前1-2-3",
    "website": "tokyotech.co.jp",
    "language_detected": "ja",
    "confidence": {"full_name": 0.95},
}


async def test_quét_rồi_dịch_ghép_đủ_cột_ghi_db(cliproxy):
    """`extract_and_translate` = 2 lượt gọi model, và `card_fields()` gộp đủ cả hai phía."""
    cliproxy.reply(dumps(OCR_JSON), dumps(JP_TRANSLATION))

    result = await ocr.extract_and_translate(FAKE_JPEG)
    fields = result.card_fields()

    assert len(cliproxy.calls) == 2
    # Bản gốc KHÔNG bị ghi đè — đó là thứ duy nhất còn đối chiếu được với ảnh.
    assert fields["full_name"] == "田中 太郎"
    assert fields["full_name_vi"] == "Tanaka Taro"
    assert fields["company_name_vi"] == "Công ty Cổ phần Tokyo Tech"
    assert fields["translation_meta"]["source"] == "llm"
    # Chuẩn hoá của 3.6 vẫn chạy nguyên vẹn trên trường gốc.
    assert fields["phone"] == "+819012345678"


async def test_dịch_hỏng_không_làm_hỏng_lượt_quét(cliproxy):
    """Lượt 1 ra JSON thẻ, lượt 2 hỏng → vẫn có kết quả quét đầy đủ, chỉ thiếu bản dịch."""
    cliproxy.reply(dumps(OCR_JSON), "không phải JSON")

    result = await ocr.extract_and_translate(FAKE_JPEG)
    fields = result.card_fields()

    assert fields["full_name"] == "田中 太郎"
    assert fields["job_title_vi"] == "Trưởng phòng Kinh doanh"  # bảng tra cứu đỡ được
    assert fields["full_name_vi"] is None
    assert "error" in fields["translation_meta"]


async def test_extract_card_không_gọi_lượt_dịch(cliproxy):
    """Hợp đồng cũ của `extract_card()` không đổi: đúng 1 lượt gọi model, không có bản dịch."""
    cliproxy.reply(dumps(OCR_JSON))

    result = await ocr.extract_card(FAKE_JPEG)

    assert len(cliproxy.calls) == 1
    assert result.translation is None
    assert result.card_fields()["full_name"] == "田中 太郎"
