"""F1 — upload, danh sách, chi tiết, confirm danh thiếp.

Chủ sở hữu: Q | Task: 3.1 | xem Task.md

D3 mới làm `POST /api/cards/upload`. Danh sách/chi tiết/confirm là task 4.1–4.3, thêm vào chính
file này (router đã khai sẵn từ D1 nên không phải đụng `app/main.py` — quy ước số 4).

Một lượt upload đi qua đúng bốn bước, theo thứ tự:

1. **Đọc có giới hạn** — cắt ở `MAX_UPLOAD_MB` ngay lúc đọc, không đọc hết rồi mới đo. Đọc hết
   một file 2GB vào RAM để sau đó trả 413 là tự mở cửa cho việc treo container.
2. **Chống trùng** — SHA-256 của **file gốc người dùng gửi**, không phải ảnh sau khi nén: nén
   JPEG không tất định giữa các phiên bản Pillow, băm bản đã nén thì cùng một ảnh vẫn có thể ra
   hai hash khác nhau sau khi nâng thư viện.
3. **Tiền xử lý + lưu volume** (task 3.2).
4. **OCR + lưu DB** (task 3.4, 3.5).

Quyết định đáng chú ý: **OCR hỏng không làm hỏng lượt upload.** Chưa bấm OAuth, CLIProxy chết,
model trả JSON rác — ảnh vẫn được lưu, bản ghi vẫn được tạo ở trạng thái `pending` kèm lý do
trong `ocr_error`, và người dùng quét lại sau. Trả 5xx rồi vứt ảnh đi là bắt người dùng chụp
lại chồng danh thiếp chỉ vì token hết hạn.
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.models.card import CardStatus
from app.repositories import card as card_repo
from app.schemas.card import CardOut, CardUploadOut
from app.services import image as image_service
from app.services import llm, ocr

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cards", tags=["cards"])

#: Đọc từng khối 1MB để giữ RAM phẳng và để dừng được ngay khi vượt ngưỡng.
CHUNK_SIZE = 1024 * 1024


@router.post(
    "/upload",
    response_model=CardUploadOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload 1 ảnh danh thiếp, quét và lưu",
)
async def upload_card(
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    file: Annotated[UploadFile, File(description="Ảnh danh thiếp: JPEG/PNG/WEBP/BMP/TIFF/GIF")],
) -> CardUploadOut:
    """Nhận 1 ảnh → tiền xử lý → gọi Gemini Vision → lưu `business_cards`.

    Trả **201** cho ảnh mới, **200** khi ảnh đã được quét trước đó (`duplicate=true`, trả lại
    đúng bản ghi cũ và không gọi lại model — tiêu chí hoàn thành D3).
    """
    started = time.perf_counter()
    raw = await _read_limited(file)
    image_hash = hashlib.sha256(raw).hexdigest()

    existing = await card_repo.get_by_hash(db, image_hash)
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        logger.info("Ảnh đã quét trước đó, trả lại card %s", existing.id)
        return CardUploadOut(
            card=CardOut.model_validate(existing),
            duplicate=True,
            elapsed_ms=_ms_since(started),
        )

    try:
        processed = image_service.preprocess(raw)
    except image_service.ImageError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    relative_path = _store(processed.data, image_hash)

    # --- OCR: hỏng thì ghi lại lý do chứ không huỷ lượt upload (xem docstring đầu file) ---
    ocr_result: ocr.OcrResult | None = None
    ocr_error: str | None = None
    try:
        ocr_result = await ocr.extract_card(processed.data, mime_type=processed.mime_type)
    except (llm.LLMError, ocr.OcrError) as exc:
        ocr_error = str(exc)
        logger.warning("Quét ảnh %s thất bại: %s", relative_path, exc)

    fields = ocr_result.extraction.card_columns() if ocr_result else {}
    card_status, notes = _status_and_notes(ocr_result, ocr_error)

    try:
        card = await card_repo.create_card(
            db,
            image_path=relative_path,
            image_hash=image_hash,
            fields=fields,
            ocr_raw_json=ocr_result.raw_json if ocr_result else None,
            status=card_status,
            notes=notes,
        )
    except card_repo.DuplicateImageError:
        # Hai lượt upload cùng một ảnh chạy song song; lượt kia đã ghi xong (task 5.2).
        raced = await card_repo.get_by_hash(db, image_hash)
        if raced is None:  # không xảy ra trên PostgreSQL, nhưng đừng trả None cho client
            raise
        response.status_code = status.HTTP_200_OK
        return CardUploadOut(
            card=CardOut.model_validate(raced), duplicate=True, elapsed_ms=_ms_since(started)
        )

    elapsed_ms = _ms_since(started)
    logger.info(
        "Card %s: %s %dx%d → JPEG %d byte, OCR %s, tổng %dms",
        card.id,
        processed.source_format,
        processed.source_width,
        processed.source_height,
        processed.size_bytes,
        f"{ocr_result.elapsed_ms}ms" if ocr_result else f"lỗi ({ocr_error})",
        elapsed_ms,
    )
    return CardUploadOut(
        card=CardOut.model_validate(card),
        duplicate=False,
        missing_required=ocr_result.extraction.missing_required() if ocr_result else [],
        ocr_ms=ocr_result.elapsed_ms if ocr_result else None,
        ocr_error=ocr_error,
        elapsed_ms=elapsed_ms,
    )


# --------------------------------------------------------------------------- nội bộ


async def _read_limited(file: UploadFile) -> bytes:
    """Đọc file, dừng và trả 413 ngay khi vượt `MAX_UPLOAD_MB`."""
    limit = settings.max_upload_mb * 1024 * 1024
    chunks: list[bytes] = []
    size = 0

    while chunk := await file.read(CHUNK_SIZE):
        size += len(chunk)
        if size > limit:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Ảnh vượt quá giới hạn {settings.max_upload_mb}MB.",
            )
        chunks.append(chunk)

    if not chunks:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="File rỗng.")
    return b"".join(chunks)


def _store(data: bytes, image_hash: str) -> str:
    """Ghi ảnh đã xử lý vào volume `uploads`, trả **đường dẫn tương đối** để lưu DB.

    Tương đối chứ không tuyệt đối: `UPLOAD_DIR` là biến môi trường (`/data/uploads` trong
    Docker, thư mục khác khi chạy ngoài container) — lưu đường dẫn tuyệt đối thì đổi mount là
    toàn bộ bản ghi cũ trỏ vào hư không.

    Tên file lấy theo hash nên ghi lại đúng ảnh đó là ghi đè chính nó, không sinh file mồ côi.
    Chia thư mục con 2 ký tự đầu để một thư mục không chứa hàng chục nghìn file.
    """
    relative = Path(image_hash[:2]) / f"{image_hash}.jpg"
    target = settings.upload_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return relative.as_posix()


def _status_and_notes(
    result: ocr.OcrResult | None, error: str | None
) -> tuple[CardStatus, str | None]:
    """Trạng thái vòng đời + ghi chú cho một bản ghi vừa quét.

    `pending` = chưa quét được, còn phải quét lại. `needs_review` = đã có dữ liệu, chờ người
    duyệt (task 5.1). Không bao giờ tự nhảy sang `confirmed` — xác nhận là việc của người dùng
    (task 4.3).
    """
    if result is None:
        return CardStatus.PENDING, f"OCR chưa chạy được: {error}"
    if not result.extraction.is_business_card:
        return (
            CardStatus.NEEDS_REVIEW,
            "Model cho rằng ảnh này không phải danh thiếp — kiểm lại trước khi xác nhận.",
        )
    return CardStatus.NEEDS_REVIEW, None


def _ms_since(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
