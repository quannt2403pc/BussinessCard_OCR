"""Nghi dinh 13/2023: nhat ky export, han luu tru, xoa theo yeu cau

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-25 11:00:00.000000+07:00

Task NEXT-07 (T). Cung ngoai le nhu `0008`-`0011`: quy uoc so 5 noi "chi Q sinh revision",
**Q cho phep T tu sinh cho rieng cac dong NEXT-xx**.

RUI RO: thap. Mot bang moi va mot cot NULL duoc tren `users`.

DU LIEU TRONG HE THONG NAY LA DU LIEU CA NHAN CUA NGUOI KHAC:

  Ten, so dien thoai, email, dia chi cua nguoi dua danh thiep. Nghi dinh 13/2023 goi ho la
  **chu the du lieu**, va ho co quyen yeu cau xoa. Ba thu bang nay phuc vu:

  - `privacy_logs`: **ai lam gi voi du lieu do, luc nao**. Xuat mot file CSV 500 lien he ra
    ngoai he thong la mot su kien dang ghi lai; xoa theo yeu cau cung vay. Khong co nhat ky
    thi khong tra loi duoc cau hoi dau tien phap che se hoi.
  - `users.retention_days`: **han luu tru cau hinh duoc**. NULL = giu vo thoi han, va do la
    mac dinh -- tu dat mot han roi tu xoa du lieu cua nguoi dung la viec khong ai cho phep.

VI SAO NHAT KY **KHONG** CO KHOA NGOAI TOI `business_cards`:

  Dong nhat ky phai song lau hon du lieu no noi ve. Ghi "da xoa 12 lien he theo yeu cau" ma
  dong ay bien mat cung luc voi 12 lien he do thi nhat ky vo nghia. Nen luu `subject` (email
  hoac so dien thoai da chuan hoa) va `count` duoi dang du lieu chet, khong tham chieu.

VI SAO XOA O DAY LA XOA THAT, khac han `merged_into_id` cua `NEXT-04`:

  `NEXT-04` gop mem vi may chi **doan** hai the la mot nguoi. O day nguoc lai: chu the du lieu
  **yeu cau** xoa, va quyen do khong duoc phuc vu bang mot co an. Xoa that ca hang, anh, ghi
  chu va chunk KB.

INDEX `(user_id, created_at DESC)`: man hinh chi hoi "gan day toi da lam gi voi du lieu".
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | None = None
depends_on: str | None = None

ACTIONS = ("export", "erase", "retention")


def upgrade() -> None:
    op.add_column("users", sa.Column("retention_days", sa.Integer(), nullable=True))
    op.create_table(
        "privacy_logs",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_privacy_logs_user_id_created_at",
        "privacy_logs",
        ["user_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_privacy_logs_user_id_created_at", table_name="privacy_logs")
    op.drop_table("privacy_logs")
    op.drop_column("users", "retention_days")
