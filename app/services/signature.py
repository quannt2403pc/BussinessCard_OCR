"""Trích xuất liên hệ từ khối chữ ký email.

Chủ sở hữu: T | Task: NEXT-08 | xem Task.md

**Đổi đầu vào, giữ nguyên mọi thứ còn lại.** Phần lớn liên hệ ngày nay đến qua email chứ không
qua thẻ giấy, nhưng thứ cần làm với chúng thì y hệt: đọc ra các trường, chuẩn hoá, Việt hoá,
rồi đưa vào màn hình review để người dùng sửa và xác nhận.

Nên file này **không** dựng lại đường ống nào cả. Nó gọi model bằng chữ thay vì ảnh, rồi trả
kết quả qua đúng `ocr.parse_response()` của Q — cùng schema, cùng bước chuẩn hoá SĐT/email,
cùng `OcrResult`, cùng lượt Việt hoá `EX-02`, cùng `status_and_notes()`. Một chỗ duy nhất khác
biệt là nguồn dữ liệu, và nó được ghi lại ở `business_cards.source`.

`image_hash` của thẻ nhập từ chữ ký là **sha256 của khối chữ đã chuẩn hoá**, không phải bam
ảnh. Nhờ vậy ràng buộc unique `(user_id, image_hash)` có sẵn từ `3.1` bắt luôn việc dán hai lần
cùng một chữ ký — không phải thêm cơ chế chống trùng thứ hai.
"""

import dataclasses
import hashlib
import logging
import time

from app.prompts import signature as prompts
from app.services import llm
from app.services import translate as translate_service
from app.services.cliproxy_client import CliProxyClient
from app.services.ocr import MAX_ATTEMPTS, OcrParseError, OcrResult, parse_response

logger = logging.getLogger(__name__)

RETRY_HINT = "Lượt trước trả về chữ không phải JSON. Lần này chỉ trả đúng một khối JSON."

#: Ngắn hơn thế thì gần như chắc chắn người dùng dán nhầm, và một lượt gọi model tốn hạn mức
#: cho một dòng chữ vô nghĩa là lãng phí thấy rõ.
MIN_SIGNATURE_CHARS = 20


class EmptySignatureError(ValueError):
    """Khối chữ dán vào ngắn tới mức không thể là một chữ ký."""


def content_hash(text: str) -> str:
    """Khoá chống trùng của một khối chữ ký.

    Chuẩn hoá khoảng trắng trước khi băm: cùng một chữ ký dán từ hai ứng dụng thư khác nhau hay
    lệch nhau ở dấu xuống dòng và khoảng trắng cuối dòng, mà đó không phải là hai liên hệ khác
    nhau.
    """
    normalised = "\n".join(line.strip() for line in text.strip().splitlines() if line.strip())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


async def extract_signature(
    text: str,
    *,
    model: str | None = None,
    client: CliProxyClient | None = None,
) -> OcrResult:
    """Đọc một khối chữ ký email thành `CardExtraction` đã chuẩn hoá.

    Thử lại cùng số lượt với `ocr.extract_card()` và vì cùng một lý do: model thỉnh thoảng trả
    chữ dẫn nhập trước khối JSON, và lượt thử thứ hai kèm lời nhắc thường ra đúng.
    """
    body = text.strip()
    if len(body) < MIN_SIGNATURE_CHARS:
        raise EmptySignatureError(f"Khối chữ ký chỉ có {len(body)} ký tự.")
    body = body[: prompts.MAX_SIGNATURE_CHARS]

    started = time.perf_counter()
    last_error: OcrParseError | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        prompt = prompts.build_prompt(body)
        if attempt > 1:
            prompt = f"{prompt}\n\n{RETRY_HINT}"

        raw_text = await llm.generate_text(
            prompt,
            system=prompts.SYSTEM_PROMPT,
            model=model,
            temperature=0.0,
            client=client,
        )
        try:
            extraction = parse_response(raw_text)
        except OcrParseError as exc:
            last_error = exc
            logger.warning(
                "Lượt %d/%d: không đọc được JSON từ chữ ký (%s)", attempt, MAX_ATTEMPTS, exc
            )
            continue

        return OcrResult(
            extraction=extraction,
            raw_json=extraction.model_dump(mode="json"),
            raw_text=raw_text,
            model=model or llm.settings.llm_model,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            attempts=attempt,
        )

    assert last_error is not None
    raise last_error


async def extract_and_translate(
    text: str,
    *,
    model: str | None = None,
    client: CliProxyClient | None = None,
) -> OcrResult:
    """`extract_signature()` + lượt Việt hoá, đúng cặp đôi mà `ocr.extract_and_translate()` làm.

    Việt hoá vẫn là **tiện ích**: hỏng thì liên hệ vẫn phải nhập xong. Chữ ký tiếng Hàn hay
    tiếng Nhật đi qua đây ra đúng bốn cột `*_vi` như thẻ giấy, vì nó dùng chung hàm ấy.
    """
    result = await extract_signature(text, model=model, client=client)
    translation = await translate_service.translate_card(
        result.extraction.model_dump(),
        language=result.extraction.language_detected,
        model=model,
        client=client,
    )
    if translation.is_empty and translation.meta.get("source") in ("failed", "skipped"):
        logger.info("Không Việt hoá được liên hệ vừa nhập từ chữ ký: %s", translation.meta)
    return dataclasses.replace(result, translation=translation)
