"""Bảng chat_sessions + chat_messages.

Chủ sở hữu: Q | Task: 1.6 | xem Task.md
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class ChatRole(StrEnum):
    """Vai trò của một lượt trong hội thoại."""

    USER = "user"
    ASSISTANT = "assistant"


class ChatSession(Base):
    """Một phiên hỏi–đáp với trợ lý AI (F3)."""

    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Chủ sở hữu phiên (task 12.3). `chat_messages` **không** có cột này: lượt chat luôn được
    # đọc qua phiên của nó, nên một khoá chủ sở hữu ở phiên là đủ và không có chỗ nào để hai
    # nguồn sự thật lệch nhau. Phần lọc đi qua `repositories/chat.py`.
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
    title: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )

    def __repr__(self) -> str:
        return f"<ChatSession {self.id} {self.title!r}>"


class ChatMessage(Base):
    """Một lượt hỏi hoặc trả lời; câu trả lời kèm `citations` trỏ về nguồn trong KB."""

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Danh sách nguồn trích dẫn: [{source_type, source_id, snippet, score}] — hợp đồng chốt ở họp D2.
    citations: Mapped[list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    session: Mapped[ChatSession] = relationship(back_populates="messages")

    def __repr__(self) -> str:
        return f"<ChatMessage {self.id} {self.role}>"
