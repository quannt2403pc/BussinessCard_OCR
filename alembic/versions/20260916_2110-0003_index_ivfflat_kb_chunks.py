"""Index ivfflat (cosine) tren kb_chunks.embedding

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-16 21:10:00.000000+07:00

Task 6.3 (Q). Migration 0001 co y KHONG tao index nay: ivfflat hoc phan cum tu du lieu co san
luc tao index, nen tao tren bang rong thi phan cum vo nghia. Tao o day vi D6 la luc KB bat dau
co du lieu that (POST /api/kb/reindex).

QUAN TRONG — index nay HOC PHAN CUM tu du lieu co san dung luc tao. Tao tren bang rong thi
centroid vo nghia va truy van tra ve gan nhu khong co gi (do that 2026-09-16: chen 3 dong, tim
chi ra 1). Vi vay `POST /api/kb/reindex` luon chay `REINDEX INDEX ix_kb_chunks_embedding` o cuoi
(repositories/kb.py::rebuild_vector_index). Chay tay khi can:

    docker compose exec db psql -U bizcard -d bizcard -c "REINDEX INDEX ix_kb_chunks_embedding;"

Opclass `vector_cosine_ops` phai khop voi toan tu `<=>` ma repositories/kb.py::search_similar
dung. Dat sai opclass thi cau truy van van chay nhung KHONG dung index — hong ve toc do, khong
bao loi.

File nay khong dau tieng Viet: xem canh bao o dau alembic.ini (I-07).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Khop voi app/models/kb.py::IVFFLAT_LISTS (xem ghi chu day du o do): 10 cum, khong phai 100.
#: KB co vai tram chunk ma chia 100 cum thi moi cum con 2-3 dong, trong khi mot luot tim mac
#: dinh chi do MOT cum -> hoi top-5 chi nhan ve 2 ket qua.
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
