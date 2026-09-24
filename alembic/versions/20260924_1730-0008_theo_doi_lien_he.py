"""Trang thai quan he + ngay hen lien he lai + bang ghi chu theo doi

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-24 17:30:00.000000+07:00

Task NEXT-01 (T). Quy uoc so 5 noi "chi Q sinh revision"; **Q cho phep T tu sinh cho rieng cac
dong NEXT-xx, 2026-09-24** -- ngoai le co ghi ngay, moi viec khac van theo quy uoc cu.

RUI RO: thap, cung muc voi 0006. Hai cot moi tren `business_cards` va mot bang moi.
`relationship_status` la NOT NULL nhung co `server_default='new'`, nen Postgres dien san cho moi
dong dang co -- khong buoc phai viet lai bang (Postgres 11+ luu default vao catalog).

VI SAO KHONG CO COT "nguoi phu trach":

  Tu D12 moi ban ghi da thuoc dung mot nguoi (`user_id`, tieu chi A9) va **khong co cach nao
  giao viec cho nguoi khac** -- chia se du lieu giua cac tai khoan la NEXT-05, va chinh no se
  doi `user_id` thanh `workspace_id`. Them cot `owner_user_id` bay gio la them mot cot luon
  bang `user_id`, roi NEXT-05 lai phai go ra.

VI SAO GHI CHU LA BANG RIENG chu khong noi them vao cot `notes` san co:

  `business_cards.notes` la ghi chu **ve tam the** (may ghi vao khi quet hong, nguoi dung sua
  tay). Con day la **dong thoi gian cua mot quan he**: nhieu dong, moi dong co moc thoi gian,
  doc theo thu tu nguoc. Nhet ca hai vao mot o van ban la mat moc thoi gian va mat luon kha nang
  dem "lan lien he gan nhat la bao gio".

INDEX: mot index cho dung cau hoi cua khoi *Can lien he hom nay* tren trang chu --
`WHERE user_id = ? AND follow_up_at <= ?`. Cot `follow_up_at` phan lon la NULL nen dung index
**partial**: chi so hoa dung nhung dong co hen, bang nho hon han va cau tren van dung duoc no.
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
        # `user_id` co mat du suy ra duoc qua `card_id`: moi truy van deu loc theo nguoi dung
        # (12.6), ma JOIN them mot bang moi biet cua ai la cho de quen -- quen mot cho la ro.
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("card_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        # `clock_timestamp()` chứ không `now()`: `now()` trả về giờ **bắt đầu transaction**,
        # nên hai ghi chú thêm trong cùng một transaction có cùng mốc y hệt và dòng thời gian
        # mất thứ tự. Bảng này tồn tại chính là để giữ thứ tự ấy.
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
