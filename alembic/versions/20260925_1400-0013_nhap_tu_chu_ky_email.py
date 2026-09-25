"""Nhap lien he tu chu ky email: cot source + image_path cho phep NULL

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-25 14:00:00.000000+07:00

Task NEXT-08 (T). Cung ngoai le nhu `0008`-`0012`: quy uoc so 5 noi "chi Q sinh revision",
**Q cho phep T tu sinh cho rieng cac dong NEXT-xx**.

RUI RO: thap-trung binh. Mot cot moi co `server_default`, va mot cot **noi long** tu NOT NULL
thanh nullable -- noi long thi du lieu dang co van hop le, khong phai viet lai bang.

VI SAO `image_path` PHAI CHO PHEP NULL:

  Mot lien he nhap tu chu ky email **that su khong co anh**. Cach khac la nhet chuoi rong vao
  do, nhung roi moi cho doc cot nay deu phai doan xem chuoi rong nghia la gi -- va
  `_resolve_image("")` tra ve chinh thu muc goc, tuc la mot duong dan hop le tro vao cho sai.
  NULL noi dung mot dieu: khong co anh.

VI SAO `image_hash` VAN NOT NULL:

  No khong con la bam cua anh nua ma la **bam cua noi dung nguon**. Voi chu ky email thi do la
  sha256 cua khoi chu da chuan hoa. Nho vay rang buoc unique `(user_id, image_hash)` san co
  bat luon viec dan hai lan cung mot chu ky, dung duong ma `3.1` da dung cho anh trung -- khong
  phai them co che chong trung thu hai.

COT `source`:

  `scan` (mac dinh, moi dong dang co) hoac `signature`. Giao dien can biet de khong ve khung
  anh rong, va bao cao ve sau can biet lien he den tu dau.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | None = None
depends_on: str | None = None

SOURCES = ("scan", "signature")


def upgrade() -> None:
    op.add_column(
        "business_cards",
        sa.Column(
            "source", sa.String(16), nullable=False, server_default="scan"
        ),
    )
    op.alter_column("business_cards", "image_path", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    # Dua ve NOT NULL thi phai co gia tri cho moi dong: nhung lien he nhap tu chu ky khong co
    # anh, nen dat chuoi rong -- day la buoc lui, khong phai buoc phuc hoi hoan hao.
    op.execute("UPDATE business_cards SET image_path = '' WHERE image_path IS NULL")
    op.alter_column("business_cards", "image_path", existing_type=sa.Text(), nullable=False)
    op.drop_column("business_cards", "source")
