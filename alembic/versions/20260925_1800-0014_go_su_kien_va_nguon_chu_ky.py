"""Go bang su kien va cot nguon: NEXT-03 va NEXT-08 bi cat khoi pham vi

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-25 18:00:00.000000+07:00

Task NEXT-03 + NEXT-08 (T) -- **ca hai bi cat khoi pham vi 2026-09-25**.

⚠️ RUI RO trung binh -- **revision duy nhat trong du an lam MAT du lieu**: bang `events` va cot
`business_cards.event_id` bien mat cung nhau nen moi nhan su kien da gan deu mat. Danh thiep
khong mat dong nao.

DI TOI CHU KHONG DOWNGRADE: DB dev cua ca hai nguoi dang o `0013`; quay nguoc thi lich su tren
`main` va tren may hai nguoi le nhau.

KHONG siet `image_path` ve lai NOT NULL: siet lai thi phai chon giua xoa nhung lien he da nhap tu
chu ky va ghi vao do mot chuoi rong -- dung cai bay ma `0013` sinh ra de tranh. Cot `source` thi
drop vi khong con ai doc.

File nay khong dau tieng Viet: xem canh bao o dau alembic.ini (I-07).
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_index("ix_business_cards_user_id_event_id", table_name="business_cards")
    op.drop_column("business_cards", "event_id")
    op.drop_column("business_cards", "source")

    op.drop_index("ix_events_one_active", table_name="events")
    op.drop_index("ix_events_user_id_name", table_name="events")
    op.drop_index("ix_events_user_id", table_name="events")
    op.drop_table("events")


def downgrade() -> None:
    """Dung lai bang va hai cot. **Nhan su kien da gan thi khong the khoi phuc** -- chung di
    cung cot `event_id` o `upgrade()`, khong co ban sao nao o dau ca."""
    op.create_table(
        "events",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("name_normalized", sa.String(120), nullable=False),
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
        sa.Column("source", sa.String(16), nullable=False, server_default="scan"),
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
