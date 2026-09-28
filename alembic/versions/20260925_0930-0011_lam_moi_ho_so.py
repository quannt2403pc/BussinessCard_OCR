"""Lam moi ho so doanh nghiep: moc kiem lai + nhat ky thay doi

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-25 09:30:00.000000+07:00

Task NEXT-06 (T) -- Q cho phep T tu sinh revision cho rieng cac dong NEXT-xx.

RUI RO thap: mot cot NULL duoc tren `company_profiles` va mot bang moi.

`last_checked_at` tach khoi `updated_at`: cot kia doi ca khi nguoi dung sua tay mot truong, ma
sua tay khong phai la da di doi chieu lai voi Internet.

`profile_changes` chi co dong khi **that su co gi do khac** -- ghi ca nhung luot khong doi gi thi
nhat ky day dong "khong co gi moi" va phan dang doc bi chon mat.

`changes` la JSONB; `missing` la nhung truong lan nay tra khong ra -- **khong phai thay doi**,
gia tri cu van duoc giu. `notable` bat khi ma so thue / ten phap ly / dia chi / website doi.

File nay khong dau tieng Viet: xem canh bao o dau alembic.ini (I-07).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "company_profiles",
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "profile_changes",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "company_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("changes", postgresql.JSONB(), nullable=False),
        sa.Column("notable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("acknowledged_at", sa.DateTime(), nullable=True),
        sa.Column(
            "detected_at",
            sa.DateTime(),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
    )
    op.create_index("ix_profile_changes_company_id", "profile_changes", ["company_id"])
    op.create_index(
        "ix_profile_changes_unseen",
        "profile_changes",
        ["user_id", "detected_at"],
        postgresql_where=sa.text("acknowledged_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_profile_changes_unseen", table_name="profile_changes")
    op.drop_index("ix_profile_changes_company_id", table_name="profile_changes")
    op.drop_table("profile_changes")
    op.drop_column("company_profiles", "last_checked_at")
