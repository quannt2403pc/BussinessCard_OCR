"""Schema cho sự kiện thu thập và báo cáo theo sự kiện.

Chủ sở hữu: T | Task: NEXT-03 | xem Task.md
"""

import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.event import EVENT_NAME_MAX_LENGTH

MAX_ASSIGN_CARDS = 500


class EventIn(BaseModel):
    name: str = Field(min_length=1, max_length=EVENT_NAME_MAX_LENGTH)
    starts_on: date | None = None
    ends_on: date | None = None
    activate: bool = False

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("Tên sự kiện rỗng.")
        return text

    @model_validator(mode="after")
    def check_dates(self) -> "EventIn":
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            raise ValueError("Ngày kết thúc trước ngày bắt đầu.")
        return self


class EventPatch(BaseModel):
    """Body của `PATCH`. **Trường không gửi thì giữ nguyên**, gửi `null` là xoá.

    Cùng luật với `FollowUpIn` của `NEXT-01`, phân biệt bằng `model_fields_set` — gộp hai thứ
    lại thì mỗi lần bật/tắt sự kiện là một lần xoá mất ngày người dùng đã nhập.
    """

    name: str | None = Field(default=None, max_length=EVENT_NAME_MAX_LENGTH)
    starts_on: date | None = None
    ends_on: date | None = None
    activate: bool | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            raise ValueError("Tên sự kiện rỗng.")
        return text

    def clears(self, field: str) -> bool:
        return field in self.model_fields_set and getattr(self, field) is None


class AssignIn(BaseModel):
    card_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_ASSIGN_CARDS)


class EventReport(BaseModel):
    """Một dòng của bảng *Báo cáo theo sự kiện*.

    `conversion_rate` chỉ tính trên những thẻ **đã ngã ngũ** (`won + lost`), không tính trên tổng
    số thẻ: chia cho tổng thì một hội chợ vừa diễn ra tuần trước luôn trông tệ hơn một hội chợ
    năm ngoái, chỉ vì phần lớn liên hệ còn đang trao đổi. `None` = chưa có thẻ nào ngã ngũ, và
    giao diện in dấu gạch chứ **không** in `0%` — hai thứ đó khác hẳn nhau.

    `closed_unknown` đếm riêng những dòng mang giá trị cũ `closed` (trước `NEXT-03`, không rõ
    thắng hay thua). Để lộ ra chứ không gộp vào đâu cả: gộp vào `lost` là bịa, gộp vào `won` còn
    tệ hơn, giấu đi thì các con số không cộng lại thành tổng.
    """

    model_config = ConfigDict(from_attributes=True)

    event_id: uuid.UUID | None = None
    name: str
    starts_on: date | None = None
    ends_on: date | None = None
    is_active: bool = False
    total_cards: int = 0
    won: int = 0
    lost: int = 0
    closed_unknown: int = 0
    in_progress: int = 0
    conversion_rate: float | None = Field(
        default=None, description="won / (won + lost); null khi chưa thẻ nào ngã ngũ."
    )


class EventListOut(BaseModel):
    active_event_id: uuid.UUID | None = None
    items: list[EventReport] = Field(default_factory=list)
    #: Thẻ chưa gắn sự kiện nào. Luôn có mặt, kể cả khi bằng 0.
    unassigned: EventReport


class AssignOut(BaseModel):
    event_id: uuid.UUID | None = None
    updated: int
