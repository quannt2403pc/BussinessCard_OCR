"""Bang su kien thu thap + gan the vao su kien

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-24 19:30:00.000000+07:00

Task NEXT-03 (T) -- Q cho phep T tu sinh revision cho rieng cac dong NEXT-xx.

RUI RO thap: mot bang moi va mot cot NULL duoc tren `business_cards`.

`business_cards.event_id` la SET NULL chu khong CASCADE: xoa nhan mot hoi cho **khong duoc** keo
theo may tram tam the thu ve tu hoi cho do.

Hai index unique: chong trung ten su kien theo tung nguoi dung, va (partial, WHERE is_active)
**nhieu nhat mot su kien dang dien ra cho moi nguoi** -- hai dong cung bat thi viec chon dong nao
thanh ngau nhien theo thu tu Postgres tra ve.

Khong co buoc doi du lieu cho `relationship_status`: nhung dong dang mang `closed` giu nguyen,
gan bua chung sang `lost` la bia ra du lieu chua ai nhap.

File nay khong dau tieng Viet: xem canh bao o dau alembic.ini (I-07).
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | None = None
depends_on: str | None = None

EVENT_NAME_MAX_LENGTH = 120


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(EVENT_NAME_MAX_LENGTH), nullable=False),
        sa.Column("name_normalized", sa.String(EVENT_NAME_MAX_LENGTH), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=True),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_events_user_id", "events", ["user_id"])
    op.create_index(
        "ix_events_user_id_name", "events", ["user_id", "name_normalized"], unique=True
    )
    op.create_index(
        "ix_events_one_active",
        "events",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.add_column(
        "business_cards",
        sa.Column(
            "event_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("events.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_business_cards_user_id_event_id", "business_cards", ["user_id", "event_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_business_cards_user_id_event_id", table_name="business_cards")
    op.drop_column("business_cards", "event_id")
    op.drop_index("ix_events_one_active", table_name="events")
    op.drop_index("ix_events_user_id_name", table_name="events")
    op.drop_index("ix_events_user_id", table_name="events")
    op.drop_table("events")
