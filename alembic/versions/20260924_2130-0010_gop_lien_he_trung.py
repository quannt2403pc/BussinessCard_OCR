"""Gop lien he trung: co merged_into_id tren business_cards

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-24 21:30:00.000000+07:00

Task NEXT-04 (T) -- Q cho phep T tu sinh revision cho rieng cac dong NEXT-xx.

RUI RO thap: mot cot NULL duoc, khoa ngoai tro ve chinh bang, mot index partial.

GOP MEM CHU KHONG XOA: may chi **goi y** hai the la mot nguoi (trung so tong dai, trung dia chi
info@ la chuyen thuong), nen doan sai + xoa = mat du lieu that vi mot phong doan. Ban trung o lai
nguyen ven, bien khoi moi danh sach va moi con so bao cao, nhung **go gop duoc**.

⚠️ Doi lai: sau revision nay **moi cau liet ke phai them `merged_into_id IS NULL`** -- sau cho,
liet ke du trong docstring cua `models/card.py`.

`SET NULL` tren khoa ngoai tro ve chinh bang: xoa han the chinh thi cac ban trung quay lai lam
the doc lap chu khong bien mat theo.

File nay khong dau tieng Viet: xem canh bao o dau alembic.ini (I-07).
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "business_cards",
        sa.Column(
            "merged_into_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("business_cards.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_business_cards_merged_into",
        "business_cards",
        ["merged_into_id"],
        postgresql_where=sa.text("merged_into_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_business_cards_merged_into", table_name="business_cards")
    op.drop_column("business_cards", "merged_into_id")
