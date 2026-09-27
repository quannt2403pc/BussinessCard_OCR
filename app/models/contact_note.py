"""Bảng contact_notes — dòng thời gian theo dõi một liên hệ.

Chủ sở hữu: T | Task: NEXT-01 | revision `0008`

Khác `business_cards.notes`: ô đó là ghi chú **về tấm thẻ** (máy ghi khi quét hỏng, người dùng
sửa tay). Bảng này là **dòng thời gian của một quan hệ** — nhiều dòng, mỗi dòng có mốc thời gian,
đọc theo thứ tự ngược. Nhét cả hai vào một ô văn bản là mất mốc thời gian, và mất luôn khả năng
trả lời "lần liên hệ gần nhất là bao giờ".
"""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ContactNote(Base):
    __tablename__ = "contact_notes"
    __table_args__ = (
        Index("ix_contact_notes_card_id_created_at", "card_id", text("created_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    #: Suy ra được qua `card_id`, nhưng vẫn gắn thẳng: mọi truy vấn đều lọc theo người dùng
    #: (12.6), mà phải JOIN thêm một bảng mới biết của ai là chỗ dễ quên — quên một chỗ là rò.
    #: **Khoá tách dữ liệu** từ `NEXT-05` (revision `0015`). Mọi câu `WHERE` lọc dữ liệu đi
    #: qua cột này, không còn qua `user_id`. `CASCADE`: xoá một không gian là xoá sạch dữ liệu
    #: của nó — không để lại bản ghi mồ côi mà không ai truy cập được nữa.
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: **Người tạo**, không còn là khoá tách dữ liệu (`NEXT-05`). Giữ lại vì nó vẫn trả
    #: lời được "ai nhập bản ghi này" và là giá trị mặc định hợp lý cho người phụ trách.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("business_cards.id", ondelete="CASCADE"),
        nullable=False,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    #: `clock_timestamp()` chứ không `now()` — xem lý do ở revision `0008`: `now()` là giờ bắt
    #: đầu transaction, nên nhiều ghi chú trong cùng một transaction sẽ trùng mốc và mất thứ tự.
    created_at: Mapped[datetime] = mapped_column(
        server_default=text("clock_timestamp()"), nullable=False
    )

    def __repr__(self) -> str:
        return f"<ContactNote {self.id} card={self.card_id}>"
