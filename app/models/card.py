"""Bảng business_cards.

Chủ sở hữu: Q | Task: 1.6 | xem Task.md
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class CardStatus(StrEnum):
    """Vòng đời một danh thiếp (Plan.md mục 3)."""

    PENDING = "pending"  # vừa upload, chưa gọi OCR xong
    NEEDS_REVIEW = "needs_review"  # OCR xong, chờ người dùng duyệt
    CONFIRMED = "confirmed"  # người dùng đã xác nhận


class BusinessCard(Base):
    """Một ảnh danh thiếp đã quét cùng các trường trích xuất được."""

    __tablename__ = "business_cards"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # --- Ảnh gốc ---
    image_path: Mapped[str] = mapped_column(Text, nullable=False)
    # SHA-256 của file: unique để upload lại đúng ảnh đó không tạo bản ghi trùng (task 3.1).
    image_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    uploaded_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    # --- Kết quả OCR ---
    ocr_raw_json: Mapped[dict | None] = mapped_column(JSONB)
    full_name: Mapped[str | None] = mapped_column(String(255))
    job_title: Mapped[str | None] = mapped_column(String(255))
    company_name_raw: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(64))
    phone_alt: Mapped[str | None] = mapped_column(String(64))
    address: Mapped[str | None] = mapped_column(Text)
    website: Mapped[str | None] = mapped_column(String(255))
    language_detected: Mapped[str | None] = mapped_column(String(16))
    # Điểm tin cậy từng trường do prompt OCR trả về (task 3.3) — UI tô vàng trường thấp (task 5.1).
    confidence: Mapped[dict | None] = mapped_column(JSONB)

    # --- Trạng thái & liên kết ---
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=CardStatus.PENDING,
        server_default=CardStatus.PENDING,
        index=True,
    )
    # Gắn khi người dùng confirm, qua company_matching.upsert_company() của T (task 4.3).
    #
    # Ràng buộc khoá ngoại tới `companies.id` ĐÃ CÓ trong DB (migration 0001) nhưng cố ý chưa
    # khai ở đây: bảng `companies` thuộc `app/models/company.py` do **T** sở hữu và còn là stub.
    # Khai `ForeignKey(...)` lúc này sẽ làm `Base.metadata.sorted_tables` ném NoReferencedTableError,
    # hỏng cả autogenerate lẫn fixture dựng schema của test (task 6.1).
    # → T khai model `Company` xong thì Q thêm lại `ForeignKey("companies.id", ondelete="SET NULL")`.
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<BusinessCard {self.id} {self.full_name!r} status={self.status}>"
