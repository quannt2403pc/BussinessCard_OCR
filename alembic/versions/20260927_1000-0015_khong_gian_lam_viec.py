"""Khong gian lam viec: doi khoa tach du lieu tu user_id sang workspace_id

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-27 10:00:00.000000+07:00

Task NEXT-05. Plan.md muc 1.4 va tieu chi A9 da sua trong commit rieng truoc revision nay.
Rui ro cao nhat du an: sai o day lam ro du lieu giua hai to chuc.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | None = None
depends_on: str | None = None

#: Bang mang du lieu cua to chuc. `user_id` o lai lam "nguoi tao", khong con la khoa tach.
SCOPED_TABLES = (
    "business_cards",
    "companies",
    "company_profiles",
    "kb_chunks",
    "chat_sessions",
    "contact_notes",
    "profile_changes",
    "privacy_logs",
)


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "workspace_members",
        sa.Column(
            "workspace_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", sa.String(16), nullable=False, server_default="member"),
        sa.Column("joined_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_workspace_members_user_id", "workspace_members", ["user_id"])

    op.add_column(
        "users",
        sa.Column(
            "active_workspace_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # Moi nguoi dung dang co duoc mot khong gian rieng, ho lam `admin`. Sau buoc nay he thong
    # hanh xu y het truoc do cho toi khi co ai moi them thanh vien.
    op.execute(
        "INSERT INTO workspaces (id, name, created_at, updated_at) "
        "SELECT gen_random_uuid(), "
        "left(coalesce(nullif(u.display_name, ''), split_part(u.email, '@', 1)), 120), "
        "now(), now() FROM users u"
    )
    # Ghep theo thu tu chu khong theo ten: ten co the trung sau khi cat con 120 ky tu.
    op.execute(
        "WITH u AS (SELECT id, row_number() OVER (ORDER BY created_at, id) AS n FROM users), "
        "w AS (SELECT id, row_number() OVER (ORDER BY created_at, id) AS n FROM workspaces) "
        "INSERT INTO workspace_members (workspace_id, user_id, role, joined_at) "
        "SELECT w.id, u.id, 'admin', now() FROM u JOIN w ON w.n = u.n"
    )
    op.execute(
        "UPDATE users u SET active_workspace_id = m.workspace_id "
        "FROM workspace_members m WHERE m.user_id = u.id"
    )

    for table in SCOPED_TABLES:
        op.add_column(
            table,
            sa.Column(
                "workspace_id",
                sa.UUID(as_uuid=True),
                sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                nullable=True,
            ),
        )
        op.execute(
            f"UPDATE {table} t SET workspace_id = m.workspace_id "
            "FROM workspace_members m WHERE m.user_id = t.user_id"
        )
        op.alter_column(table, "workspace_id", nullable=False)
        op.create_index(f"ix_{table}_workspace_id", table, ["workspace_id"])

    # Hai rang buoc unique phai doi theo. De nguyen theo `user_id` thi hai thanh vien cung mot
    # khong gian quet dung mot tam the se tao ra hai ban ghi.
    op.drop_index("ix_business_cards_user_id_image_hash", table_name="business_cards")
    op.create_index(
        "ix_business_cards_workspace_id_image_hash",
        "business_cards",
        ["workspace_id", "image_hash"],
        unique=True,
    )
    op.drop_index("ix_companies_user_id_name_normalized", table_name="companies")
    op.create_index(
        "ix_companies_workspace_id_name_normalized",
        "companies",
        ["workspace_id", "name_normalized"],
        unique=True,
    )

    # Ba index loc theo nguoi dung nay loc theo khong gian.
    op.drop_index("ix_business_cards_follow_up", table_name="business_cards")
    op.create_index(
        "ix_business_cards_follow_up",
        "business_cards",
        ["workspace_id", "follow_up_at"],
        postgresql_where=sa.text("follow_up_at IS NOT NULL"),
    )
    op.drop_index("ix_profile_changes_unseen", table_name="profile_changes")
    op.create_index(
        "ix_profile_changes_unseen",
        "profile_changes",
        ["workspace_id", "detected_at"],
        postgresql_where=sa.text("acknowledged_at IS NULL"),
    )
    op.drop_index("ix_privacy_logs_user_id_created_at", table_name="privacy_logs")
    op.create_index(
        "ix_privacy_logs_workspace_id_created_at",
        "privacy_logs",
        ["workspace_id", sa.text("created_at DESC")],
    )

    # `SET NULL`: xoa mot tai khoan thi lien he o lai voi to chuc, chi mat nguoi phu trach.
    op.add_column(
        "business_cards",
        sa.Column(
            "assigned_to_user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_business_cards_assigned_to",
        "business_cards",
        ["workspace_id", "assigned_to_user_id"],
        postgresql_where=sa.text("assigned_to_user_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Quay ve tach du lieu theo tai khoan.

    Du lieu khong mat dong nao, nhung quan he "cua chung" thi mat: moi ban ghi tro lai thuoc
    dung nguoi da tao ra no, nhung thanh vien khac mat quyen doc.
    """
    op.drop_index("ix_business_cards_assigned_to", table_name="business_cards")
    op.drop_column("business_cards", "assigned_to_user_id")

    op.drop_index("ix_privacy_logs_workspace_id_created_at", table_name="privacy_logs")
    op.create_index(
        "ix_privacy_logs_user_id_created_at",
        "privacy_logs",
        ["user_id", sa.text("created_at DESC")],
    )
    op.drop_index("ix_profile_changes_unseen", table_name="profile_changes")
    op.create_index(
        "ix_profile_changes_unseen",
        "profile_changes",
        ["user_id", "detected_at"],
        postgresql_where=sa.text("acknowledged_at IS NULL"),
    )
    op.drop_index("ix_business_cards_follow_up", table_name="business_cards")
    op.create_index(
        "ix_business_cards_follow_up",
        "business_cards",
        ["user_id", "follow_up_at"],
        postgresql_where=sa.text("follow_up_at IS NOT NULL"),
    )

    op.drop_index("ix_companies_workspace_id_name_normalized", table_name="companies")
    op.create_index(
        "ix_companies_user_id_name_normalized",
        "companies",
        ["user_id", "name_normalized"],
        unique=True,
    )
    op.drop_index("ix_business_cards_workspace_id_image_hash", table_name="business_cards")
    op.create_index(
        "ix_business_cards_user_id_image_hash",
        "business_cards",
        ["user_id", "image_hash"],
        unique=True,
    )

    for table in SCOPED_TABLES:
        op.drop_index(f"ix_{table}_workspace_id", table_name=table)
        op.drop_column(table, "workspace_id")

    op.drop_column("users", "active_workspace_id")
    op.drop_index("ix_workspace_members_user_id", table_name="workspace_members")
    op.drop_table("workspace_members")
    op.drop_table("workspaces")
