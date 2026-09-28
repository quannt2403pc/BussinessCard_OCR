"""Nhap lien he tu chu ky email: cot source + image_path cho phep NULL

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-25 14:00:00.000000+07:00

Task NEXT-08 (T) -- Q cho phep T tu sinh revision cho rieng cac dong NEXT-xx.

RUI RO thap-trung binh: mot cot moi co `server_default`, va mot cot **noi long** tu NOT NULL
thanh nullable.

`image_path` phai cho phep NULL vi lien he nhap tu chu ky email **that su khong co anh**. Nhet
chuoi rong vao do thi `_resolve_image("")` tra ve chinh thu muc goc -- mot duong dan hop le tro
vao cho sai.

`image_hash` van NOT NULL: no khong con la bam cua anh ma la **bam cua noi dung nguon**, nho vay
rang buoc unique san co bat luon viec dan hai lan cung mot chu ky.

File nay khong dau tieng Viet: xem canh bao o dau alembic.ini (I-07).
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
    # Dua ve NOT NULL thi phai co gia tri cho moi dong; lien he nhap tu chu ky khong co anh nen
    # dat chuoi rong -- day la buoc lui, khong phai phuc hoi hoan hao.
    op.execute("UPDATE business_cards SET image_path = '' WHERE image_path IS NULL")
    op.alter_column("business_cards", "image_path", existing_type=sa.Text(), nullable=False)
    op.drop_column("business_cards", "source")
