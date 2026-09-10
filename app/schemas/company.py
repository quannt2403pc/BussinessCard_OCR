"""CompanyProfileSchema + SourceRef (mỗi trường kèm nguồn).

Chủ sở hữu: T | Task: 2.8 | xem Task.md

Nguyên tắc chống bịa (rủi ro R4, Plan.md mục 6): **trường nào không có URL nguồn kiểm chứng
được thì phải là `None`**. Thà thiếu còn hơn sai — tiêu chí A5 chỉ đòi ≥ 5 trường có nguồn.
Việc *thực thi* quy tắc này (loại bỏ trường không nguồn, gắn nhãn `unverified`) là task 4.8;
ở đây chỉ định nghĩa cấu trúc dữ liệu và cung cấp hàm kiểm tra.

Tên trường khớp 1-1 với cột bảng `company_profiles` trong migration `0001` để repository
(task 4.6) map thẳng, không phải đổi tên.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Các trường nội dung **bắt buộc phải có nguồn** mới được giữ giá trị (rủi ro R4).
#: Không gồm `description` vì đó là văn bản tổng hợp từ nhiều nguồn, kiểm chứng theo câu không
#: khả thi trong phạm vi demo — nhưng vẫn nên có ít nhất một nguồn ở mức toàn hồ sơ.
SOURCED_FIELDS: tuple[str, ...] = (
    "legal_name",
    "tax_code",
    "founded_year",
    "size_label",
    "employee_range",
    "industry",
    "products",
    "address",
    "website",
    "phone",
    "email",
)


class ProfileStatus(StrEnum):
    """Vòng đời một hồ sơ doanh nghiệp (Plan.md mục 3)."""

    DRAFT = "draft"  # đã tạo bản ghi, chưa enrich xong
    GENERATED = "generated"  # LLM sinh xong, chưa ai kiểm
    VERIFIED = "verified"  # người dùng đã sửa/duyệt tay (task 6.8)


class SourceRef(BaseModel):
    """Một nguồn trích dẫn cho một trường của hồ sơ.

    `url` giữ kiểu `str` chứ không dùng `HttpUrl`: LLM hay trả URL thiếu scheme hoặc kèm ký tự
    thừa, `HttpUrl` sẽ ném lỗi và làm hỏng cả hồ sơ thay vì chỉ bỏ một nguồn. Chuẩn hoá nhẹ ở
    validator bên dưới, phần lọc nghiêm ngặt để task 4.8 xử lý.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    url: str
    title: str | None = None
    retrieved_at: datetime | None = None

    @field_validator("url")
    @classmethod
    def add_scheme_if_missing(cls, v: str) -> str:
        if v and not v.startswith(("http://", "https://")):
            return f"https://{v}"
        return v


class CompanyProfileSchema(BaseModel):
    """Hồ sơ doanh nghiệp đối tác — kết quả của luồng enrichment (F2).

    Mọi trường nội dung đều `None` được: không tìm thấy thì để trống, **không đoán**.
    `sources` ánh xạ *tên trường* → danh sách nguồn, ví dụ::

        {"tax_code": [{"url": "https://...", "title": "Cổng TT đăng ký DN"}]}
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    # --- Định danh pháp lý ---
    legal_name: str | None = None
    tax_code: str | None = None
    founded_year: int | None = Field(default=None, ge=1800, le=2100)

    # --- Quy mô & ngành nghề ---
    size_label: str | None = None
    employee_range: str | None = None
    industry: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)

    # --- Liên hệ ---
    address: str | None = None
    website: str | None = None
    phone: str | None = None
    email: str | None = None

    # --- Mô tả & nguồn ---
    description: str | None = None
    sources: dict[str, list[SourceRef]] = Field(default_factory=dict)

    @field_validator("industry", "products", mode="before")
    @classmethod
    def split_comma_separated(cls, v: object) -> object:
        """LLM hay trả `"Logistics, Kho vận"` thay vì mảng — tách ra thay vì ném lỗi."""
        if isinstance(v, str):
            return [part.strip() for part in v.split(",") if part.strip()]
        if v is None:
            return []
        return v

    def fields_with_value(self) -> set[str]:
        """Tên các trường trong `SOURCED_FIELDS` đang thực sự có dữ liệu."""
        filled = set()
        for name in SOURCED_FIELDS:
            if getattr(self, name) not in (None, "", []):
                filled.add(name)
        return filled

    def fields_missing_source(self) -> set[str]:
        """Trường có giá trị nhưng **không** có nguồn nào — task 4.8 sẽ xoá hoặc gắn `unverified`."""
        return {name for name in self.fields_with_value() if not self.sources.get(name)}

    def sourced_field_count(self) -> int:
        """Đếm trường vừa có giá trị vừa có nguồn — tiêu chí nghiệm thu A5 đòi ≥ 5."""
        return len(self.fields_with_value() - self.fields_missing_source())


class CompanyProfileOut(CompanyProfileSchema):
    """Hồ sơ trả về cho UI (task 5.5 / 6.6), kèm metadata do hệ thống sinh."""

    llm_model: str | None = None
    generated_at: datetime | None = None
    status: ProfileStatus = ProfileStatus.DRAFT
    #: Trường có giá trị nhưng chưa kiểm chứng được nguồn — UI hiện nhãn "chưa xác minh".
    unverified_fields: list[str] = Field(default_factory=list)
