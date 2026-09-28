"""Phát hiện và gộp liên hệ trùng.

Chủ sở hữu: T | Task: NEXT-04

**Máy chỉ gợi ý, người quyết định.** Không có đường nào ở đây tự gộp: trùng số tổng đài công ty
hay trùng địa chỉ `info@` là chuyện thường, mà hai thứ đó trông y hệt hai bản ghi của cùng một
người.

Gộp là **gộp mềm**: bản trùng ở lại, mang `merged_into_id`, biến khỏi mọi danh sách nhưng vẫn mở
ra đọc được và gỡ gộp được.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.card import BusinessCard, RelationshipStatus
from app.models.contact_note import ContactNote
from app.services.normalize_company import normalize_label

#: Trần số nhóm trả về. Danh sách dài hơn thế thì vấn đề không còn là gộp từng nhóm nữa.
MAX_GROUPS = 50

#: Một giá trị bị dùng chung bởi nhiều thẻ hơn mức này gần như chắc chắn là **số tổng đài hoặc
#: hộp thư chung của công ty**. Vẫn hiện, nhưng có cờ để giao diện cảnh báo trước khi gộp.
LIKELY_SHARED_FROM = 4

#: Cột được lấp từ bản trùng khi ô tương ứng của thẻ chính còn trống. Khai tường minh chứ không
#: mượn `card_repo.OCR_COLUMNS` — hai việc khác nhau thì không nên dính số phận vào nhau.
MERGE_FIELDS: tuple[str, ...] = (
    "full_name",
    "full_name_vi",
    "job_title",
    "job_title_vi",
    "company_name_raw",
    "company_name_vi",
    "email",
    "phone",
    "phone_alt",
    "address",
    "address_vi",
    "website",
    "language_detected",
    "notes",
)

#: Thứ tự "xa" của vòng đời quan hệ, dùng khi gộp. `won` xếp trên `lost`/`closed`: mất một quan
#: hệ **đã chốt** vì thao tác gộp là lỗi tệ hơn hẳn việc giữ một trạng thái lạc quan hơn thực tế.
STAGE_RANK: dict[str, int] = {
    RelationshipStatus.NEW: 0,
    RelationshipStatus.CONTACTED: 1,
    RelationshipStatus.TALKING: 2,
    RelationshipStatus.CLOSED: 3,
    RelationshipStatus.LOST: 3,
    RelationshipStatus.WON: 4,
}


@dataclass(frozen=True)
class DuplicateReason:
    """Vì sao mấy thẻ này bị xếp chung: trùng giá trị nào, ở cột nào."""

    kind: str  # "email" | "phone"
    value: str


@dataclass(frozen=True)
class DuplicateGroup:
    """Một nhóm thẻ dùng chung ít nhất một giá trị đã chuẩn hoá.

    `reasons` có thể nhiều hơn một: trùng **cả** email lẫn số điện thoại là bằng chứng mạnh hơn
    hẳn. Gộp vào cùng một khối chứ không tách đôi thành hai khối giống hệt nhau.
    """

    reasons: list[DuplicateReason]
    cards: list[BusinessCard]

    @property
    def same_name(self) -> bool:
        """Tên của mọi thẻ trong nhóm chuẩn hoá về cùng một chuỗi.

        Tín hiệu phân biệt *một người bị quét hai lần* với *cả phòng dùng chung một số*. Thẻ
        không có tên thì không tính là khớp — trống không phải là bằng chứng.
        """
        keys = set()
        for card in self.cards:
            name = card.full_name_vi or card.full_name
            if not name:
                return False
            try:
                keys.add(normalize_label(name))
            except ValueError:
                return False
        return len(keys) == 1

    @property
    def likely_shared(self) -> bool:
        return len(self.cards) >= LIKELY_SHARED_FROM


def live_cards(workspace_id: uuid.UUID) -> Select[tuple[BusinessCard]]:
    """Thẻ **chưa bị gộp** của một người dùng — nền của mọi câu trong file này."""
    return select(BusinessCard).where(
        BusinessCard.workspace_id == workspace_id, BusinessCard.merged_into_id.is_(None)
    )


async def _shared_values(db: AsyncSession, workspace_id: uuid.UUID, column: Any) -> list[str]:
    rows = await db.scalars(
        select(column)
        .where(
            BusinessCard.workspace_id == workspace_id,
            BusinessCard.merged_into_id.is_(None),
            column.is_not(None),
            column != "",
        )
        .group_by(column)
        .having(func.count() > 1)
        .order_by(func.count().desc(), column)
        .limit(MAX_GROUPS)
    )
    return list(rows.all())


async def find_groups(db: AsyncSession, *, workspace_id: uuid.UUID) -> list[DuplicateGroup]:
    """Nhóm thẻ trùng theo email hoặc số điện thoại **đã chuẩn hoá**.

    Chuẩn hoá đã xong từ lúc quét nên ở đây so thẳng giá trị trong cột — không chuẩn hoá lại, để
    hai chỗ không bao giờ lệch luật với nhau.

    Hai nhóm có **đúng cùng một tập thẻ** được gộp làm một, giữ cả hai lý do: đó là ca trùng cả
    email lẫn số điện thoại. Cố ý **không** gom thành cụm liên thông khi các tập chỉ chồng lấn
    một phần — gom lại thì người dùng nhìn thấy một đống sáu thẻ mà không biết vì sao chúng dính.
    """
    found: dict[frozenset[uuid.UUID], tuple[list[DuplicateReason], list[BusinessCard]]] = {}
    for kind, column in (("email", BusinessCard.email), ("phone", BusinessCard.phone)):
        values = await _shared_values(db, workspace_id, column)
        if not values:
            continue
        rows = await db.scalars(
            live_cards(workspace_id)
            .where(column.in_(values))
            .order_by(column, BusinessCard.uploaded_at, BusinessCard.id)
        )
        by_value: dict[str, list[BusinessCard]] = {}
        for card in rows.all():
            by_value.setdefault(getattr(card, kind), []).append(card)

        for value, cards in by_value.items():
            if len(cards) < 2:
                continue
            key = frozenset(card.id for card in cards)
            reasons, _ = found.setdefault(key, ([], cards))
            reasons.append(DuplicateReason(kind=kind, value=value))

    groups = [DuplicateGroup(reasons=reasons, cards=cards) for reasons, cards in found.values()]
    # Bằng chứng nhiều hơn xếp trước, rồi tới nhóm đông thẻ hơn: thứ chắc chắn nhất nằm trên cùng.
    groups.sort(key=lambda group: (len(group.reasons), len(group.cards)), reverse=True)
    return groups[:MAX_GROUPS]


async def get_live(
    db: AsyncSession, card_id: uuid.UUID, *, workspace_id: uuid.UUID
) -> BusinessCard | None:
    return await db.scalar(live_cards(workspace_id).where(BusinessCard.id == card_id))


async def merged_into(
    db: AsyncSession, card_id: uuid.UUID, *, workspace_id: uuid.UUID
) -> Sequence[BusinessCard]:
    """Những thẻ đã gộp vào thẻ này — câu duy nhất dùng tới index partial của `0010`."""
    rows = await db.scalars(
        select(BusinessCard)
        .where(BusinessCard.workspace_id == workspace_id, BusinessCard.merged_into_id == card_id)
        .order_by(BusinessCard.uploaded_at)
    )
    return list(rows.all())


def _best_stage(cards: Sequence[BusinessCard]) -> str:
    return max((card.relationship_status for card in cards), key=lambda s: STAGE_RANK.get(s, 0))


def _earliest(values: Sequence[date | None]) -> date | None:
    known = [value for value in values if value is not None]
    return min(known) if known else None


async def merge(
    db: AsyncSession,
    primary: BusinessCard,
    duplicates: Sequence[BusinessCard],
    *,
    workspace_id: uuid.UUID,
) -> BusinessCard:
    """Gộp `duplicates` vào `primary`. Trả về thẻ chính đã cập nhật.

    Luật gộp, mỗi dòng chọn theo hướng **mất dữ liệu là lỗi nặng hơn giữ thừa**:

    - Ô nào của thẻ chính còn trống thì lấp từ bản trùng; ô đã có thì **không đụng tới**.
    - `relationship_status` lấy trạng thái **xa nhất** (xem `STAGE_RANK`).
    - `follow_up_at` lấy hẹn **sớm nhất** — bỏ sót một lời nhắc tệ hơn nhắc sớm một ngày.
    - `contact_notes` của bản trùng **chuyển sang thẻ chính**.
    - Bản trùng không mất gì: nó chỉ mang `merged_into_id` và biến khỏi các danh sách.
    """
    ordered = sorted(duplicates, key=lambda card: card.uploaded_at)
    values: dict[str, Any] = {}

    for field in MERGE_FIELDS:
        if getattr(primary, field):
            continue
        filler = next((getattr(card, field) for card in ordered if getattr(card, field)), None)
        if filler is not None:
            values[field] = filler

    for field in ("company_id",):
        if getattr(primary, field) is None:
            filler = next(
                (getattr(card, field) for card in ordered if getattr(card, field) is not None),
                None,
            )
            if filler is not None:
                values[field] = filler

    stage = _best_stage([primary, *ordered])
    if stage != primary.relationship_status:
        values["relationship_status"] = stage

    follow_up = _earliest([primary.follow_up_at, *(card.follow_up_at for card in ordered)])
    if follow_up != primary.follow_up_at:
        values["follow_up_at"] = follow_up

    if values:
        await db.execute(update(BusinessCard).where(BusinessCard.id == primary.id).values(**values))

    duplicate_ids = [card.id for card in ordered]
    await db.execute(
        update(ContactNote)
        .where(ContactNote.workspace_id == workspace_id, ContactNote.card_id.in_(duplicate_ids))
        .values(card_id=primary.id)
    )
    await db.execute(
        update(BusinessCard)
        .where(BusinessCard.workspace_id == workspace_id, BusinessCard.id.in_(duplicate_ids))
        .values(merged_into_id=primary.id)
    )
    await db.commit()
    await db.refresh(primary)
    return primary


async def unmerge(
    db: AsyncSession, card_ids: Sequence[uuid.UUID], *, workspace_id: uuid.UUID
) -> Sequence[BusinessCard]:
    """Gỡ gộp: thẻ quay lại làm thẻ độc lập. Trả về những thẻ thật sự đổi.

    **Không kéo ghi chú về lại**: sau khi gộp, dòng thời gian là của một quan hệ đã hợp nhất, và
    đoán xem ghi chú nào vốn thuộc thẻ nào là đoán mò.
    """
    rows = await db.scalars(
        select(BusinessCard).where(
            BusinessCard.workspace_id == workspace_id,
            BusinessCard.id.in_(card_ids),
            BusinessCard.merged_into_id.is_not(None),
        )
    )
    restored = list(rows.all())
    if not restored:
        return []

    await db.execute(
        update(BusinessCard)
        .where(BusinessCard.id.in_([card.id for card in restored]))
        .values(merged_into_id=None)
    )
    await db.commit()
    for card in restored:
        await db.refresh(card)
    return restored
