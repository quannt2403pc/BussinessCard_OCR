"""Schema cho vòng đời quan hệ: hẹn liên hệ lại + ghi chú theo dõi.

Chủ sở hữu: T | Task: NEXT-01 | xem Task.md
"""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.card import RelationshipStatus

NOTE_MAX_LENGTH = 2000

RELATIONSHIP_LABELS: dict[RelationshipStatus, str] = {
    RelationshipStatus.NEW: "Mới",
    RelationshipStatus.CONTACTED: "Đã liên hệ",
    RelationshipStatus.TALKING: "Đang trao đổi",
    RelationshipStatus.WON: "Đã chốt",
    RelationshipStatus.LOST: "Không thành",
    # Giá trị cũ trước `NEXT-03`. Giao diện không mời chọn nó nữa, nhưng vẫn phải có nhãn: thẻ
    # đánh dấu từ hôm qua mà hiện ra chuỗi `closed` trần thì người dùng tưởng hỏng.
    RelationshipStatus.CLOSED: "Đã dừng (không rõ kết cục)",
}


class FollowUpIn(BaseModel):
    """Body của `PATCH`. **Trường không gửi thì giữ nguyên.**

    `follow_up_at: null` nghĩa là *xoá hẹn*, khác hẳn *không gửi trường*. Hai thứ đó phân biệt
    bằng `model_fields_set`, nên đổi trạng thái không bao giờ vô tình xoá mất ngày hẹn.
    """

    relationship_status: RelationshipStatus | None = None
    follow_up_at: date | None = None
    #: Người phụ trách (`NEXT-05`). `null` = **bỏ giao**, cùng lối phân biệt với `follow_up_at`.
    #: Trường này chỉ có nghĩa từ khi dữ liệu thuộc tổ chức: trước đó mỗi bản ghi thuộc đúng một
    #: người nên không có ai khác để giao — xem ghi chú "cắt khỏi phạm vi" ở `NEXT-01`.
    assigned_to_user_id: uuid.UUID | None = None

    def clears_follow_up(self) -> bool:
        return "follow_up_at" in self.model_fields_set and self.follow_up_at is None

    def clears_assignee(self) -> bool:
        return "assigned_to_user_id" in self.model_fields_set and self.assigned_to_user_id is None


class NoteIn(BaseModel):
    body: str = Field(min_length=1, max_length=NOTE_MAX_LENGTH)

    @field_validator("body")
    @classmethod
    def strip_body(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("Ghi chú rỗng.")
        return text


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    card_id: uuid.UUID
    body: str
    created_at: datetime


class FollowUpOut(BaseModel):
    """Trạng thái theo dõi của một liên hệ — thứ giao diện vẽ lại sau mỗi lần sửa."""

    model_config = ConfigDict(from_attributes=True)

    card_id: uuid.UUID
    relationship_status: RelationshipStatus
    relationship_label: str
    follow_up_at: date | None = None
    assigned_to_user_id: uuid.UUID | None = None
    #: Tên hiển thị của người phụ trách, để giao diện khỏi phải gọi thêm một API nữa.
    assigned_to_name: str | None = None
    notes: list[NoteOut] = Field(default_factory=list)


class DueContact(BaseModel):
    """Một dòng của khối *Cần liên hệ hôm nay*."""

    card_id: uuid.UUID
    display_name: str
    company_name: str | None = None
    relationship_status: RelationshipStatus
    relationship_label: str
    follow_up_at: date
    assigned_to_name: str | None = None
    overdue_days: int = Field(description="0 = đến hạn hôm nay; > 0 = trễ bấy nhiêu ngày.")


class DueListOut(BaseModel):
    on: date
    total: int
    items: list[DueContact] = Field(default_factory=list)
