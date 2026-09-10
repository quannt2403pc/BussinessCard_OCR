"""Khởi tạo schema: 6 nhóm bảng + extension vector

Revision ID: 0001
Revises:
Create Date: 2026-09-10 09:00:00+07:00

Chủ sở hữu: Q | Task: 1.6 — chỉ Q được sinh revision (quy ước số 5, Task.md).

Viết tay chứ không autogenerate: hai bảng `companies` / `company_profiles` thuộc F2, model ORM
nằm ở `app/models/company.py` do **T** sở hữu và hiện còn là stub. Task 1.6 giao cho Q tạo đủ
6 nhóm bảng, nên bảng được tạo ở đây; T khai model sau mà không cần sinh revision mới.

Index `ivfflat` trên `kb_chunks.embedding` KHÔNG tạo ở đây — task 6.3 tạo, khi KB đã có dữ liệu
để index học phân cụm.
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Số chiều vector — 384 = `intfloat/multilingual-e5-small` (Plan.md mục 2.6, chốt ở task 2.6).
#: Đổi model khác số chiều thì phải có revision mới đổi kiểu cột, xong TRƯỚC D6.
EMBEDDING_DIM = 384


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ---------------------------------------------------------------- companies (F2 — T dùng)
    op.create_table(
        "companies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        # Tên đã chuẩn hoá bởi normalize_company.normalize_company_name() (task 3.7) — khoá dedupe.
        sa.Column("name_normalized", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("aliases", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_companies_name_normalized", "companies", ["name_normalized"], unique=True
    )

    # -------------------------------------------------------- company_profiles (F2 — T dùng)
    op.create_table(
        "company_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("legal_name", sa.String(255), nullable=True),
        sa.Column("tax_code", sa.String(64), nullable=True),
        sa.Column("founded_year", sa.Integer(), nullable=True),
        sa.Column("size_label", sa.String(64), nullable=True),
        sa.Column("employee_range", sa.String(64), nullable=True),
        sa.Column("industry", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("products", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("website", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(64), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        # {ten_truong: [{"url": ..., "title": ...}]} — rủi ro R4: trường không nguồn phải để null.
        sa.Column("sources", postgresql.JSONB(), nullable=True),
        sa.Column("llm_model", sa.String(128), nullable=True),
        sa.Column("generated_at", sa.DateTime(), nullable=True),
        # draft | generated | verified
        sa.Column(
            "status", sa.String(16), server_default="draft", nullable=False
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # Mỗi công ty đúng một hồ sơ: nút "Tạo lại hồ sơ" (task 6.7) ghi đè chứ không sinh bản mới.
        sa.UniqueConstraint("company_id", name="uq_company_profiles_company_id"),
    )
    # GIN để lọc theo ngành nghề (Plan.md mục 3).
    op.create_index(
        "ix_company_profiles_industry",
        "company_profiles",
        ["industry"],
        postgresql_using="gin",
    )

    # ------------------------------------------------------------- business_cards (F1 — Q dùng)
    op.create_table(
        "business_cards",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("image_path", sa.Text(), nullable=False),
        sa.Column("image_hash", sa.String(64), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("ocr_raw_json", postgresql.JSONB(), nullable=True),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("job_title", sa.String(255), nullable=True),
        sa.Column("company_name_raw", sa.String(255), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(64), nullable=True),
        sa.Column("phone_alt", sa.String(64), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("website", sa.String(255), nullable=True),
        sa.Column("language_detected", sa.String(16), nullable=True),
        sa.Column("confidence", postgresql.JSONB(), nullable=True),
        # pending | needs_review | confirmed
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Unique: upload lại đúng ảnh đó thì không tạo bản ghi trùng (task 3.1).
    op.create_index(
        "ix_business_cards_image_hash", "business_cards", ["image_hash"], unique=True
    )
    op.create_index("ix_business_cards_status", "business_cards", ["status"])
    op.create_index("ix_business_cards_company_id", "business_cards", ["company_id"])

    # ------------------------------------------------------------------- kb_chunks (F3 — Q dùng)
    op.create_table(
        "kb_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        # card | company_profile
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(EMBEDDING_DIM), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_kb_chunks_source", "kb_chunks", ["source_type", "source_id"])

    # ------------------------------------------------------- chat_sessions / chat_messages (F3)
    op.create_table(
        "chat_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        # user | assistant
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"])

    # ---------------------------------------------------------------- integration_status (D2)
    op.create_table(
        "integration_status",
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("connected", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("account_label", sa.String(255), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("provider"),
    )


def downgrade() -> None:
    op.drop_table("integration_status")
    op.drop_index("ix_chat_messages_session_id", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
    op.drop_index("ix_kb_chunks_source", table_name="kb_chunks")
    op.drop_table("kb_chunks")
    op.drop_index("ix_business_cards_company_id", table_name="business_cards")
    op.drop_index("ix_business_cards_status", table_name="business_cards")
    op.drop_index("ix_business_cards_image_hash", table_name="business_cards")
    op.drop_table("business_cards")
    op.drop_index("ix_company_profiles_industry", table_name="company_profiles")
    op.drop_table("company_profiles")
    op.drop_index("ix_companies_name_normalized", table_name="companies")
    op.drop_table("companies")
    # Cố ý KHÔNG drop extension `vector`: có thể còn schema khác trong DB đang dùng.
