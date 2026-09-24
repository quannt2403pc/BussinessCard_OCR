"""Phát hiện và gộp liên hệ trùng.

Chủ sở hữu: T | Task: NEXT-04 | xem Task.md

**Không có đường nào tự gộp.** Endpoint duy nhất làm thay đổi dữ liệu là `POST /merge`, và nó
đòi người dùng chỉ đích danh thẻ chính lẫn từng bản trùng. Máy chỉ trả về gợi ý kèm **lý do**
(trùng giá trị nào) và cờ cảnh báo khi giá trị ấy trông giống số tổng đài hơn là một người.

Mọi route trả **404** cho thẻ của người khác, không phải 403 (Plan.md mục 4).
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import CurrentUser
from app.models.card import BusinessCard
from app.models.kb import KBSourceType
from app.repositories import duplicate as duplicate_repo
from app.repositories import kb as kb_repo
from app.schemas.duplicate import (
    DuplicateCard,
    DuplicateGroupOut,
    DuplicateListOut,
    DuplicateReasonOut,
    MergeIn,
    MergeOut,
    UnmergeIn,
    UnmergeOut,
)
from app.services import kb as kb_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/duplicates", tags=["duplicates"])

Session = Annotated[AsyncSession, Depends(get_db)]
NOT_FOUND = "not found"

#: Những ô được đếm để gợi ý "thẻ này đầy đặn nhất, giữ làm thẻ chính".
COUNTED_FIELDS = duplicate_repo.MERGE_FIELDS


def _card_out(card: BusinessCard) -> DuplicateCard:
    out = DuplicateCard.model_validate(card)
    out.filled_fields = sum(1 for field in COUNTED_FIELDS if getattr(card, field))
    return out


@router.get("", response_model=DuplicateListOut)
async def list_duplicates(db: Session, user: CurrentUser) -> DuplicateListOut:
    """Các nhóm thẻ trùng email hoặc số điện thoại — nguồn của khối *Liên hệ trùng*."""
    groups = await duplicate_repo.find_groups(db, user_id=user.id)
    return DuplicateListOut(
        total_groups=len(groups),
        items=[
            DuplicateGroupOut(
                reasons=[
                    DuplicateReasonOut(kind=reason.kind, value=reason.value)
                    for reason in group.reasons
                ],
                same_name=group.same_name,
                likely_shared=group.likely_shared,
                cards=[_card_out(card) for card in group.cards],
            )
            for group in groups
        ],
    )


@router.post("/merge", response_model=MergeOut)
async def merge_duplicates(body: MergeIn, db: Session, user: CurrentUser) -> MergeOut:
    """Gộp các bản trùng vào một thẻ chính do **người dùng chỉ định**.

    Thẻ nào không còn sống (đã bị gộp trước đó, hoặc của người khác) thì trả **404** kèm tên
    trường — gộp một nửa rồi báo lỗi là trạng thái khó gỡ hơn hẳn việc không gộp gì cả.
    """
    primary = await duplicate_repo.get_live(db, body.primary_id, user_id=user.id)
    if primary is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)

    duplicates = []
    for card_id in body.duplicate_ids:
        card = await duplicate_repo.get_live(db, card_id, user_id=user.id)
        if card is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
        duplicates.append(card)

    # Gỡ KB **trước** khi gộp và trong cùng lượt: bỏ bước này thì chunk của bản trùng ở lại và
    # trợ lý vẫn trích dẫn một liên hệ người dùng tưởng đã gộp đi rồi — cùng lỗi mà `4.2` của Q
    # đã chặn khi xoá thẻ.
    removed = 0
    for card in duplicates:
        removed += await kb_repo.delete_for_source(
            db, user_id=user.id, source_type=KBSourceType.CARD, source_id=card.id
        )

    await duplicate_repo.merge(db, primary, duplicates, user_id=user.id)
    logger.info("Đã gộp %d thẻ vào %s", len(duplicates), primary.id)
    return MergeOut(primary_id=primary.id, merged=len(duplicates), kb_chunks_removed=removed)


@router.post("/unmerge", response_model=UnmergeOut)
async def unmerge_duplicates(body: UnmergeIn, db: Session, user: CurrentUser) -> UnmergeOut:
    """Trả các thẻ đã gộp về làm thẻ độc lập, và nạp lại chúng vào Knowledge Base."""
    restored = await duplicate_repo.unmerge(db, body.card_ids, user_id=user.id)
    if not restored:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)

    reindexed = True
    for card in restored:
        try:
            await kb_service.ingest_card(db, card)
        except Exception as exc:  # embedder có thể đang tắt — xem docstring của `UnmergeOut`
            reindexed = False
            logger.warning("Gỡ gộp %s xong nhưng chưa nạp lại được KB: %s", card.id, exc)
    return UnmergeOut(restored=len(restored), kb_reindexed=reindexed)
