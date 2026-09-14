"""Repository business_cards.

Chủ sở hữu: Q | Task: 3.5 | xem Task.md

Lớp mỏng giữa router và ORM: router lo HTTP, file này lo câu truy vấn. Mục đích thật là để
`routers/cards.py` không phình ra khi thêm danh sách/lọc/phân trang ở task 4.1.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.card import BusinessCard, CardStatus

logger = logging.getLogger(__name__)

#: Cột được phép ghi từ kết quả OCR. Danh sách trắng, không phải `setattr` tuỳ ý: dữ liệu này do
#: một model ngôn ngữ sinh ra, để nó đặt được `status` hay `company_id` là mở cửa cho chính nó
#: quyết định vòng đời bản ghi.
OCR_COLUMNS: frozenset[str] = frozenset(
    {
        "full_name",
        "job_title",
        "company_name_raw",
        "email",
        "phone",
        "phone_alt",
        "address",
        "website",
        "language_detected",
        "confidence",
    }
)


class DuplicateImageError(RuntimeError):
    """Ảnh này đã có trong DB (`image_hash` trùng). Router trả lại bản ghi cũ (task 3.1)."""

    def __init__(self, image_hash: str) -> None:
        super().__init__(f"Ảnh đã được quét trước đó (hash {image_hash[:12]}…).")
        self.image_hash = image_hash


async def get(db: AsyncSession, card_id: uuid.UUID) -> BusinessCard | None:
    return await db.get(BusinessCard, card_id)


async def get_by_hash(db: AsyncSession, image_hash: str) -> BusinessCard | None:
    """Tra theo SHA-256 của file gốc — chống upload trùng (task 3.1)."""
    result = await db.execute(select(BusinessCard).where(BusinessCard.image_hash == image_hash))
    return result.scalar_one_or_none()


async def create_card(
    db: AsyncSession,
    *,
    image_path: str,
    image_hash: str,
    fields: Mapping[str, Any] | None = None,
    ocr_raw_json: dict[str, Any] | None = None,
    status: CardStatus = CardStatus.PENDING,
    notes: str | None = None,
) -> BusinessCard:
    """Tạo một bản ghi danh thiếp và commit.

    Ném `DuplicateImageError` khi `image_hash` đã tồn tại. Router có kiểm trước rồi, nhưng vẫn
    bắt ở đây vì hai lần upload cùng lúc (batch, task 5.2) lọt qua được bước kiểm đó — chỉ ràng
    buộc unique trong DB mới là thật.
    """
    card = BusinessCard(
        image_path=image_path,
        image_hash=image_hash,
        ocr_raw_json=ocr_raw_json,
        status=status,
        notes=notes,
    )
    for name, value in (fields or {}).items():
        if name in OCR_COLUMNS:
            setattr(card, name, value)

    db.add(card)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if _is_duplicate_hash(exc):
            raise DuplicateImageError(image_hash) from exc
        raise

    # Lấy về `uploaded_at`/`created_at` do PostgreSQL sinh (`server_default`).
    await db.refresh(card)
    return card


async def update_fields(
    db: AsyncSession,
    card: BusinessCard,
    fields: Mapping[str, Any],
    *,
    status: CardStatus | None = None,
    notes: str | None = None,
) -> BusinessCard:
    """Ghi đè các cột OCR của một bản ghi đã có (quét lại, hoặc người dùng sửa ở task 4.2)."""
    for name, value in fields.items():
        if name in OCR_COLUMNS:
            setattr(card, name, value)
    if status is not None:
        card.status = status
    if notes is not None:
        card.notes = notes

    await db.commit()
    await db.refresh(card)
    return card


def _is_duplicate_hash(exc: IntegrityError) -> bool:
    """Phân biệt vi phạm unique `image_hash` với mọi lỗi toàn vẹn khác.

    Nuốt nhầm một `IntegrityError` khác (khoá ngoại `company_id` chẳng hạn) rồi báo "ảnh trùng"
    sẽ khiến người dùng đi tìm một bản ghi cũ không hề tồn tại.
    """
    return "ix_business_cards_image_hash" in str(exc.orig) or "image_hash" in str(exc.orig)
