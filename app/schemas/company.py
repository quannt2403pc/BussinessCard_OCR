import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.card import CardOut

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
    DRAFT = "draft"
    GENERATED = "generated"
    VERIFIED = "verified"


class SourceRef(BaseModel):
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
    model_config = ConfigDict(str_strip_whitespace=True)

    legal_name: str | None = Field(default=None, max_length=255)
    tax_code: str | None = Field(default=None, max_length=64)
    founded_year: int | None = Field(default=None, ge=1800, le=2100)

    size_label: str | None = Field(default=None, max_length=64)
    employee_range: str | None = Field(default=None, max_length=64)
    industry: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)

    address: str | None = None
    website: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=255)

    description: str | None = None
    sources: dict[str, list[SourceRef]] = Field(default_factory=dict)

    @field_validator("industry", "products", mode="before")
    @classmethod
    def split_comma_separated(cls, v: object) -> object:
        if isinstance(v, str):
            return [part.strip() for part in v.split(",") if part.strip()]
        if v is None:
            return []
        return v

    def fields_with_value(self) -> set[str]:
        filled = set()
        for name in SOURCED_FIELDS:
            if getattr(self, name) not in (None, "", []):
                filled.add(name)
        return filled

    def fields_missing_source(self) -> set[str]:
        return {name for name in self.fields_with_value() if not self.sources.get(name)}

    def sourced_field_count(self) -> int:
        return len(self.fields_with_value() - self.fields_missing_source())


class CompanyProfileUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    legal_name: str | None = Field(default=None, max_length=255)
    tax_code: str | None = Field(default=None, max_length=64)
    founded_year: int | None = Field(default=None, ge=1800, le=2100)
    size_label: str | None = Field(default=None, max_length=64)
    employee_range: str | None = Field(default=None, max_length=64)
    industry: list[str] | None = None
    products: list[str] | None = None
    address: str | None = None
    website: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=255)
    description: str | None = None

    @field_validator("industry", "products")
    @classmethod
    def none_as_empty(cls, v: list[str] | None) -> list[str]:
        return v or []

    def changes(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


class CompanyProfileOut(CompanyProfileSchema):
    llm_model: str | None = None
    generated_at: datetime | None = None
    status: ProfileStatus = ProfileStatus.DRAFT
    unverified_fields: list[str] = Field(default_factory=list)

    @field_validator("generated_at")
    @classmethod
    def assume_utc(cls, v: datetime | None) -> datetime | None:
        return as_utc(v)


class CompanyListItem(BaseModel):
    id: uuid.UUID
    display_name: str
    name_normalized: str
    contact_count: int
    profile_status: ProfileStatus | None = None
    profile_generated_at: datetime | None = None

    @field_validator("profile_generated_at")
    @classmethod
    def assume_utc(cls, v: datetime | None) -> datetime | None:
        return as_utc(v)


class CompanyListOut(BaseModel):
    items: list[CompanyListItem]
    total: int
    page: int
    size: int
    pages: int


class CompanyDetailOut(CompanyListItem):
    aliases: list[str]
    profile: CompanyProfileOut | None = None
    contacts: list[CardOut]

    @field_validator("aliases", mode="before")
    @classmethod
    def none_as_empty(cls, v: object) -> object:
        return [] if v is None else v


def as_utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
