"""Bảng events — sự kiện thu thập danh thiếp (hội chợ, hội thảo, hội nghị khách hàng).

Chủ sở hữu: T | Task: NEXT-03 | revision `0009`

**Một bảng chứ không phải một cột chữ trên `business_cards`.** Sự kiện thì ít mà danh thiếp thì
nhiều, và tên sự kiện hay bị gõ sai ở tấm thứ năm mươi; sửa một chỗ phải sửa được cho cả lô.
Bảng riêng còn cho phép gắn ngày diễn ra và đếm số thẻ mà không phải `GROUP BY` trên chuỗi.

**Mỗi thẻ thuộc nhiều nhất một sự kiện**, không phải quan hệ nhiều–nhiều. Một tấm danh thiếp
được đưa ở đúng một chỗ vào đúng một lúc. Nhãn tự do gắn chồng lên nhau là một hệ thống tag đa
mục đích — thứ Task.md dặn phải tránh.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

EVENT_NAME_MAX_LENGTH = 120


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        # Chống trùng **theo từng người dùng**, cùng luật với `companies` (Plan.md mục 3): hai
        # người cùng đi một hội chợ thì ai cũng có sự kiện của mình.
        Index("ix_events_user_id_name", "user_id", "name_normalized", unique=True),
        # **Nhiều nhất một sự kiện đang diễn ra cho mỗi người**, ràng buộc ở DB chứ không chỉ ở
        # mã: cờ này quyết định thẻ vừa quét được đóng dấu vào đâu, mà hai dòng cùng bật thì việc
        # chọn dòng nào trở thành ngẫu nhiên theo thứ tự trả về của Postgres.
        Index(
            "ix_events_one_active",
            "user_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(EVENT_NAME_MAX_LENGTH), nullable=False)
    #: Khoá so trùng, sinh bằng `normalize_label()`. Giữ riêng khỏi `name` để người dùng vẫn đọc
    #: được đúng chữ hoa và dấu tiếng Việt họ đã gõ.
    name_normalized: Mapped[str] = mapped_column(String(EVENT_NAME_MAX_LENGTH), nullable=False)
    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    #: Sự kiện **đang diễn ra**: mọi thẻ quét từ giờ tự mang nhãn này. Đứng ở hội chợ quét liên
    #: tục thì không ai muốn chọn lại nhãn cho từng tấm.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<Event {self.id} {self.name!r} active={self.is_active}>"
