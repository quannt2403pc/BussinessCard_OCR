import json
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.card import CardStatus
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
    job_title: str | None = None
    company_name_raw: str | None = None
    company_name: str | None = None
    company_id: uuid.UUID | None = None
    email: str | None = None
    phone: str | None = None
    phone_alt: str | None = None
    address: str | None = None
    website: str | None = None
    language_detected: str | None = None
    status: CardStatus
    notes: str | None = None
    uploaded_at: datetime
    updated_at: datetime

    @field_validator("uploaded_at", "updated_at")
    @classmethod
    def assume_utc(cls, v: datetime | None) -> datetime | None:
        return as_utc(v)


class CompanyExportRow(ExportRow):
    company_id: uuid.UUID
    display_name: str
    aliases: list[str] = Field(default_factory=list)
    card_count: int
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
