"""Trang thai quan he + ngay hen lien he lai + bang ghi chu theo doi

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-24 17:30:00.000000+07:00

Task NEXT-01 (T) -- Q cho phep T tu sinh revision cho rieng cac dong NEXT-xx (2026-09-24).

RUI RO thap. `relationship_status` NOT NULL nhung co `server_default='new'` nen Postgres 11+ luu
default vao catalog, khong phai viet lai bang.

Ghi chu la bang rieng chu khong noi them vao `business_cards.notes`: cot do la ghi chu **ve tam
the**, con day la **dong thoi gian cua mot quan he** -- nhieu dong, moi dong mot moc thoi gian.

INDEX partial `WHERE follow_up_at IS NOT NULL`: phan lon the khong co hen.

File nay khong dau tieng Viet: xem canh bao o dau alembic.ini (I-07).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | None = None
depends_on: str | None = None

#: Bon trang thai, dung bang bon -- xem ghi chu "ranh gioi phai giu" o NEXT-01: them "co hoi",
#: "gia tri hop dong", "phieu ban hang" la bien san pham thanh CRM nua voi.
RELATIONSHIP_STATUSES = ("new", "contacted", "talking", "closed")


def upgrade() -> None:
    op.add_column(
        "business_cards",
        sa.Column(
            "relationship_status",
            sa.String(16),
            nullable=False,
            server_default="new",
        ),
    )
    op.add_column("business_cards", sa.Column("follow_up_at", sa.Date(), nullable=True))
    op.create_index(
        "ix_business_cards_follow_up",
        "business_cards",
        ["user_id", "follow_up_at"],
        postgresql_where=sa.text("follow_up_at IS NOT NULL"),
    )

    op.create_table(
        "contact_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        # `user_id` co mat du suy ra duoc qua `card_id`: JOIN them mot bang moi biet cua ai la
        # cho de quen, ma quen mot cho la ro.
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("card_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        # `clock_timestamp()` chứ không `now()`: `now()` trả giờ **bắt đầu transaction**, nên hai
        # ghi chú thêm trong cùng một transaction có cùng mốc y hệt và mất thứ tự.
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="contact_notes_pkey"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_contact_notes_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["card_id"],
            ["business_cards.id"],
            name="fk_contact_notes_card_id_business_cards",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_contact_notes_user_id", "contact_notes", ["user_id"])
    op.create_index(
        "ix_contact_notes_card_id_created_at",
        "contact_notes",
        ["card_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_contact_notes_card_id_created_at", table_name="contact_notes")
    op.drop_index("ix_contact_notes_user_id", table_name="contact_notes")
    op.drop_table("contact_notes")
    op.drop_index("ix_business_cards_follow_up", table_name="business_cards")
    op.drop_column("business_cards", "follow_up_at")
    op.drop_column("business_cards", "relationship_status")
