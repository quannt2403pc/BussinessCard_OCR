import json
import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.card import CardStatus, RelationshipStatus
from app.schemas.company import ProfileStatus, SourceRef, as_utc

EXPORT_BATCH_SIZE = 200
CSV_BOM = "﻿"
LIST_SEPARATOR = "; "


def csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return LIST_SEPARATOR.join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False) if value else ""
    return str(value)


class ExportRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def columns(cls) -> tuple[str, ...]:
        return tuple(cls.model_fields)

    def csv_row(self) -> list[str]:
        data = self.model_dump(mode="json")
        return [csv_cell(data[name]) for name in self.columns()]


class CardExportRow(ExportRow):
    id: uuid.UUID
    full_name: str | None = None
    full_name_vi: str | None = None
    job_title: str | None = None
    job_title_vi: str | None = None
    company_name_raw: str | None = None
    company_name_vi: str | None = None
    company_name: str | None = None
    company_id: uuid.UUID | None = None
    email: str | None = None
    phone: str | None = None
    phone_alt: str | None = None
    address: str | None = None
    address_vi: str | None = None
    website: str | None = None
    language_detected: str | None = None
    status: CardStatus
    relationship_status: RelationshipStatus = RelationshipStatus.NEW
    follow_up_at: date | None = None
    event_name: str | None = None
    notes: str | None = None
    uploaded_at: datetime
    updated_at: datetime

    def display_name(self) -> str:
        """Tên hiển thị trên danh bạ điện thoại: **bản Việt hoá trước**, bản in trên thẻ sau.

        Danh bạ điện thoại tìm kiếm bằng bàn phím Latin, nên `Kim Min-jun` tra được còn `김민수`
        thì không. Bản gốc không mất: nó đi vào `NOTE` của vCard (xem `vcard()`).
        """
        return self.full_name_vi or self.full_name or "(chưa có tên)"

    def organisation(self) -> str | None:
        return self.company_name_vi or self.company_name or self.company_name_raw

    def vcard(self) -> str:
        return render_vcard(self)

    @field_validator("uploaded_at", "updated_at")
    @classmethod
    def assume_utc(cls, v: datetime | None) -> datetime | None:
        return as_utc(v)


class CompanyExportRow(ExportRow):
    company_id: uuid.UUID
    display_name: str
    aliases: list[str] = Field(default_factory=list)
    contact_count: int
    profile_status: ProfileStatus | None = None
    legal_name: str | None = None
    tax_code: str | None = None
    founded_year: int | None = None
    size_label: str | None = None
    employee_range: str | None = None
    industry: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    address: str | None = None
    website: str | None = None
    phone: str | None = None
    email: str | None = None
    description: str | None = None
    sources: dict[str, list[SourceRef]] = Field(default_factory=dict)
    llm_model: str | None = None
    generated_at: datetime | None = None

    @field_validator("aliases", "industry", "products", mode="before")
    @classmethod
    def none_as_empty_list(cls, v: object) -> object:
        return [] if v is None else v

    @field_validator("sources", mode="before")
    @classmethod
    def none_as_empty_dict(cls, v: object) -> object:
        return {} if v is None else v

    @field_validator("generated_at")
    @classmethod
    def assume_utc(cls, v: datetime | None) -> datetime | None:
        return as_utc(v)


class ExportMeta(BaseModel):
    exported_at: datetime
    total: int
    filters: dict[str, str] = Field(default_factory=dict)


class CardsExportOut(ExportMeta):
    items: list[CardExportRow]


class CompaniesExportOut(ExportMeta):
    items: list[CompanyExportRow]


# --------------------------------------------------------------------------- vCard (NEXT-02)

#: Ký tự phải thoát trong giá trị vCard 3.0 (RFC 2426 mục 2.4.2), theo đúng thứ tự: dấu chéo
#: ngược trước, nếu không thì chính dấu chéo ta vừa thêm lại bị thoát lần nữa.
VCARD_ESCAPES: tuple[tuple[str, str], ...] = (
    ("\\", "\\\\"),
    (";", "\\;"),
    (",", "\\,"),
    ("\n", "\\n"),
)

#: Giới hạn dòng của vCard tính bằng **octet**, không phải ký tự (RFC 2426 mục 2.6). Một chữ
#: tiếng Việt có dấu chiếm 2–3 octet, nên đếm theo ký tự là gập sai chỗ và Outlook đọc ra rác.
VCARD_LINE_OCTETS = 75

VCARD_MEDIA_TYPE = "text/vcard; charset=utf-8"


def vcard_escape(value: str) -> str:
    for old, new in VCARD_ESCAPES:
        value = value.replace(old, new)
    return value


def vcard_fold(line: str) -> str:
    """Gập dòng dài theo RFC 2426: cắt ở ranh giới **octet**, dòng tiếp theo bắt đầu bằng một dấu cách.

    Cắt giữa một ký tự nhiều byte là hỏng cả tệp, nên chỗ cắt luôn lùi về đầu ký tự gần nhất.
    """
    raw = line.encode("utf-8")
    if len(raw) <= VCARD_LINE_OCTETS:
        return line

    parts: list[str] = []
    start = 0
    limit = VCARD_LINE_OCTETS
    while start < len(raw):
        cut = min(start + limit, len(raw))
        while cut > start and cut < len(raw) and (raw[cut] & 0xC0) == 0x80:
            cut -= 1
        parts.append(raw[start:cut].decode("utf-8"))
        start = cut
        limit = VCARD_LINE_OCTETS - 1
    return "\r\n ".join(parts)


def vcard_line(name: str, value: str | None) -> list[str]:
    """Một dòng vCard, hoặc danh sách rỗng khi không có giá trị — để chỗ gọi cứ nối thẳng."""
    if value is None or not str(value).strip():
        return []
    return [vcard_fold(f"{name}:{vcard_escape(str(value).strip())}")]


def _name_parts(full_name: str) -> tuple[str, str]:
    """`"Nguyễn Văn An"` → `("Nguyễn", "Văn An")`.

    Tiếng Việt (và cả tiếng Hàn, Nhật, Trung) đặt **họ trước**, nên từ đầu tiên là họ. Đây là quy
    ước của phần lớn danh thiếp hệ thống này đọc; sai với tên phương Tây viết đủ, nhưng `FN` mới
    là thứ danh bạ hiển thị, `N` chỉ dùng để sắp xếp.
    """
    words = full_name.split()
    if len(words) < 2:
        return "", full_name
    return words[0], " ".join(words[1:])


def render_vcard(row: "CardExportRow") -> str:
    """Một danh thiếp → chuỗi vCard 3.0.

    Chọn 3.0 chứ không 4.0: đây là bản mà danh bạ Android, iOS, Outlook và Google Contacts đều
    đọc được mà không cần chuyển đổi.
    """
    display = row.display_name()
    family, given = _name_parts(display)

    lines = ["BEGIN:VCARD", "VERSION:3.0"]
    lines += vcard_line("FN", display)
    lines += [vcard_fold(f"N:{vcard_escape(family)};{vcard_escape(given)};;;")]
    lines += vcard_line("ORG", row.organisation())
    lines += vcard_line("TITLE", row.job_title_vi or row.job_title)
    lines += vcard_line("EMAIL;TYPE=INTERNET,WORK", row.email)
    lines += vcard_line("TEL;TYPE=WORK,VOICE", row.phone)
    lines += vcard_line("TEL;TYPE=CELL", row.phone_alt)
    if address := (row.address_vi or row.address):
        lines += [vcard_fold(f"ADR;TYPE=WORK:;;{vcard_escape(address)};;;;")]
    lines += vcard_line("URL", row.website)
    lines += vcard_line("NOTE", _note(row))
    lines += vcard_line("UID", f"urn:uuid:{row.id}")
    lines += vcard_line("REV", row.updated_at.strftime("%Y%m%dT%H%M%SZ"))
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


def _note(row: "CardExportRow") -> str | None:
    """Ghi chú của người dùng, cộng bản gốc in trên thẻ khi nó khác bản Việt hoá.

    Mất bản gốc là mất thứ duy nhất đối chiếu lại được với tấm thẻ, nên nó phải đi cùng — nhưng
    xuống `NOTE` chứ không tranh chỗ của `FN`, vì danh bạ hiển thị `FN`.
    """
    parts: list[str] = []
    if row.full_name_vi and row.full_name and row.full_name_vi != row.full_name:
        parts.append(f"Tên trên thẻ: {row.full_name}")
    original_org = row.company_name or row.company_name_raw
    if row.company_name_vi and original_org and row.company_name_vi != original_org:
        parts.append(f"Công ty trên thẻ: {original_org}")
    if row.notes:
        parts.append(row.notes)
    return "\n".join(parts) if parts else None
