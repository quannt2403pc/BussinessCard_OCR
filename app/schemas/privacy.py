"""Schema cho phần tuân thủ Nghị định 13/2023.

Chủ sở hữu: T | Task: NEXT-07 | xem Task.md
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.privacy import PrivacyAction
from app.repositories.privacy import MAX_RETENTION_DAYS, MIN_RETENTION_DAYS


class PrivacyLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    action: PrivacyAction
    label: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)
    record_count: int = 0
    created_at: datetime


class LogListOut(BaseModel):
    total: int = 0
    items: list[PrivacyLogOut] = Field(default_factory=list)


class RetentionIn(BaseModel):
    """`null` = giữ vô thời hạn. Đó là mặc định và là thứ phải chọn để tắt hạn lưu trữ."""

    days: int | None = Field(default=None, ge=MIN_RETENTION_DAYS, le=MAX_RETENTION_DAYS)


class RetentionOut(BaseModel):
    days: int | None = None
    total_contacts: int = 0
    #: Số danh thiếp đã quá hạn. **Không tự xoá** — hệ thống chỉ đếm ra và chờ người bấm.
    expired: int = 0
    oldest_upload: datetime | None = None


class SubjectIn(BaseModel):
    """Email hoặc số điện thoại **đã chuẩn hoá** của chủ thể dữ liệu."""

    term: str = Field(min_length=3, max_length=255)

    @field_validator("term")
    @classmethod
    def strip_term(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("Chuỗi tìm kiếm rỗng.")
        return text


class SubjectCard(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str | None = None
    full_name_vi: str | None = None
    company_name_raw: str | None = None
    email: str | None = None
    phone: str | None = None
    uploaded_at: datetime


class SubjectOut(BaseModel):
    term: str
    total: int = 0
    items: list[SubjectCard] = Field(default_factory=list)


class EraseOut(BaseModel):
    term: str
    erased: int = 0
    #: File ảnh xoá được trên volume. Xoá hàng trước, xoá file sau (cùng luật với `4.2` của Q):
    #: file thừa nằm lại không làm hỏng gì, còn một bản ghi trỏ vào file đã mất thì hỏng im lặng.
    images_removed: int = 0


class PurgeOut(BaseModel):
    days: int
    erased: int = 0
    images_removed: int = 0
