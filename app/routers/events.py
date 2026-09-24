"""Sự kiện thu thập danh thiếp và báo cáo chuyển đổi theo sự kiện.

Chủ sở hữu: T | Task: NEXT-03 | xem Task.md

Router riêng, cùng lý do đã ghi ở `routers/contacts.py`: `routers/cards.py` của Q lo việc **số
hoá**, còn việc ở đây là gom thẻ theo nơi thu được và trả lời "hội chợ này có đáng tiền không".

Mọi route trả **404** cho sự kiện của người khác, không phải 403 (Plan.md mục 4).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import CurrentUser
from app.models.event import Event
from app.repositories import event as event_repo
from app.schemas.event import (
    AssignIn,
    AssignOut,
    EventIn,
    EventListOut,
    EventPatch,
    EventReport,
)

router = APIRouter(prefix="/api/events", tags=["events"])

Session = Annotated[AsyncSession, Depends(get_db)]
NOT_FOUND = "not found"
UNASSIGNED_LABEL = "Chưa gắn sự kiện"

EMPTY_COUNTS = (0, 0, 0, 0, 0)


def _conversion_rate(won: int, lost: int) -> float | None:
    """`None` khi chưa thẻ nào ngã ngũ — xem docstring của `EventReport`, đó không phải `0%`."""
    decided = won + lost
    return round(won / decided, 3) if decided else None


def _report(event: Event | None, counts: tuple[int, int, int, int, int]) -> EventReport:
    total, won, lost, closed_unknown, in_progress = counts
    return EventReport(
        event_id=event.id if event else None,
        name=event.name if event else UNASSIGNED_LABEL,
        starts_on=event.starts_on if event else None,
        ends_on=event.ends_on if event else None,
        is_active=bool(event and event.is_active),
        total_cards=total,
        won=won,
        lost=lost,
        closed_unknown=closed_unknown,
        in_progress=in_progress,
        conversion_rate=_conversion_rate(won, lost),
    )


async def _event(db: AsyncSession, event_id: uuid.UUID, user: CurrentUser) -> Event:
    found = await event_repo.get(db, event_id, user_id=user.id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return found


@router.get("", response_model=EventListOut)
async def list_events(db: Session, user: CurrentUser) -> EventListOut:
    """Mọi sự kiện kèm báo cáo chuyển đổi — nguồn của khối *Sự kiện* trên trang chủ."""
    events, counts = await event_repo.list_with_report(db, user_id=user.id)
    return EventListOut(
        active_event_id=next((item.id for item in events if item.is_active), None),
        items=[_report(item, counts.get(item.id, EMPTY_COUNTS)) for item in events],
        unassigned=_report(None, counts.get(None, EMPTY_COUNTS)),
    )


@router.post("", response_model=EventReport, status_code=status.HTTP_201_CREATED)
async def create_event(body: EventIn, db: Session, user: CurrentUser) -> EventReport:
    try:
        event = await event_repo.create(
            db,
            user_id=user.id,
            name=body.name,
            starts_on=body.starts_on,
            ends_on=body.ends_on,
            activate=body.activate,
        )
    except event_repo.DuplicateEventError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _report(event, EMPTY_COUNTS)


@router.patch("/{event_id}", response_model=EventReport)
async def update_event(
    event_id: uuid.UUID, body: EventPatch, db: Session, user: CurrentUser
) -> EventReport:
    """Đổi tên, đổi ngày, bật/tắt *đang diễn ra*. Bật một sự kiện sẽ tắt sự kiện đang bật."""
    event = await _event(db, event_id, user)
    if not body.model_fields_set:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Không có trường nào để sửa.")
    try:
        event = await event_repo.update_event(
            db,
            event,
            name=body.name,
            starts_on=body.starts_on,
            ends_on=body.ends_on,
            clear_starts_on=body.clears("starts_on"),
            clear_ends_on=body.clears("ends_on"),
            activate=body.activate,
        )
    except event_repo.DuplicateEventError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    _, counts = await event_repo.list_with_report(db, user_id=user.id)
    return _report(event, counts.get(event.id, EMPTY_COUNTS))


@router.delete("/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(event_id: uuid.UUID, db: Session, user: CurrentUser) -> None:
    """Xoá nhãn sự kiện. **Danh thiếp ở lại**, chỉ mất nhãn."""
    await event_repo.delete(db, await _event(db, event_id, user))


@router.post("/{event_id}/cards", response_model=AssignOut)
async def assign_cards(
    event_id: uuid.UUID, body: AssignIn, db: Session, user: CurrentUser
) -> AssignOut:
    """Gắn một loạt thẻ đã quét vào sự kiện này — dùng cho thẻ thu trước khi bật sự kiện."""
    event = await _event(db, event_id, user)
    updated = await event_repo.assign_cards(db, body.card_ids, user_id=user.id, event_id=event.id)
    return AssignOut(event_id=event.id, updated=updated)


@router.post("/unassign", response_model=AssignOut)
async def unassign_cards(body: AssignIn, db: Session, user: CurrentUser) -> AssignOut:
    """Gỡ nhãn sự kiện khỏi một loạt thẻ."""
    updated = await event_repo.assign_cards(db, body.card_ids, user_id=user.id, event_id=None)
    return AssignOut(event_id=None, updated=updated)
