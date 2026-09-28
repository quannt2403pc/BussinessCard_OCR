"""Bang users + cot user_id cho 6 bang + doi 2 rang buoc unique sang theo nguoi dung

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-21 17:00:00.000000+07:00

Task 12.3 (Q). Thiet ke o Plan.md muc 3.

⚠️ REVISION NGUY HIEM NHAT CUA DU AN. Ba diem:

1. `user_id` la NOT NULL tren bang DANG CO DU LIEU -> phai theo dung thu tu: them cot NULLABLE,
   nhan du lieu cu ve mot tai khoan khoi tao, MOI dat NOT NULL.
2. Tai khoan khoi tao chi sinh KHI CO DU LIEU CU DE NHAN. May sach khong tao gi -- tao vo dieu
   kien la de lai mot tai khoan la ma khong ai dang nhap duoc: rac co quyen.
3. Doi hai rang buoc unique TOAN CUC thanh unique THEO NGUOI DUNG. De nguyen thi nguoi B upload
   dung tam the A da co se bi tu choi, va cau tu choi do LO RA rang A co tam the ay.

`integration_status` doi luon khoa chinh thanh `(user_id, provider)`.

DOWNGRADE co that nhung **mat du lieu**, va no vap khi hai nguoi da co cung mot ma bam anh. Alembic
chay DDL trong transaction nen lan vap do quay lui sach; muon downgrade that thi phai don trung truoc.

File nay khong dau tieng Viet: xem canh bao o dau alembic.ini (I-07).
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Sau bang mang du lieu cua nguoi dung. `company_profiles` suy ra duoc chu so huu qua FK nhung
#: van gan `user_id` thang vao: loc ma phai JOIN them mot bang la cho de quen, quen mot cho la ro.
OWNED_TABLES: tuple[str, ...] = (
    "business_cards",
    "companies",
    "company_profiles",
    "kb_chunks",
    "chat_sessions",
    "integration_status",
)

#: Tai khoan nhan du lieu cu. Mat khau de o dang KHONG BAO GIO khop duoc -- bam Argon2 luon bat
#: dau bang "$argon2", nen chuoi nay truot moi lan kiem tra.
BOOTSTRAP_EMAIL = "owner@bizcard.local"
BOOTSTRAP_HASH = "!khong-dang-nhap-duoc-cho-toi-khi-dat-lai-mat-khau"


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        # Ten file credential trong `auth-files` cua CLIProxy. Nullable vi nguoi dung moi dang ky
        # thi chua bam ket noi OAuth lan nao. Dung o 13.1.
        sa.Column("cliproxy_auth_file", sa.Text(), nullable=True),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="users_pkey"),
    )
    # Email luu chu thuong (tang app chuan hoa truoc khi ghi) nen unique thuong la du; khong dung
    # index tren `lower(email)` de khoi phai nho mot ham IMMUTABLE khi doc EXPLAIN.
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    bind = op.get_bind()

    # --- Buoc 1: co du lieu cu khong? -------------------------------------------------------
    existing = 0
    for table in OWNED_TABLES:
        existing += bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()

    owner_id: uuid.UUID | None = None
    if existing:
        owner_id = uuid.uuid4()
        bind.execute(
            sa.text(
                "INSERT INTO users (id, email, password_hash, display_name, is_active) "
                "VALUES (:id, :email, :hash, :name, true)"
            ),
            {
                "id": owner_id,
                "email": BOOTSTRAP_EMAIL,
                "hash": BOOTSTRAP_HASH,
                "name": "Chu so huu du lieu cu",
            },
        )

    # --- Buoc 2: them cot nullable roi nhan du lieu cu -------------------------------------
    for table in OWNED_TABLES:
        op.add_column(
            table, sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True)
        )
        if owner_id is not None:
            bind.execute(
                sa.text(f"UPDATE {table} SET user_id = :owner"), {"owner": owner_id}
            )

    # --- Buoc 3: gio moi khoa lai ----------------------------------------------------------
    for table in OWNED_TABLES:
        op.alter_column(table, "user_id", nullable=False)
        op.create_foreign_key(
            f"fk_{table}_user_id_users",
            table,
            "users",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )
        # Moi truy van tu D12 deu loc theo user_id nen index nay phuc vu tat ca, khong chi mot cho.
        op.create_index(f"ix_{table}_user_id", table, ["user_id"])

    # --- Buoc 4: hai rang buoc unique toan cuc -> theo nguoi dung ---------------------------
    op.drop_index("ix_business_cards_image_hash", table_name="business_cards")
    op.create_index(
        "ix_business_cards_user_id_image_hash",
        "business_cards",
        ["user_id", "image_hash"],
        unique=True,
    )
    op.drop_index("ix_companies_name_normalized", table_name="companies")
    op.create_index(
        "ix_companies_user_id_name_normalized",
        "companies",
        ["user_id", "name_normalized"],
        unique=True,
    )

    # --- Buoc 5: integration_status doi khoa chinh -----------------------------------------
    op.drop_constraint("integration_status_pkey", "integration_status", type_="primary")
    op.create_primary_key(
        "integration_status_pkey", "integration_status", ["user_id", "provider"]
    )


def downgrade() -> None:
    op.drop_constraint("integration_status_pkey", "integration_status", type_="primary")
    op.create_primary_key("integration_status_pkey", "integration_status", ["provider"])

    op.drop_index("ix_companies_user_id_name_normalized", table_name="companies")
    op.create_index(
        "ix_companies_name_normalized", "companies", ["name_normalized"], unique=True
    )
    op.drop_index("ix_business_cards_user_id_image_hash", table_name="business_cards")
    op.create_index(
        "ix_business_cards_image_hash", "business_cards", ["image_hash"], unique=True
    )

    for table in OWNED_TABLES:
        op.drop_index(f"ix_{table}_user_id", table_name=table)
        op.drop_constraint(f"fk_{table}_user_id_users", table, type_="foreignkey")
        op.drop_column(table, "user_id")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
