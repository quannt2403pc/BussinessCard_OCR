"""Theo dõi quan hệ với người trên danh thiếp: trạng thái, ngày hẹn, ghi chú.

Chủ sở hữu: T | Task: NEXT-01 | xem Task.md

Router riêng chứ không nhét vào `routers/cards.py`: file đó của Q và đã dài 800 dòng lo việc
**số hoá** (upload, OCR, review, xác nhận). Việc ở đây bắt đầu *sau* khi số hoá xong và có vòng
đời riêng, nên tách ra thì mỗi file còn đúng một lý do để đổi.

Mọi route trả **404** cho thẻ của người khác, không phải 403 — 403 là tự khai rằng bản ghi đó
tồn tại (Plan.md mục 4, cùng luật với 12.6).
"""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import CurrentUser
from app.core.workspace import CurrentWorkspace, WriterWorkspace, membership
from app.models.card import BusinessCard, RelationshipStatus
from app.repositories import contact as contact_repo
from app.repositories import user as user_repo
from app.schemas.contact import (
    RELATIONSHIP_LABELS,
    DueContact,
    DueListOut,
    FollowUpIn,
    FollowUpOut,
    NoteIn,
    NoteOut,
)

router = APIRouter(prefix="/api/contacts", tags=["contacts"])

Session = Annotated[AsyncSession, Depends(get_db)]
NOT_FOUND = "not found"


async def _card(db: AsyncSession, card_id: uuid.UUID, workspace: CurrentWorkspace) -> BusinessCard:
    card = await contact_repo.get_card(db, card_id, workspace_id=workspace.id)
    if card is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return card


def _label(value: str) -> str:
    return RELATIONSHIP_LABELS[RelationshipStatus(value)]


async def _follow_up_out(db: AsyncSession, card: BusinessCard) -> FollowUpOut:
    notes = await contact_repo.list_notes(db, card.id, workspace_id=card.workspace_id)
    return FollowUpOut(
        card_id=card.id,
        relationship_status=RelationshipStatus(card.relationship_status),
        relationship_label=_label(card.relationship_status),
        follow_up_at=card.follow_up_at,
        assigned_to_user_id=card.assigned_to_user_id,
        assigned_to_name=await _assignee_name(db, card.assigned_to_user_id),
        notes=[NoteOut.model_validate(note) for note in notes],
    )


async def _assignee_name(db: AsyncSession, user_id: uuid.UUID | None) -> str | None:
    if user_id is None:
        return None
    assignee = await user_repo.get_by_id(db, user_id)
    return None if assignee is None else (assignee.display_name or assignee.email)


@router.get("/due", response_model=DueListOut)
async def due_contacts(
    db: Session,
    user: CurrentUser,
    workspace: CurrentWorkspace,
    on: Annotated[date | None, Query(description="Mặc định: hôm nay")] = None,
) -> DueListOut:
    """Liên hệ **đến hạn hoặc quá hạn** — nguồn của khối *Cần liên hệ hôm nay* trên trang chủ.

    Quá hạn nằm trên cùng: thứ trượt lịch từ tuần trước mới là thứ dễ rơi mất. Thẻ đã `closed`
    không bao giờ vào danh sách này.
    """
    today = on or date.today()
    items: list[DueContact] = []
    for card, company_name in await contact_repo.due_cards(db, workspace_id=workspace.id, on=today):
        if card.follow_up_at is None:  # không xảy ra: truy vấn đã lọc, ở đây để kiểu khỏi lỏng
            continue
        items.append(
            DueContact(
                card_id=card.id,
                display_name=card.full_name_vi or card.full_name or "(chưa có tên)",
                company_name=company_name or card.company_name_vi or card.company_name_raw,
                relationship_status=RelationshipStatus(card.relationship_status),
                relationship_label=_label(card.relationship_status),
                follow_up_at=card.follow_up_at,
                overdue_days=(today - card.follow_up_at).days,
            )
        )
    return DueListOut(
        on=today,
        total=await contact_repo.due_count(db, workspace_id=workspace.id, on=today),
        items=items,
    )


@router.get("/{card_id}", response_model=FollowUpOut)
async def get_follow_up(
    card_id: uuid.UUID, db: Session, user: CurrentUser, workspace: CurrentWorkspace
) -> FollowUpOut:
    return await _follow_up_out(db, await _card(db, card_id, workspace))


@router.patch("/{card_id}", response_model=FollowUpOut)
async def update_follow_up(
    card_id: uuid.UUID,
    body: FollowUpIn,
    db: Session,
    user: CurrentUser,
    workspace: WriterWorkspace,
) -> FollowUpOut:
    """Đổi trạng thái quan hệ, ngày hẹn, người phụ trách. Gửi `null` là **xoá** trường đó.

    Người phụ trách phải là **thành viên của chính không gian này** (`NEXT-05`). Không kiểm thì
    một id bất kỳ ghi được xuống cột ấy: liên hệ hiện ra là "đang giao cho ai đó", nhưng người
    ấy không mở nổi nó, và không ai trong tổ chức hiểu vì sao.
    """
    card = await _card(db, card_id, workspace)
    if not body.model_fields_set:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Không có trường nào để sửa.")
    if body.assigned_to_user_id is not None and not await membership(
        db, user_id=body.assigned_to_user_id, workspace_id=workspace.id
    ):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="Người phụ trách phải là thành viên của không gian làm việc này.",
        )
    card = await contact_repo.set_follow_up(
        db,
        card,
        relationship_status=body.relationship_status,
        follow_up_at=body.follow_up_at,
        clear_follow_up=body.clears_follow_up(),
        assigned_to_user_id=body.assigned_to_user_id,
        clear_assignee=body.clears_assignee(),
    )
    return await _follow_up_out(db, card)


@router.post("/{card_id}/notes", response_model=NoteOut, status_code=status.HTTP_201_CREATED)
async def add_note(
    card_id: uuid.UUID, body: NoteIn, db: Session, user: CurrentUser, workspace: WriterWorkspace
) -> NoteOut:
    card = await _card(db, card_id, workspace)
    note = await contact_repo.add_note(
        db, card, body.body, workspace_id=workspace.id, user_id=user.id
    )
    return NoteOut.model_validate(note)


@router.delete("/{card_id}/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(
    card_id: uuid.UUID,
    note_id: uuid.UUID,
    db: Session,
    user: CurrentUser,
    workspace: WriterWorkspace,
) -> None:
    await _card(db, card_id, workspace)
    if not await contact_repo.delete_note(db, note_id, workspace_id=workspace.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
