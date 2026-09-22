"""Gọi Gemini Flash Vision, parse & validate JSON trả về.

Chủ sở hữu: Q | Task: 3.4 | xem Task.md

Đường đi một ảnh: `routers/cards.py` (3.1) → `services/image.py` (3.2) → **file này** →
`services/normalize.py` (3.6) → `services/translate.py` (EX-02) → DB (3.5).

Hai chỗ hỏng đã biết trước khi viết dòng code đầu tiên:

1. **I-15 — model bọc JSON trong khối ```json.** Đo thật ở task 2.3: prompt ghi rõ "không bọc"
   mà `gemini-3-flash` vẫn bọc. `services/llm_json.py` gỡ hàng rào code, và nếu vẫn hỏng thì
   quét lấy object `{…}` cân bằng ngoặc đầu tiên trong chuỗi — bắt được cả trường hợp model
   thêm một câu dẫn trước JSON. (Trước EX-02 phần này nằm ngay trong file; chuyển ra ngoài khi
   lượt Việt hoá cần đúng logic đó cho lời gọi model thứ hai.)
2. **Model trả JSON hợp lệ nhưng sai khoá / sai kiểu.** Không có hợp đồng nào ràng buộc nó cả.
   `CardExtraction` (task 3.4, `app/schemas/card.py`) lo phần này; ở đây chỉ lo cú pháp.

Thất bại parse được **thử lại đúng một lần** kèm lời nhắc gắt hơn. Thử lại lỗi kết nối/OAuth thì
không — `CliProxyClient` đã lo retry mạng, còn 4xx thì thử lại chỉ tốn thời gian (I-05, I-10).
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
#: chờ 3 lượt vision (~10s) để rồi vẫn hỏng — thà báo lỗi sớm cho họ bấm quét lại.
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

    Giữ nguyên văn câu trả lời trong `raw_text` để ghi log và dán vào `docs/bugs-f1-f3.md` khi
    cần điều tra — mất chuỗi gốc là mất luôn khả năng biết model đã trả cái gì.
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
    #: Bản Việt hoá (EX-02), `None` khi lượt quét không chạy bước đó. Cố ý **không** trộn vào
    #: `extraction`: `CardExtraction` là *chữ model đọc được từ ảnh*, còn đây là kết quả của một
    #: lời gọi model khác trên chính dữ liệu đó. Gộp làm một thì không còn phân biệt được
    #: "model đọc sai chữ" với "model dịch sai chữ đọc đúng" — hai lỗi phải sửa ở hai chỗ.
    translation: translate_service.Translation | None = None

    def card_fields(self) -> dict[str, Any]:
        """Toàn bộ phần ghi vào `business_cards`: trường OCR + 4 cột `*_vi` + `translation_meta`.

        Chỗ **duy nhất** gộp hai nguồn, để hai đường vào (upload 1 ảnh ở `routers/cards.py` và
        upload hàng loạt ở `services/card_batch.py`) không bao giờ lệch nhau về việc cột nào
        được ghi — đúng lý lẽ đã viết ở `status_and_notes()`.
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
    client: CliProxyClient | None = None,
) -> OcrResult:
    """Đọc một ảnh danh thiếp đã tiền xử lý thành `CardExtraction` đã chuẩn hoá.

    `image_bytes` phải là ảnh **đã qua `services/image.py`** (JPEG, cạnh dài ≤ 1600px): ảnh gốc
    từ điện thoại có thể nằm sai chiều theo EXIF, model đọc đúng pixel nhưng chữ xoay 90°.

    Ném `OcrParseError` khi model trả chữ không dùng được, và để nguyên `llm.LLMError` các loại
    (`LLMNotConnectedError`, `LLMBlockedError`…) đi tiếp — router phân biệt "chưa kết nối OAuth"
    với "quét hỏng" để hiện đúng lời mời bấm nút (task 9.4).
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
            model=settings.llm_model,
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
    client: CliProxyClient | None = None,
) -> OcrResult:
    """`extract_card()` + lượt Việt hoá (EX-02). **Đây là hàm hai đường upload cùng gọi.**

    Tách khỏi `extract_card()` chứ không nhét thẳng vào trong, vì hai lý do khác nhau:

    * `extract_card()` là *đọc chữ trên ảnh* và có hợp đồng riêng — số lượt gọi model của nó
      (`attempts`, `MAX_ATTEMPTS`) là thứ test và log đang đếm. Thêm một lời gọi nữa vào giữa
      làm hỏng phép đếm đó.
    * Việt hoá là **tiện ích**: hỏng thì thẻ vẫn phải quét xong. Ở đây lỗi bị nuốt hẳn
      (`translate_card()` tự rơi về bảng tra cứu), còn `extract_card()` vẫn ném lỗi như cũ.
    """
    result = await extract_card(image_bytes, mime_type=mime_type, hint=hint, client=client)
    translation = await translate_service.translate_card(
        result.extraction.model_dump(),
        language=result.extraction.language_detected,
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
    duyệt (task 5.1). Không bao giờ tự nhảy sang `confirmed` — xác nhận là việc của người dùng
    (task 4.3).

    Nằm ở đây chứ không ở `routers/cards.py` vì từ task 5.2 có **hai** đường đi tới cùng một kết
    luận: upload 1 ảnh (đồng bộ, trong request) và upload hàng loạt (nền, `services/card_batch.py`).
    Hai bản sao của cùng một quy tắc vòng đời là chỗ sẽ lệch nhau mà không ai nhận ra — sửa một
    bên rồi quên bên kia thì cùng một ảnh hỏng lại ra hai trạng thái khác nhau tuỳ đường vào.
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
    """Ép JSON thô vào schema rồi chạy hậu xử lý SĐT/email (task 3.6).

    Chuẩn hoá **sau** khi validate chứ không phải trước: `CardExtraction` mới là chỗ biết
    `language_detected` sau khi đã gom alias, mà mã vùng số điện thoại lại phụ thuộc trường đó.
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

    Phần đếm ngoặc / gỡ hàng rào ```json (I-15) chuyển sang `services/llm_json.py` ở EX-02, khi
    lượt Việt hoá cần đúng logic đó cho lời gọi model thứ hai. Giữ lại hàm này vì lớp dịch lỗi
    mới là phần thuộc về F1: `OcrParseError` mang theo `raw_text` để dán vào `docs/bugs-f1-f3.md`.
    """
    try:
        return extract_json_object(text)
    except JsonExtractError as exc:
        raise OcrParseError(
            "Model không trả về JSON đọc được. Thường là do ảnh không phải danh thiếp hoặc model "
            "trả lời bằng lời văn — xem `raw_text` trong log để biết chính xác.",
            raw_text=text[:500],
        ) from exc
