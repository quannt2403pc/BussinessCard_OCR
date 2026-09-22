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
from sqlalchemy import ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.core.db import Base

#: Số cụm (`lists`) của index `ivfflat`. **Đo thật 2026-09-16, không lấy theo công thức chung.**
#:
#: pgvector khuyên `lists = số dòng / 1000`, nhưng KB của bản demo chỉ cỡ vài trăm chunk
#: (30 danh thiếp + 10 hồ sơ). Để 100 cụm thì mỗi cụm còn 2–3 dòng, mà một lượt tìm mặc định
#: chỉ dò **một** cụm (`ivfflat.probes = 1`) — hỏi top-5 nhưng chỉ nhận về 2 kết quả, và không
#: có lỗi nào báo. 10 cụm giữ mỗi cụm vài chục dòng, đủ cho quy mô này.
#:
#: ⚠️ Index này **học phân cụm từ dữ liệu có sẵn đúng lúc nó được tạo**. Migration 0003 chạy khi
#: `kb_chunks` còn rỗng nên centroid vô nghĩa: đã đo, chèn 3 dòng rồi tìm chỉ ra 1 dòng
#: (pgvector cũng tự cảnh báo *"ivfflat index created with little data"*). Vì vậy
#: `POST /api/kb/reindex` **luôn `REINDEX` lại index này ở cuối** — xem
#: `repositories/kb.py::rebuild_vector_index()`.
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
        # Vector search (task 6.3, revision 0003). `vector_cosine_ops` phải khớp với toán tử
        # `<=>` mà `repositories/kb.py::search_similar` dùng — sai opclass thì câu truy vấn vẫn
        # chạy nhưng bỏ qua index và quét toàn bảng, tức là hỏng về tốc độ chứ không báo lỗi.
        #
        # Khai ở đây *và* trong revision 0003: thiếu khai ở model thì `alembic check` coi index
        # trong DB là thừa và sinh lệnh `drop_index` ở revision sau (Q giữ `alembic check` sạch
        # từ I-16).
        Index(
            "ix_kb_chunks_embedding",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"lists": IVFFLAT_LISTS},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Chủ sở hữu chunk (task 12.3). Đây là cột **quan trọng nhất** của việc tách dữ liệu ở F3:
    # rò một chunk là trợ lý AI đọc được dữ liệu của người khác rồi trả lời ra thành câu — đường
    # rò khó thấy nhất mà tiêu chí A9 nhắm tới. Mọi câu tìm kiếm phải có nó trong `WHERE`
    # (`repositories/kb.py::scope_filters`), không lọc lại sau khi đã lấy về.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
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
