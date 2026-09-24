"""Truy vấn cho vòng đời quan hệ: hẹn liên hệ lại + dòng thời gian ghi chú.

Chủ sở hữu: T | Task: NEXT-01 | xem Task.md

**Mọi hàm ở đây bắt buộc `user_id`, không có giá trị mặc định.** Cùng lối đã chốt ở 12.6: quên
truyền thì lỗi kiểu, chứ không phải rò dữ liệu của người khác.
"""

import uuid
from collections.abc import Sequence
from datetime import date
from typing import cast

from sqlalchemy import ColumnElement, Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.card import BusinessCard, RelationshipStatus
from app.models.company import Company
from app.models.contact_note import ContactNote

#: Trần số dòng trả về cho khối *Cần liên hệ hôm nay*. Trang chủ chỉ hiện vài dòng đầu; ai muốn
#: xem hết thì sang danh sách danh thiếp — trang chủ không phải chỗ cuộn 500 dòng.
DUE_LIMIT = 20


def _owned(user_id: uuid.UUID) -> Select[tuple[BusinessCard, str | None]]:
    # `outerjoin` nên tên công ty có thể `NULL`; SQLAlchemy vẫn suy ra `str`, cast cho khớp thật.
    company_name = cast(ColumnElement[str | None], Company.display_name)
    return (
        select(BusinessCard, company_name)
        .outerjoin(Company, Company.id == BusinessCard.company_id)
        .where(BusinessCard.user_id == user_id)
    )


async def get_card(
    db: AsyncSession, card_id: uuid.UUID, *, user_id: uuid.UUID
) -> BusinessCard | None:
    return await db.scalar(
        select(BusinessCard).where(BusinessCard.id == card_id, BusinessCard.user_id == user_id)
    )


async def set_follow_up(
    db: AsyncSession,
    card: BusinessCard,
    *,
    relationship_status: RelationshipStatus | None = None,
    follow_up_at: date | None = None,
    clear_follow_up: bool = False,
) -> BusinessCard:
    """Đổi trạng thái quan hệ và/hoặc ngày hẹn.

    `clear_follow_up` tách khỏi `follow_up_at=None` vì hai thứ khác nghĩa hẳn: *không gửi trường*
    là **giữ nguyên hẹn cũ**, còn *gửi null* là **xoá hẹn**. Gộp lại thì mỗi lần đổi trạng thái là
    vô tình xoá mất ngày hẹn người dùng đặt hôm trước.
    """
    values: dict[str, object] = {}
    if relationship_status is not None:
        values["relationship_status"] = relationship_status.value
    if clear_follow_up:
        values["follow_up_at"] = None
    elif follow_up_at is not None:
        values["follow_up_at"] = follow_up_at

    if values:
        await db.execute(update(BusinessCard).where(BusinessCard.id == card.id).values(**values))
        await db.commit()
        await db.refresh(card)
    return card


async def due_cards(
    db: AsyncSession, *, user_id: uuid.UUID, on: date, limit: int = DUE_LIMIT
) -> Sequence[tuple[BusinessCard, str | None]]:
    """Thẻ có hẹn **đến hạn hoặc quá hạn** tính tới ngày `on`, cũ nhất trước.

    Quá hạn đứng trước là có chủ đích: thứ trượt lịch từ tuần trước mới là thứ dễ rơi mất.
    Thẻ đã `closed` không bao giờ vào đây — quan hệ đã dừng thì lời nhắc chỉ là nhiễu.
    """
    rows = await db.execute(
        _owned(user_id)
        .where(
            BusinessCard.follow_up_at.is_not(None),
            BusinessCard.follow_up_at <= on,
            BusinessCard.relationship_status != RelationshipStatus.CLOSED.value,
        )
        .order_by(BusinessCard.follow_up_at, BusinessCard.id)
        .limit(limit)
    )
    return list(rows.tuples().all())


async def due_count(db: AsyncSession, *, user_id: uuid.UUID, on: date) -> int:
    total = await db.scalar(
        select(func.count())
        .select_from(BusinessCard)
        .where(
            BusinessCard.user_id == user_id,
            BusinessCard.follow_up_at.is_not(None),
            BusinessCard.follow_up_at <= on,
            BusinessCard.relationship_status != RelationshipStatus.CLOSED.value,
        )
    )
    return int(total or 0)


async def list_notes(
    db: AsyncSession, card_id: uuid.UUID, *, user_id: uuid.UUID
) -> Sequence[ContactNote]:
    rows = await db.scalars(
        select(ContactNote)
        .where(ContactNote.card_id == card_id, ContactNote.user_id == user_id)
        .order_by(ContactNote.created_at.desc(), ContactNote.id.desc())
    )
    return list(rows.all())


async def add_note(
    db: AsyncSession, card: BusinessCard, body: str, *, user_id: uuid.UUID
) -> ContactNote:
    note = ContactNote(user_id=user_id, card_id=card.id, body=body)
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return note


async def delete_note(db: AsyncSession, note_id: uuid.UUID, *, user_id: uuid.UUID) -> bool:
    note = await db.scalar(
        select(ContactNote).where(ContactNote.id == note_id, ContactNote.user_id == user_id)
    )
    if note is None:
        return False
    await db.delete(note)
    await db.commit()
    return True
