"""Gọi Gemini Flash Vision, parse & validate JSON trả về.

Chủ sở hữu: Q | Task: 3.4

Đường đi một ảnh: `routers/cards.py` → `services/image.py` → **file này** →
`services/normalize.py` → `services/translate.py` → DB.

Hai chỗ hỏng đã biết trước:

1. **I-15 — model bọc JSON trong khối ```json** dù prompt ghi rõ "không bọc". `services/llm_json.py`
   gỡ hàng rào, và nếu vẫn hỏng thì quét lấy object `{…}` cân bằng ngoặc đầu tiên.
2. **Model trả JSON hợp lệ nhưng sai khoá / sai kiểu** — `CardExtraction` lo phần này; ở đây chỉ
   lo cú pháp.

Thất bại parse được **thử lại đúng một lần** kèm lời nhắc gắt hơn. Lỗi kết nối/OAuth thì không.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.core.config import settings
from app.models.card import CardStatus
from app.prompts import ocr as prompts
from app.schemas.card import CardExtraction
from app.services import image as image_service
from app.services import llm
from app.services import translate as translate_service
from app.services.cliproxy_client import CliProxyClient
from app.services.llm_json import JsonExtractError, extract_json_object
from app.services.normalize import normalize_card_fields

logger = logging.getLogger(__name__)

#: Một lần gọi lại khi model trả chữ không parse được thành JSON. Nhiều hơn thì người dùng ngồi
#: chờ 3 lượt vision (~10s) để rồi vẫn hỏng.
MAX_ATTEMPTS = 2

#: Nhắc thêm ở lượt thử lại. Cố ý cộc lốc: lượt đầu đã có prompt đầy đủ rồi.
RETRY_HINT = (
    "Lượt trước bạn trả về chữ không phải JSON hợp lệ. Lần này chỉ in ra đúng một object JSON, "
    "ký tự đầu tiên là { và ký tự cuối cùng là }, không có chữ nào khác."
)


class OcrError(RuntimeError):
    """Không lấy được kết quả OCR. Router dịch thành thông báo cho người dùng (task 3.1)."""


class OcrParseError(OcrError):
    """Model trả lời nhưng không đọc được thành dữ liệu danh thiếp.

    Giữ nguyên văn câu trả lời trong `raw_text`: mất chuỗi gốc là mất luôn khả năng biết model đã
    trả cái gì.
    """

    def __init__(self, message: str, raw_text: str = "") -> None:
        super().__init__(message)
        self.raw_text = raw_text


@dataclass(frozen=True, slots=True)
class OcrResult:
    """Kết quả một lượt quét: dữ liệu đã chuẩn hoá + đủ số liệu để ghi log và đo (task 3.5)."""

    extraction: CardExtraction
    #: JSON model trả về, **trước** khi chuẩn hoá — ghi vào `business_cards.ocr_raw_json`.
    raw_json: dict[str, Any]
    raw_text: str
    model: str
    elapsed_ms: int
    attempts: int = 1
    #: Bản Việt hoá, `None` khi lượt quét không chạy bước đó. Cố ý **không** trộn vào `extraction`:
    #: gộp làm một thì không phân biệt được "model đọc sai chữ" với "model dịch sai chữ đọc đúng".
    translation: translate_service.Translation | None = None

    def card_fields(self) -> dict[str, Any]:
        """Toàn bộ phần ghi vào `business_cards`: trường OCR + 4 cột `*_vi` + `translation_meta`.

        Chỗ **duy nhất** gộp hai nguồn, để hai đường upload không bao giờ lệch nhau về việc cột
        nào được ghi.
        """
        fields = self.extraction.card_columns()
        if self.translation is not None:
            fields.update(self.translation.columns())
        return fields


async def extract_card(
    image_bytes: bytes,
    *,
    mime_type: str = image_service.OUTPUT_MIME,
    hint: str | None = None,
    model: str | None = None,
    client: CliProxyClient | None = None,
) -> OcrResult:
    """Đọc một ảnh danh thiếp đã tiền xử lý thành `CardExtraction` đã chuẩn hoá.

    `image_bytes` phải là ảnh **đã qua `services/image.py`**: ảnh gốc từ điện thoại có thể nằm
    sai chiều theo EXIF, model đọc đúng pixel nhưng chữ xoay 90°.

    Ném `OcrParseError` khi model trả chữ không dùng được, và để nguyên `llm.LLMError` đi tiếp —
    router phân biệt "chưa kết nối OAuth" với "quét hỏng".
    """
    started = time.perf_counter()
    last_error: OcrParseError | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        prompt = prompts.build_prompt(hint)
        if attempt > 1:
            prompt = f"{prompt}\n\n{RETRY_HINT}"

        text = await llm.generate_vision(
            prompt,
            image_bytes,
            mime_type=mime_type,
            system=prompts.SYSTEM_PROMPT,
            model=model,
            temperature=0.0,
            client=client,
        )

        try:
            raw_json = _to_json(text)
            extraction = _validate(raw_json)
        except OcrParseError as exc:
            last_error = exc
            logger.warning(
                "Lượt %d/%d: không đọc được JSON từ model (%s)", attempt, MAX_ATTEMPTS, exc
            )
            continue

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "OCR xong sau %dms (%d lượt gọi), thiếu %d/%d trường bắt buộc",
            elapsed_ms,
            attempt,
            len(extraction.missing_required()),
            len(prompts.REQUIRED_FIELDS),
        )
        return OcrResult(
            extraction=extraction,
            raw_json=raw_json,
            raw_text=text,
            # Model **thật sự đã gọi**, không phải model mặc định của hệ thống. `base_model()`
            # cắt tiền tố credential — ghi nó vào sổ là lộ một phần `user_id` ra bản ghi thẻ.
            model=llm.base_model(model or settings.llm_model),
            elapsed_ms=elapsed_ms,
            attempts=attempt,
        )

    if last_error is None:  # không xảy ra: vòng lặp hoặc `return`, hoặc gán `last_error`
        raise OcrError("Không nhận được câu trả lời nào từ model.")
    raise last_error


async def extract_and_translate(
    image_bytes: bytes,
    *,
    mime_type: str = image_service.OUTPUT_MIME,
    hint: str | None = None,
    model: str | None = None,
    client: CliProxyClient | None = None,
) -> OcrResult:
    """`extract_card()` + lượt Việt hoá. **Đây là hàm hai đường upload cùng gọi.**

    Tách khỏi `extract_card()` vì hai lý do: số lượt gọi model của `extract_card()` là thứ test và
    log đang đếm, thêm một lời gọi vào giữa làm hỏng phép đếm đó; và Việt hoá là **tiện ích**,
    hỏng thì thẻ vẫn phải quét xong nên ở đây lỗi bị nuốt hẳn.
    """
    result = await extract_card(
        image_bytes, mime_type=mime_type, hint=hint, model=model, client=client
    )
    translation = await translate_service.translate_card(
        result.extraction.model_dump(),
        language=result.extraction.language_detected,
        model=model,
        client=client,
    )
    if translation.is_empty and translation.meta.get("source") in ("failed", "skipped"):
        logger.info("Không Việt hoá được thẻ vừa quét: %s", translation.meta)
    return dataclasses.replace(result, translation=translation)


def parse_response(text: str) -> CardExtraction:
    """Chuỗi model trả về → `CardExtraction` đã chuẩn hoá. Tách riêng để test không cần gọi mạng."""
    return _validate(_to_json(text))


def status_and_notes(result: OcrResult | None, error: str | None) -> tuple[CardStatus, str | None]:
    """Trạng thái vòng đời + ghi chú cho một bản ghi vừa quét.

    `pending` = chưa quét được, còn phải quét lại. `needs_review` = đã có dữ liệu, chờ người
    duyệt. Không bao giờ tự nhảy sang `confirmed`.

    Nằm ở đây chứ không ở router vì có **hai** đường đi tới cùng một kết luận (upload 1 ảnh và
    upload hàng loạt); hai bản sao của cùng một quy tắc vòng đời sẽ lệch nhau mà không ai nhận ra.
    """
    if result is None:
        return CardStatus.PENDING, f"OCR chưa chạy được: {error}"
    if not result.extraction.is_business_card:
        return (
            CardStatus.NEEDS_REVIEW,
            "Model cho rằng ảnh này không phải danh thiếp — kiểm lại trước khi xác nhận.",
        )
    return CardStatus.NEEDS_REVIEW, None


# --------------------------------------------------------------------------- nội bộ


def _validate(raw_json: dict[str, Any]) -> CardExtraction:
    """Ép JSON thô vào schema rồi chạy hậu xử lý SĐT/email.

    Chuẩn hoá **sau** khi validate: `CardExtraction` mới là chỗ biết `language_detected` sau khi
    đã gom alias, mà mã vùng số điện thoại lại phụ thuộc trường đó.
    """
    try:
        extraction = CardExtraction.model_validate(raw_json)
    except ValidationError as exc:
        raise OcrParseError(
            f"JSON đọc được nhưng không khớp schema danh thiếp: {exc.error_count()} lỗi. "
            f"Chi tiết: {exc.errors()[:3]}",
            raw_text=json.dumps(raw_json, ensure_ascii=False)[:500],
        ) from exc

    normalized = normalize_card_fields(
        extraction.model_dump(), language=extraction.language_detected
    )
    return CardExtraction.model_validate(normalized)


def _to_json(text: str) -> dict[str, Any]:
    """Chuỗi model trả về → dict, dịch lỗi bóc JSON thành lỗi của F1.

    Phần đếm ngoặc / gỡ hàng rào ```json nằm ở `services/llm_json.py`; giữ hàm này vì lớp dịch
    lỗi mới là phần thuộc về F1 — `OcrParseError` mang theo `raw_text` để điều tra.
    """
    try:
        return extract_json_object(text)
    except JsonExtractError as exc:
        raise OcrParseError(
            "Model không trả về JSON đọc được. Thường là do ảnh không phải danh thiếp hoặc model "
            "trả lời bằng lời văn — xem `raw_text` trong log để biết chính xác.",
            raw_text=text[:500],
        ) from exc
