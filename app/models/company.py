import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (
        # ⚠️ Dòng này do **Q** thêm ở task 9.2 vào file của **T** — ngoại lệ có yêu cầu sẵn:
        # `docs/db-tuning.md` mục 5 (T viết ở 9.8) đề nghị Q thêm đúng dòng này **cùng PR** với
        # migration `0004`, vì khai model mà chưa có migration thì `alembic check` báo lệch
        # ngay, còn tách hai PR thì giữa chừng `main` luôn ở trạng thái lệch. **T xác nhận khi
        # review PR.** Không có thay đổi nào khác của Q trong file này.
        # Số đo của T: danh sách công ty trang 1 2.13 → 0.11 ms; export mỗi lô 5.18 → 0.76 ms.
        Index("ix_companies_display_name_id", "display_name", "id"),
        # ⚠️ Lần thứ hai **Q** chạm file của **T**, lần này ở task 12.5 (luật nới D12, quy ước 2:
        # T review PR). Chỉ hai thứ, và cả hai là *bắt buộc để hệ thống ghi được*: cột `user_id`
        # và unique theo người dùng — revision `0005` đã áp vào DB cả hai, nên model thiếu chúng
        # là `INSERT` nào vào `companies` cũng chết (`NOT NULL`), kéo theo cả nút Xác nhận của F1.
        # **Phần lọc theo `user_id` ở F2 (repository / router / matching) vẫn là task 12.6 của T**
        # — ở đây cố ý không chạm dòng nghiệp vụ nào.
        Index("ix_companies_user_id_name_normalized", "user_id", "name_normalized", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name_normalized: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    aliases: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<Company {self.id} {self.name_normalized!r}>"


class CompanyProfile(Base):
    __tablename__ = "company_profiles"
    __table_args__ = (
        UniqueConstraint("company_id", name="uq_company_profiles_company_id"),
        Index("ix_company_profiles_industry", "industry", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Suy ra được qua `company_id`, nhưng `0005` vẫn gắn thẳng vào đây (xem `OWNED_TABLES` trong
    # revision): lọc mà phải JOIN thêm một bảng mới biết của ai là chỗ dễ quên, quên một chỗ là
    # rò dữ liệu. `services/kb.py` đọc đúng cột này để biết chunk hồ sơ thuộc về ai.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    legal_name: Mapped[str | None] = mapped_column(String(255))
    tax_code: Mapped[str | None] = mapped_column(String(64))
    founded_year: Mapped[int | None] = mapped_column(Integer)
    size_label: Mapped[str | None] = mapped_column(String(64))
    employee_range: Mapped[str | None] = mapped_column(String(64))
    industry: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    products: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    address: Mapped[str | None] = mapped_column(Text)
    website: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(64))
    email: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    sources: Mapped[dict | None] = mapped_column(JSONB)
    llm_model: Mapped[str | None] = mapped_column(String(128))
    generated_at: Mapped[datetime | None] = mapped_column()
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft", server_default="draft"
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<CompanyProfile {self.id} company={self.company_id} status={self.status}>"


ACTIVE_JOB_ITEM_STATUSES = ("pending", "running")


class EnrichJob(Base):
    __tablename__ = "enrich_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column()

    def __repr__(self) -> str:
        return f"<EnrichJob {self.id} finished={self.finished_at is not None}>"


class EnrichJobItem(Base):
    __tablename__ = "enrich_job_items"
    __table_args__ = (
        Index(
            "uq_enrich_job_items_active_company",
            "company_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("enrich_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    sourced_fields: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<EnrichJobItem {self.id} company={self.company_id} status={self.status}>"
