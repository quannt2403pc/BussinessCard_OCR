"""Truy vấn cho sự kiện thu thập danh thiếp và báo cáo theo sự kiện.

Chủ sở hữu: T | Task: NEXT-03 | xem Task.md

**Mọi hàm ở đây bắt buộc `user_id`, không có giá trị mặc định** — cùng lối đã chốt ở 12.6 và
dùng lại ở `repositories/contact.py`: quên truyền thì lỗi kiểu, chứ không phải rò dữ liệu.
"""

import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any, cast

from sqlalchemy import ColumnElement, CursorResult, Select, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.card import BusinessCard, RelationshipStatus
from app.models.event import Event
from app.services.normalize_company import normalize_label


class DuplicateEventError(ValueError):
    """Người dùng đã có một sự kiện trùng tên sau khi chuẩn hoá."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Đã có sự kiện tên {name!r}.")
        self.name = name


async def get(db: AsyncSession, event_id: uuid.UUID, *, user_id: uuid.UUID) -> Event | None:
    return await db.scalar(select(Event).where(Event.id == event_id, Event.user_id == user_id))


async def active_event_id(db: AsyncSession, user_id: uuid.UUID) -> uuid.UUID | None:
    """Sự kiện đang diễn ra của người dùng, hoặc `None` nếu không bật cái nào.

    Index partial `ix_events_one_active` đảm bảo nhiều nhất một dòng, nên câu này không cần
    `ORDER BY` để khỏi phụ thuộc vào thứ tự ngẫu nhiên của Postgres.
    """
    return await db.scalar(
        select(Event.id).where(Event.user_id == user_id, Event.is_active.is_(True))
    )


async def create(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    name: str,
    starts_on: date | None = None,
    ends_on: date | None = None,
    activate: bool = False,
) -> Event:
    """Tạo một sự kiện. Ném `DuplicateEventError` nếu người này đã có sự kiện trùng tên.

    Bắt `IntegrityError` chứ không chỉ kiểm trước: hai tab cùng bấm *Tạo* trong một giây lọt
    qua được bước kiểm, chỉ ràng buộc unique trong DB mới là thật — cùng lý lẽ với
    `card_repo.create_card()`.
    """
    if activate:
        await _clear_active(db, user_id)

    event = Event(
        user_id=user_id,
        name=name.strip(),
        name_normalized=normalize_label(name),
        starts_on=starts_on,
        ends_on=ends_on,
        is_active=activate,
    )
    db.add(event)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise DuplicateEventError(name) from exc
    await db.refresh(event)
    return event


async def update_event(
    db: AsyncSession,
    event: Event,
    *,
    name: str | None = None,
    starts_on: date | None = None,
    ends_on: date | None = None,
    clear_starts_on: bool = False,
    clear_ends_on: bool = False,
    activate: bool | None = None,
) -> Event:
    """Sửa một sự kiện.

    `clear_*` tách khỏi `None` vì hai thứ khác nghĩa: *không gửi trường* là giữ nguyên, *gửi
    null* là xoá — y như `set_follow_up()` của `NEXT-01`.
    """
    values: dict[str, object] = {}
    if name is not None:
        values["name"] = name.strip()
        values["name_normalized"] = normalize_label(name)
    if clear_starts_on:
        values["starts_on"] = None
    elif starts_on is not None:
        values["starts_on"] = starts_on
    if clear_ends_on:
        values["ends_on"] = None
    elif ends_on is not None:
        values["ends_on"] = ends_on

    if activate is True:
        # Tắt cái đang bật **trước** khi bật cái mới, nếu không index unique partial sẽ chặn.
        await _clear_active(db, event.user_id, except_id=event.id)
        values["is_active"] = True
    elif activate is False:
        values["is_active"] = False

    if values:
        try:
            await db.execute(update(Event).where(Event.id == event.id).values(**values))
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise DuplicateEventError(str(values.get("name", event.name))) from exc
        await db.refresh(event)
    return event


async def delete(db: AsyncSession, event: Event) -> None:
    """Xoá nhãn sự kiện. Danh thiếp **ở lại**, `event_id` về `NULL` (khoá ngoại `SET NULL`)."""
    await db.delete(event)
    await db.commit()


async def assign_cards(
    db: AsyncSession,
    card_ids: Sequence[uuid.UUID],
    *,
    user_id: uuid.UUID,
    event_id: uuid.UUID | None,
) -> int:
    """Gắn (hoặc gỡ, khi `event_id` là `None`) nhãn sự kiện cho một loạt thẻ. Trả số dòng đổi.

    Lọc `user_id` ngay trong `WHERE`: id của người khác lọt vào danh sách thì đơn giản là không
    khớp dòng nào, không cần kiểm trước và cũng không có đường nào sửa nhầm dữ liệu của họ.
    """
    if not card_ids:
        return 0
    # `execute()` khai kiểu trả về là `Result`, nhưng một câu UPDATE luôn trả `CursorResult` —
    # chỉ lớp đó mới có `rowcount`, thứ cho biết bao nhiêu thẻ thật sự đổi nhãn.
    result = cast(
        CursorResult[Any],
        await db.execute(
            update(BusinessCard)
            .where(BusinessCard.user_id == user_id, BusinessCard.id.in_(card_ids))
            .values(event_id=event_id)
        ),
    )
    await db.commit()
    return int(result.rowcount or 0)


async def _clear_active(
    db: AsyncSession, user_id: uuid.UUID, *, except_id: uuid.UUID | None = None
) -> None:
    stmt = update(Event).where(Event.user_id == user_id, Event.is_active.is_(True))
    if except_id is not None:
        stmt = stmt.where(Event.id != except_id)
    await db.execute(stmt.values(is_active=False))


# --------------------------------------------------------------------------- báo cáo


def _stage_count(stage: RelationshipStatus) -> ColumnElement[int]:
    return func.count(BusinessCard.id).filter(BusinessCard.relationship_status == stage.value)


def _report_select(user_id: uuid.UUID) -> Select[tuple[uuid.UUID | None, int, int, int, int, int]]:
    """Đếm thẻ theo từng kết cục, gộp theo sự kiện — **một lượt quét bảng cho tất cả sự kiện**.

    Đếm bằng `FILTER` thay vì chạy sáu câu riêng: sáu câu là sáu lần quét cùng một bảng để trả
    lời cùng một câu hỏi.
    """
    return select(
        BusinessCard.event_id,
        func.count(BusinessCard.id).label("total"),
        _stage_count(RelationshipStatus.WON).label("won"),
        _stage_count(RelationshipStatus.LOST).label("lost"),
        _stage_count(RelationshipStatus.CLOSED).label("closed_unknown"),
        func.count(BusinessCard.id)
        .filter(
            BusinessCard.relationship_status.in_(
                (RelationshipStatus.CONTACTED.value, RelationshipStatus.TALKING.value)
            )
        )
        .label("in_progress"),
    ).where(
        BusinessCard.user_id == user_id,
        # Đếm cả bản trùng đã gộp là thổi phồng đúng con số mà `NEXT-04` sinh ra để dọn.
        BusinessCard.merged_into_id.is_(None),
    )


async def list_with_report(
    db: AsyncSession, *, user_id: uuid.UUID
) -> tuple[Sequence[Event], dict[uuid.UUID | None, tuple[int, int, int, int, int]]]:
    """Mọi sự kiện của người dùng, kèm bảng đếm theo `event_id`.

    Khoá `None` của bảng đếm là **thẻ chưa gắn sự kiện nào** — vẫn trả về, vì giấu nó đi thì
    tổng các sự kiện không bằng tổng số thẻ và người đọc báo cáo sẽ đi tìm chỗ sai.
    """
    events = list(
        (
            await db.scalars(
                select(Event)
                .where(Event.user_id == user_id)
                .order_by(Event.starts_on.desc().nullslast(), Event.created_at.desc())
            )
        ).all()
    )
    rows = await db.execute(_report_select(user_id).group_by(BusinessCard.event_id))
    counts = {
        row.event_id: (row.total, row.won, row.lost, row.closed_unknown, row.in_progress)
        for row in rows
    }
    return events, counts
