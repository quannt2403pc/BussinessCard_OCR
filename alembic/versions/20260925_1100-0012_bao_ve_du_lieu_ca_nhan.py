"""Nghi dinh 13/2023: nhat ky export, han luu tru, xoa theo yeu cau

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-25 11:00:00.000000+07:00

Task NEXT-07 (T) -- Q cho phep T tu sinh revision cho rieng cac dong NEXT-xx.

RUI RO thap: mot bang moi va mot cot NULL duoc tren `users`.

Du lieu trong he thong nay la du lieu ca nhan cua nguoi khac, va ho co quyen yeu cau xoa.
`privacy_logs` ghi **ai lam gi voi du lieu do, luc nao**; `users.retention_days` NULL = giu vo
thoi han, va do la mac dinh -- tu dat mot han roi tu xoa du lieu cua nguoi dung la viec khong ai
cho phep.

⚠️ Nhat ky **khong** co khoa ngoai toi `business_cards`: dong nhat ky phai song lau hon du lieu
no noi ve.

Xoa o day la **xoa that**, khac han gop mem cua `NEXT-04`: o kia may chi doan, o day chu the du
lieu yeu cau -- va quyen do khong duoc phuc vu bang mot co an.

File nay khong dau tieng Viet: xem canh bao o dau alembic.ini (I-07).
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
