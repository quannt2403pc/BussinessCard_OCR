"""Gop lien he trung: co merged_into_id tren business_cards

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-24 21:30:00.000000+07:00

Task NEXT-04 (T). Cung ngoai le nhu `0008`/`0009`: quy uoc so 5 noi "chi Q sinh revision",
**Q cho phep T tu sinh cho rieng cac dong NEXT-xx, 2026-09-24**.

RUI RO: thap. Mot cot NULL duoc, khoa ngoai tro ve chinh bang, mot index partial.

VI SAO GOP MEM CHU KHONG XOA BAN TRUNG:

  Xoa mot ban trung la xoa mot tam anh that ma nguoi dung khong lay lai duoc. Ma viec "hai the
  nay la mot nguoi" thi may chi **goi y**: trung so tong dai cong ty la chuyen thuong, trung
  dia chi info@ cung vay. Doan sai + xoa = mat du lieu that vi mot phong doan.

  Nen ban trung o lai nguyen ven, chi mang co `merged_into_id`. No bien mat khoi moi danh sach,
  moi ban xuat va moi con so bao cao, nhung mo ra van doc duoc, va **go gop duoc**.

  Doi lai: sau revision nay, **moi cau liet ke phai them `merged_into_id IS NULL`**. Sau cho:
  `card_repo._list_conditions`, `export._cards_select` + `count_cards`, `contact_repo` (hai cau
  nhac viec), `event_repo._report_select`, `stats.get_stats`.

`ondelete="SET NULL"` tren khoa ngoai tro ve chinh bang: xoa han the chinh thi cac ban trung
**quay lai lam the doc lap** chu khong bien mat theo. Chung von la du lieu that.

INDEX partial `WHERE merged_into_id IS NOT NULL`: gan het bang co gia tri NULL o cot nay, nen
index day du chi to phi. Cau duy nhat can index la "liet ke nhung the da gop vao the X", dung
khi ve trang chi tiet va khi go gop.
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
