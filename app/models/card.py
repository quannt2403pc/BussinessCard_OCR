"""Bảng business_cards.

Chủ sở hữu: Q | Task: 1.6, 12.5

⚠️ Tên index ở đây phải khớp **từng chữ** với migration: `conftest.py` dựng schema test bằng
`create_all()` chứ không chạy migration, nên model và migration là hai nguồn sự thật song song.
"""

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import Date, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class CardStatus(StrEnum):
    """Vòng đời một danh thiếp (Plan.md mục 3)."""

    PENDING = "pending"  # vừa upload, chưa gọi OCR xong
    NEEDS_REVIEW = "needs_review"  # OCR xong, chờ người dùng duyệt
    CONFIRMED = "confirmed"  # người dùng đã xác nhận


class RelationshipStatus(StrEnum):
    """Vòng đời quan hệ với người trên thẻ.

    `CLOSED` giữ nghĩa cũ: đã dừng, không rõ thắng hay thua. Giao diện không mời chọn nữa.
    """

    NEW = "new"  # vừa quét, chưa liên hệ lần nào
    CONTACTED = "contacted"  # đã gọi hoặc gửi thư, chưa có hồi âm đáng kể
    TALKING = "talking"  # đang trao đổi qua lại
    WON = "won"  # đã chốt được
    LOST = "lost"  # không thành
    CLOSED = "closed"  # giá trị cũ trước NEXT-03: đã dừng, không rõ kết cục


#: Các trạng thái **đã ngã ngũ** — quan hệ dừng ở đây, không còn nhắc liên hệ lại nữa.
TERMINAL_RELATIONSHIP_STATUSES: frozenset[str] = frozenset(
    {RelationshipStatus.WON, RelationshipStatus.LOST, RelationshipStatus.CLOSED}
)


class BusinessCard(Base):
    """Một ảnh danh thiếp đã quét cùng các trường trích xuất được."""

    __tablename__ = "business_cards"
    __table_args__ = (
        # Một index cho cả hai chiều đọc: danh sách thẻ (DESC) và keyset export (ASC).
        Index("ix_business_cards_uploaded_at_id", "uploaded_at", "id"),
        # Chống trùng ảnh theo từng không gian, không toàn cục. Cũng phục vụ `get_by_hash()`.
        Index(
            "ix_business_cards_workspace_id_image_hash",
            "workspace_id",
            "image_hash",
            unique=True,
        ),
        # Khối *Cần liên hệ hôm nay*; partial vì phần lớn thẻ không có hẹn.
        Index(
            "ix_business_cards_follow_up",
            "workspace_id",
            "follow_up_at",
            postgresql_where=text("follow_up_at IS NOT NULL"),
        ),
        # Partial: gần hết bảng để `NULL`; chỉ dùng cho "những thẻ đã gộp vào thẻ X".
        Index(
            "ix_business_cards_merged_into",
            "merged_into_id",
            postgresql_where=text("merged_into_id IS NOT NULL"),
        ),
        # Khối *việc của tôi*; partial vì phần lớn liên hệ chưa giao cho ai.
        Index(
            "ix_business_cards_assigned_to",
            "workspace_id",
            "assigned_to_user_id",
            postgresql_where=text("assigned_to_user_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    #: Khoá tách dữ liệu: mọi câu `WHERE` lọc qua cột này, không qua `user_id`.
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: Người tạo — KHÔNG phải khoá tách dữ liệu.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # --- Ảnh gốc ---
    image_path: Mapped[str | None] = mapped_column(Text)
    # SHA-256 của file gốc. Unique theo `(workspace_id, image_hash)` — xem __table_args__.
    image_hash: Mapped[str] = mapped_column(String(64), nullable=False)
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
    # Điểm tin cậy từng trường do prompt OCR trả về; UI tô vàng trường thấp.
    confidence: Mapped[dict | None] = mapped_column(JSONB)

    # --- Việt hoá sau khi quét ---
    # Cột riêng, không ghi đè bản gốc. `NULL` = bản gốc dùng được luôn, KHÔNG phải "chưa dịch".
    full_name_vi: Mapped[str | None] = mapped_column(String(255))
    job_title_vi: Mapped[str | None] = mapped_column(String(255))
    company_name_vi: Mapped[str | None] = mapped_column(String(255))
    address_vi: Mapped[str | None] = mapped_column(Text)
    # Nguồn bản dịch, ngôn ngữ/hệ chữ, cách phiên âm, cờ `stale` khi sửa tay bản gốc.
    translation_meta: Mapped[dict | None] = mapped_column(JSONB)

    # --- Trạng thái & liên kết ---
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=CardStatus.PENDING,
        server_default=CardStatus.PENDING,
        index=True,
    )
    # Gắn khi người dùng confirm, qua `company_matching.upsert_company()`.
    # `SET NULL`: xoá công ty không kéo theo danh thiếp — dữ liệu gốc còn ở `company_name_raw`.
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        index=True,
    )
    notes: Mapped[str | None] = mapped_column(Text)

    #: Người phụ trách; `NULL` = chưa giao. `SET NULL` để xoá tài khoản không kéo theo liên hệ.
    assigned_to_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )

    # Vòng đời **quan hệ**, tách khỏi `status` (vòng đời quét) ở trên.
    relationship_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=RelationshipStatus.NEW,
        server_default=RelationshipStatus.NEW,
    )
    #: Ngày cần liên hệ lại. `NULL` = không hẹn, và đó là mặc định — không tự đặt hẹn hộ người
    #: dùng, vì một hàng chờ đầy việc không ai hẹn là hàng chờ bị bỏ qua.
    follow_up_at: Mapped[date | None] = mapped_column(Date)

    # Gộp mềm: bản trùng ở lại, biến khỏi mọi danh sách nhưng gỡ gộp được.
    # Mọi câu liệt kê phải kèm `merged_into_id IS NULL`.
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("business_cards.id", ondelete="SET NULL"),
    )

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<BusinessCard {self.id} {self.full_name!r} status={self.status}>"
