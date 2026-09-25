"""Go bang su kien va cot nguon: NEXT-03 va NEXT-08 bi cat khoi pham vi

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-25 18:00:00.000000+07:00

Task NEXT-03 + NEXT-08 (T) -- **ca hai bi cat khoi pham vi 2026-09-25** theo quyet dinh cua
nhom sau khi ban bac. Cung ngoai le nhu `0008`-`0013`: Q cho phep T tu sinh revision cho rieng
cac dong NEXT-xx.

RUI RO: **trung binh -- day la revision duy nhat trong du an lam MAT du lieu.** Bang `events`
va cot `business_cards.event_id` bien mat cung nhau, nen moi nhan su kien da gan deu mat. Danh
thiep thi khong mat mot dong nao: khoa ngoai la `SET NULL`, va o day ta drop ca cot chu khong
dong vao bang the.

DI TOI CHU KHONG DOWNGRADE:

  DB dev cua ca hai nguoi dang o `0013`. Quay nguoc bang `alembic downgrade` thi lich su tren
  `main` va lich su tren may hai nguoi le nhau -- ai pull ve sau se chay mot chuoi revision
  khong ai tung chay. Mot revision di toi thi moi may deu di qua cung mot duong.

VI SAO KHONG SIET `image_path` VE LAI NOT NULL:

  Cot nay duoc noi ra o `0013` cho `NEXT-08`. Siet lai thi phai chon giua **xoa nhung lien he
  da nhap tu chu ky** va **ghi vao do mot chuoi rong** -- ma chuoi rong la mot duong dan *hop
  le* tro vao thu muc goc cua volume, dung cai bay ma `0013` sinh ra de tranh.

  Mot cot cho phep NULL ma khong con duong nao ghi NULL vao la vo hai. Siet lai moi la huong
  co rui ro. Nen `image_path` giu nguyen nullable, va `models/card.py` ghi ro ly do.

COT `source` THI DROP:

  Khac `image_path`, cot nay khong con ai doc: no sinh ra chi de phan biet `scan` voi
  `signature`, ma mot trong hai ve khong ton tai nua. Giu lai la de mot cot chi co dung mot gia
  tri va mot cau hoi cho nguoi doc ma nguon sau nay.
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
