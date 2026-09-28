"""Index ivfflat (cosine) tren kb_chunks.embedding

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-16 21:10:00.000000+07:00

Task 6.3 (Q).

⚠️ Index nay HOC PHAN CUM tu du lieu co san dung luc tao, nen `0001` co y khong tao no. Tao tren
bang rong thi centroid vo nghia va truy van tra ve gan nhu khong co gi (do that: chen 3 dong, tim
chi ra 1). Vi vay `POST /api/kb/reindex` luon `REINDEX` o cuoi.

Opclass `vector_cosine_ops` phai khop toan tu `<=>` ma `search_similar` dung -- dat sai thi cau
truy van van chay nhung KHONG dung index, hong ve toc do va khong bao loi.

File nay khong dau tieng Viet: xem canh bao o dau alembic.ini (I-07).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Khop voi app/models/kb.py::IVFFLAT_LISTS: 10 cum, khong phai 100.
IVFFLAT_LISTS = 10


def upgrade() -> None:
    op.create_index(
        "ix_kb_chunks_embedding",
        "kb_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="ivfflat",
        postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_with={"lists": IVFFLAT_LISTS},
    )


def downgrade() -> None:
    op.drop_index("ix_kb_chunks_embedding", table_name="kb_chunks")
