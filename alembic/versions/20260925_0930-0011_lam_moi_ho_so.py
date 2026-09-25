"""Lam moi ho so doanh nghiep: moc kiem lai + nhat ky thay doi

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-25 09:30:00.000000+07:00

Task NEXT-06 (T). Cung ngoai le nhu `0008`-`0010`: quy uoc so 5 noi "chi Q sinh revision",
**Q cho phep T tu sinh cho rieng cac dong NEXT-xx**.

RUI RO: thap. Mot cot NULL duoc tren `company_profiles` va mot bang moi.

VI SAO CO CA COT LAN BANG:

  - `company_profiles.last_checked_at` tra loi "ho so nay tra lai lan gan nhat la bao gio" ->
    dung de xep hang cai nao qua han. Khong dung `updated_at` duoc: cot do doi ca khi nguoi
    dung sua tay mot truong, ma sua tay khong phai la da di doi chieu lai voi Internet.
  - `profile_changes` chi co dong khi **that su co gi do khac**. Luot lam moi ma khong doi gi
    thi chi cap nhat `last_checked_at`, khong sinh rac. Nguoc lai thi nhat ky day nhung dong
    "khong co gi moi" va phan dang doc bi chon mat.

`changes` la JSONB dang {"changes": {truong: {"old": ..., "new": ...}}, "missing": [truong]}.
`missing` la nhung truong lan truoc co ma lan nay tra khong ra -- **khong phai thay doi**, gia
tri cu van duoc giu (xem `services/profile_diff.py`).

`notable` bat khi mot trong `tax_code` / `legal_name` / `address` / `website` doi. Ma so thue
doi nghia la **phap nhan doi** -- sap nhap, tach cong ty, hoac ho so cu gan nham doanh nghiep.

INDEX partial `WHERE acknowledged_at IS NULL`: man hinh chi hoi "con thay doi nao chua xem
khong". Dong da xem la phan lon bang sau vai tuan, khong dang so hoa.
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
