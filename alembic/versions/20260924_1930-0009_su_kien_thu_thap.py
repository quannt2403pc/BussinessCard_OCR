"""Bang su kien thu thap + gan the vao su kien

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-24 19:30:00.000000+07:00

Task NEXT-03 (T). Cung ngoai le nhu `0008`: quy uoc so 5 noi "chi Q sinh revision", **Q cho phep
T tu sinh cho rieng cac dong NEXT-xx, 2026-09-24**. Moi viec khac van theo quy uoc cu.

RUI RO: thap. Mot bang moi va mot cot NULL duoc tren `business_cards` -- khong viet lai bang,
khong doi kieu cot nao dang co.

VI SAO LA BANG CHU KHONG PHAI MOT COT CHU TREN `business_cards`:

  Su kien thi it ma danh thiep thi nhieu. Ten su kien hay bi go sai o tam thu nam muoi, va sua
  mot cho phai sua duoc cho ca lo. Bang rieng con cho gan ngay dien ra va dem so the ma khong
  phai GROUP BY tren chuoi.

VI SAO `business_cards.event_id` LA SET NULL CHU KHONG CASCADE:

  Xoa nhan mot hoi cho **khong duoc** keo theo may tram tam danh thiep thu ve tu hoi cho do.
  Mat nhan con gan lai duoc, mat the thi khong.

HAI INDEX UNIQUE, MOI CAI MOT VIEC:

  - `ix_events_user_id_name`: chong trung theo tung nguoi dung, cung luat voi `companies`
    (Plan.md muc 3). Hai nguoi cung di mot hoi cho thi ai cung co su kien cua minh.
  - `ix_events_one_active` (partial, WHERE is_active): **nhieu nhat mot su kien dang dien ra cho
    moi nguoi**. Co nay quyet dinh the vua quet duoc dong dau vao dau; hai dong cung bat thi viec
    chon dong nao thanh ngau nhien theo thu tu Postgres tra ve.

KHONG CO BUOC DOI DU LIEU CHO `relationship_status`:

  NEXT-03 tach ket cuc thanh `won` / `lost` o tang ung dung (cot la VARCHAR(16), khong co CHECK
  nen DDL khong phai doi gi). Nhung dong dang mang `closed` **giu nguyen**: `closed` co nghia cu
  la *da dung, khong ro thang hay thua*. Gan bua chung sang `lost` la bia ra du lieu chua ai
  nhap, va bia thang vao con so ma ca bao cao dua len.
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
