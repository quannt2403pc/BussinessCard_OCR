"""Bảng business_cards.

Chủ sở hữu: Q | Task: 1.6, 12.5 | xem Task.md

**Từ D12 mỗi danh thiếp thuộc về một người dùng** (`user_id`, task 12.3/12.5). Cột này khai ở
đây phải khớp **từng tên index** với revision `0005`, không chỉ khớp về ý: `conftest.py` dựng
schema test bằng `Base.metadata.create_all()` chứ không chạy migration, nên model và migration
là hai nguồn sự thật song song — lệch tên index thì test chạy trên một lược đồ khác với lược đồ
thật, và `alembic check` sinh ra một cặp drop/create thừa ở revision sau (I-16).
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, String, Text, func
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
    __table_args__ = (
        # Đề xuất số 1 của `docs/db-tuning.md` (T đo ở task 9.8), migration `0004` ở task 9.2.
        # **Một index phục vụ cả hai chiều đọc**: `GET /api/cards` sắp xếp `uploaded_at DESC,
        # id DESC`, còn export của T duyệt keyset `(uploaded_at, id)` tăng dần — Postgres quét
        # btree được theo cả hai chiều nên không cần index thứ hai.
        # Số đo: danh sách thẻ trang 1 2.46 → 0.02 ms; export mỗi lô 6.62 → 0.35 ms.
        Index("ix_business_cards_uploaded_at_id", "uploaded_at", "id"),
        # Chống trùng ảnh **theo từng người dùng**, không toàn cục (task 12.3, Plan.md mục 3).
        # Để unique toàn cục thì B upload đúng tấm thẻ A đã có sẽ bị từ chối, và câu từ chối đó
        # tự khai ra rằng A có tấm thẻ ấy. Đây cũng là index phục vụ `get_by_hash()`.
        Index("ix_business_cards_user_id_image_hash", "user_id", "image_hash", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Chủ sở hữu bản ghi (task 12.3). `CASCADE`: xoá tài khoản là xoá sạch dữ liệu của tài khoản
    # đó, không để lại danh thiếp mồ côi mà không ai truy cập được nữa.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # --- Ảnh gốc ---
    image_path: Mapped[str] = mapped_column(Text, nullable=False)
    # SHA-256 của file gốc (task 3.1). Unique theo `(user_id, image_hash)` — xem __table_args__.
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
    # Điểm tin cậy từng trường do prompt OCR trả về (task 3.3) — UI tô vàng trường thấp (task 5.1).
    confidence: Mapped[dict | None] = mapped_column(JSONB)

    # --- Việt hoá sau khi quét (EX-02, revision 0006) ---
    #
    # **Cột riêng, không ghi đè cột gốc.** Bốn cột trên vẫn giữ nguyên chữ in trên thẻ (quy tắc 3
    # của `prompts/ocr.py`) — mất bản gốc là mất luôn khả năng đối chiếu với ảnh, và giao diện
    # còn phải in nó làm chú thích nhỏ dưới bản dịch.
    #
    # `NULL` ở đây mang đúng một nghĩa: **bản gốc dùng được luôn, không cần bản dịch** — thẻ
    # tiếng Việt và thẻ tiếng Anh rơi hết vào ca này (`services/translate.py::_finalize`). Nó
    # KHÔNG có nghĩa "chưa dịch"; muốn biết đã dịch hay chưa thì đọc `translation_meta`.
    full_name_vi: Mapped[str | None] = mapped_column(String(255))
    job_title_vi: Mapped[str | None] = mapped_column(String(255))
    company_name_vi: Mapped[str | None] = mapped_column(String(255))
    address_vi: Mapped[str | None] = mapped_column(Text)
    # Nguồn bản dịch (`llm` / `dictionary` / `mixed` / `failed` / `skipped`), ngôn ngữ & hệ chữ
    # model nhận ra, cách phiên âm, và cờ `stale` bật khi người dùng sửa tay trường gốc mà chưa
    # bấm *Dịch lại* (task EX-04).
    translation_meta: Mapped[dict | None] = mapped_column(JSONB)

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
    # Khoá ngoại khớp đúng ràng buộc đã có trong DB từ migration 0001 (Postgres tự đặt tên
    # `business_cards_company_id_fkey`) — khai không tên, giống migration và `company.py`,
    # để autogenerate so theo cột/bảng và không sinh lệnh drop/create thừa.
    # `SET NULL` để xoá một công ty không kéo theo danh thiếp: dữ liệu gốc trên thẻ vẫn nằm
    # ở `company_name_raw` và `ocr_raw_json`, gắn lại được sau.
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        index=True,
    )
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<BusinessCard {self.id} {self.full_name!r} status={self.status}>"
