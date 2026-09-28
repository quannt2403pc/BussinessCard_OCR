import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (
        # Một index cho cả hai chiều đọc: danh sách công ty và keyset export.
        Index("ix_companies_display_name_id", "display_name", "id"),
        # Chống trùng công ty theo từng không gian, không toàn cục.
        Index(
            "ix_companies_workspace_id_name_normalized",
            "workspace_id",
            "name_normalized",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    #: Khoá tách dữ liệu: mọi câu `WHERE` lọc qua cột này, không qua `user_id`.
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: Người tạo — KHÔNG phải khoá tách dữ liệu.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name_normalized: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Tên **như in trên danh thiếp**, giữ nguyên chữ viết gốc.
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Bản Việt hoá của `display_name`, chép từ `business_cards.company_name_vi` lúc xác nhận thẻ
    #: — không gọi thêm lượt model nào. `NULL` thì chỗ đọc rơi về `display_name`.
    display_name_vi: Mapped[str | None] = mapped_column(String(255))
    aliases: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )

    @property
    def vi_name(self) -> str:
        """Tên để hiện ra: bản Việt nếu có, không thì bản gốc.

        Một chỗ duy nhất quyết định việc này — rải `a or b` ở từng chỗ đọc thì sót một chỗ là
        danh sách hiện chữ Hàn còn file xuất ra hiện tiếng Việt.
        """
        return self.display_name_vi or self.display_name

    def __repr__(self) -> str:
        return f"<Company {self.id} {self.name_normalized!r}>"


class CompanyProfile(Base):
    __tablename__ = "company_profiles"
    __table_args__ = (
        UniqueConstraint("company_id", name="uq_company_profiles_company_id"),
        Index("ix_company_profiles_industry", "industry", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    #: Khoá tách dữ liệu. Suy ra được qua `company_id` nhưng vẫn gắn thẳng vào đây: lọc mà phải
    #: JOIN thêm một bảng mới biết của ai là chỗ dễ quên, quên một chỗ là rò dữ liệu.
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: Người tạo — KHÔNG phải khoá tách dữ liệu.
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
    #: Lần gần nhất hồ sơ được **đi tra lại** với Internet. Tách khỏi `updated_at`: sửa tay một
    #: trường không phải là đã đối chiếu lại với nguồn.
    last_checked_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<CompanyProfile {self.id} company={self.company_id} status={self.status}>"


class ProfileChange(Base):
    """Một lượt làm mới **có phát hiện khác biệt**.

    Lượt không đổi gì chỉ cập nhật `CompanyProfile.last_checked_at` — ghi cả những lượt ấy thì
    nhật ký đầy dòng "không có gì mới".
    """

    __tablename__ = "profile_changes"
    __table_args__ = (
        # Partial: màn hình chỉ hỏi "còn thay đổi nào chưa xem không"; tên và mệnh đề `WHERE`
        # phải khớp từng chữ với migration.
        Index(
            "ix_profile_changes_unseen",
            "workspace_id",
            "detected_at",
            postgresql_where=text("acknowledged_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    #: Khoá tách dữ liệu: mọi câu `WHERE` lọc qua cột này, không qua `user_id`.
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: **Không có cột người tạo**: thay đổi do máy phát hiện khi đi tra lại, không do ai nhập.
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: `{"changes": {trường: {"old": …, "new": …}}, "missing": [trường]}` — xem
    #: `services/profile_diff.py`. `missing` **không phải** thay đổi: giá trị cũ vẫn được giữ.
    changes: Mapped[dict] = mapped_column(JSONB, nullable=False)
    #: Mã số thuế / tên pháp lý / địa chỉ / website đổi. Mã số thuế đổi nghĩa là **pháp nhân đổi**.
    notable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column()
    detected_at: Mapped[datetime] = mapped_column(
        server_default=text("clock_timestamp()"), nullable=False
    )

    def __repr__(self) -> str:
        return f"<ProfileChange {self.id} company={self.company_id} notable={self.notable}>"


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
