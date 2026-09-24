"""Schema cho việc phát hiện và gộp liên hệ trùng.

Chủ sở hữu: T | Task: NEXT-04 | xem Task.md
"""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.card import RelationshipStatus

MAX_MERGE_CARDS = 20


class DuplicateCard(BaseModel):
    """Đủ để người dùng nhìn là biết nên giữ thẻ nào, không phải cả bản ghi."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str | None = None
    full_name_vi: str | None = None
    company_name_raw: str | None = None
    company_name_vi: str | None = None
    job_title: str | None = None
    email: str | None = None
    phone: str | None = None
    relationship_status: RelationshipStatus = RelationshipStatus.NEW
    follow_up_at: date | None = None
    uploaded_at: datetime
    #: Số ô có dữ liệu — giao diện gợi ý giữ thẻ đầy đặn nhất làm thẻ chính.
    filled_fields: int = 0


class DuplicateReasonOut(BaseModel):
    kind: str = Field(description="email | phone")
    value: str


class DuplicateGroupOut(BaseModel):
    """Một nhóm thẻ dùng chung ít nhất một giá trị."""

    reasons: list[DuplicateReasonOut] = Field(
        default_factory=list,
        description="Nhiều hơn một lý do = bằng chứng mạnh hơn (trùng cả email lẫn số).",
    )
    same_name: bool = Field(description="Tên mọi thẻ chuẩn hoá về cùng một chuỗi.")
    likely_shared: bool = Field(
        description="Nhiều thẻ dùng chung giá trị này — nhiều khả năng là số tổng đài "
        "hoặc hộp thư chung của công ty, không phải một người bị quét hai lần."
    )
    cards: list[DuplicateCard] = Field(default_factory=list)


class DuplicateListOut(BaseModel):
    total_groups: int = 0
    items: list[DuplicateGroupOut] = Field(default_factory=list)


class MergeIn(BaseModel):
    primary_id: uuid.UUID
    duplicate_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_MERGE_CARDS)

    @model_validator(mode="after")
    def primary_is_not_a_duplicate(self) -> "MergeIn":
        if self.primary_id in self.duplicate_ids:
            raise ValueError("Thẻ chính không thể vừa là bản trùng của chính nó.")
        if len(set(self.duplicate_ids)) != len(self.duplicate_ids):
            raise ValueError("Danh sách bản trùng có id lặp.")
        return self


class UnmergeIn(BaseModel):
    card_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_MERGE_CARDS)


class MergeOut(BaseModel):
    primary_id: uuid.UUID
    merged: int
    #: Chunk Knowledge Base của bản trùng đã gỡ. Không gỡ thì trợ lý AI vẫn trích dẫn một liên hệ
    #: người dùng tưởng đã gộp đi rồi.
    kb_chunks_removed: int = 0


class UnmergeOut(BaseModel):
    restored: int
    #: `False` khi gỡ gộp xong mà không nạp lại được Knowledge Base (embedder hỏng chẳng hạn).
    #: Nói thẳng ra chứ không im lặng: thẻ khôi phục mà trợ lý không biết là một lỗ hổng lặng lẽ,
    #: và người dùng chạy `POST /api/kb/reindex` là xong.
    kb_reindexed: bool = True
