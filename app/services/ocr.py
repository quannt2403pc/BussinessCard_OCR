"""Gọi Gemini Flash Vision, parse & validate JSON trả về.

Chủ sở hữu: Q | Task: 3.4 | xem Task.md

Đường đi một ảnh: `routers/cards.py` (3.1) → `services/image.py` (3.2) → **file này** →
`services/normalize.py` (3.6) → DB (3.5).

Hai chỗ hỏng đã biết trước khi viết dòng code đầu tiên:

1. **I-15 — model bọc JSON trong khối ```json.** Đo thật ở task 2.3: prompt ghi rõ "không bọc"
   mà `gemini-3-flash` vẫn bọc. `json.loads()` thẳng vào đó là `JSONDecodeError` ngay lời gọi
   đầu tiên. `_to_json()` gỡ hàng rào code, và nếu vẫn hỏng thì quét lấy object `{…}` cân bằng
   ngoặc đầu tiên trong chuỗi — bắt được cả trường hợp model thêm một câu dẫn trước JSON.
2. **Model trả JSON hợp lệ nhưng sai khoá / sai kiểu.** Không có hợp đồng nào ràng buộc nó cả.
   `CardExtraction` (task 3.4, `app/schemas/card.py`) lo phần này; ở đây chỉ lo cú pháp.

Thất bại parse được **thử lại đúng một lần** kèm lời nhắc gắt hơn. Thử lại lỗi kết nối/OAuth thì
không — `CliProxyClient` đã lo retry mạng, còn 4xx thì thử lại chỉ tốn thời gian (I-05, I-10).
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.core.config import settings
from app.prompts import ocr as prompts
from app.schemas.card import CardExtraction
from app.services import image as image_service
from app.services import llm
from app.services.cliproxy_client import CliProxyClient
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

#: Hàng rào code ```json … ``` (I-15).
_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_+-]*\s*\n?(?P<body>.*?)\n?\s*```\s*$", re.DOTALL)

#: Dấu phẩy thừa trước `}` hoặc `]` — lỗi cú pháp JSON duy nhất mà model hay mắc và sửa được
#: an toàn. Chỉ dùng ở bước cuối, sau khi đã cắt đúng phạm vi object.
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


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


def parse_response(text: str) -> CardExtraction:
    """Chuỗi model trả về → `CardExtraction` đã chuẩn hoá. Tách riêng để test không cần gọi mạng."""
    return _validate(_to_json(text))


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
    """Chuỗi model trả về → dict. Gỡ hàng rào code, cắt phần thừa, vá dấu phẩy thừa.

    Bốn lần thử, dừng ngay khi thành công:

    1. nguyên văn — trường hợp model ngoan;
    2. bỏ hàng rào ```…``` (I-15) — trường hợp gặp thật ở task 2.3;
    3. quét lấy object `{…}` cân bằng ngoặc đầu tiên — bắt được cả lời dẫn kiểu "Đây là JSON:";
    4. bỏ dấu phẩy thừa trước `}`/`]`.
    """
    candidates = []
    stripped = text.strip()
    if stripped:
        candidates.append(stripped)

    fenced = _FENCE_RE.match(stripped)
    if fenced:
        candidates.append(fenced.group("body").strip())

    span = _first_json_object(candidates[-1] if candidates else stripped)
    if span:
        candidates.append(span)
        candidates.append(_TRAILING_COMMA_RE.sub(r"\1", span))

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            return data
        # Model đôi khi gói kết quả trong mảng một phần tử.
        if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
            return data[0]

    raise OcrParseError(
        "Model không trả về JSON đọc được. Thường là do ảnh không phải danh thiếp hoặc model "
        "trả lời bằng lời văn — xem `raw_text` trong log để biết chính xác.",
        raw_text=text[:500],
    )


def _first_json_object(text: str) -> str | None:
    """Cắt object JSON cân bằng ngoặc đầu tiên trong chuỗi.

    Đếm ngoặc chứ không dùng regex: giá trị trong danh thiếp có thể chứa `{`/`}` (địa chỉ, tên
    công ty), và chuỗi JSON có thể chứa dấu nháy đã escape — regex không xử lý nổi hai thứ đó.
    """
    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None
