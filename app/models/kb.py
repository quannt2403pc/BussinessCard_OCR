"""Bảng kb_chunks (vector(384)).

Chủ sở hữu: Q | Task: 1.6 | xem Task.md

Số chiều lấy từ `settings.embedding_dim` (mặc định 384 = `multilingual-e5-small`, Plan.md 2.6).
Nếu task 2.6 chốt model khác số chiều, T phải báo Q **trong ngày** để Q sinh revision đổi kiểu cột
trước D6 — chỉ Q được sinh revision (quy ước số 5, Task.md).
Index `ivfflat` (cosine) KHÔNG tạo ở đây mà ở task 6.3, khi đã có dữ liệu để index học.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from pgvector.sqlalchemy import Vector
from sqlalchemy import Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.core.db import Base


class KBSourceType(StrEnum):
    """Nguồn của một chunk trong Knowledge Base."""

    CARD = "card"
    COMPANY_PROFILE = "company_profile"


class KBChunk(Base):
    """Một đoạn văn bản đã nhúng vector, phục vụ RAG (F3)."""

    __tablename__ = "kb_chunks"
    __table_args__ = (
        # Lọc chunk theo nguồn khi reindex lại một danh thiếp / hồ sơ (task 6.4).
        Index("ix_kb_chunks_source", "source_type", "source_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # Không đặt FK: trỏ tới business_cards hoặc company_profiles tuỳ `source_type`.
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    content: Mapped[str] = mapped_column(Text, nullable=False)
    # `metadata` là tên dành riêng của SQLAlchemy → thuộc tính đặt là `meta`, cột vẫn là "metadata".
    meta: Mapped[dict | None] = mapped_column("metadata", JSONB)

    # Vector đã chuẩn hoá L2, so khớp bằng khoảng cách cosine (Plan.md 2.6).
    embedding: Mapped[list[float] | None] = mapped_column(Vector(settings.embedding_dim))

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<KBChunk {self.id} {self.source_type}:{self.source_id}>"
