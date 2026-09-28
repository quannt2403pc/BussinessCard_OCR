"""Bảng kb_chunks (vector(384)).

Chủ sở hữu: Q | Task: 1.6

Số chiều lấy từ `settings.embedding_dim`. Đổi model khác số chiều thì cần một Alembic revision
đổi kiểu cột. Index `ivfflat` (cosine) tạo ở revision riêng, khi đã có dữ liệu để index học.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.core.db import Base

#: Số cụm (`lists`) của index `ivfflat`, đo thật chứ không theo công thức chung: KB cỡ vài trăm
#: chunk, để 100 cụm thì mỗi cụm còn 2–3 dòng mà một lượt tìm chỉ dò **một** cụm — hỏi top-5 chỉ
#: nhận về 2 kết quả, không lỗi nào báo.
#:
#: ⚠️ Index học phân cụm từ dữ liệu **có sẵn lúc nó được tạo**, nên `POST /api/kb/reindex` luôn
#: `REINDEX` lại ở cuối — xem `repositories/kb.py::rebuild_vector_index()`.
IVFFLAT_LISTS = 10


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
        # `vector_cosine_ops` phải khớp toán tử `<=>` mà `search_similar` dùng — sai opclass thì
        # câu truy vấn vẫn chạy nhưng bỏ qua index và quét toàn bảng, không báo lỗi gì.
        Index(
            "ix_kb_chunks_embedding",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"lists": IVFFLAT_LISTS},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    #: Cột tách dữ liệu **quan trọng nhất** của F3: rò một chunk là trợ lý đọc được dữ liệu của
    #: không gian khác rồi trả lời ra thành câu. Mọi câu tìm kiếm phải có nó trong `WHERE`.
    #: Không có cột người tạo — chunk là dữ liệu dẫn xuất, sinh lại được bất cứ lúc nào.
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

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
