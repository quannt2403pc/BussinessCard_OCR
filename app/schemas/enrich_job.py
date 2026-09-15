import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from app.schemas.company import as_utc

MAX_BATCH_COMPANIES = 50


class JobItemStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


class EnrichBatchIn(BaseModel):
    company_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_BATCH_COMPANIES)


class EnrichStartOut(BaseModel):
    job_id: uuid.UUID


class EnrichBatchOut(BaseModel):
    job_id: uuid.UUID
    accepted: int
    skipped: int


class EnrichConflictOut(BaseModel):
    detail: str
    existing_id: uuid.UUID | None


class EnrichJobItemOut(BaseModel):
    company_id: uuid.UUID
    display_name: str
    status: JobItemStatus
    error: str | None = None
    attempts: int
    sourced_fields: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @field_validator("started_at", "finished_at")
    @classmethod
    def assume_utc(cls, v: datetime | None) -> datetime | None:
        return as_utc(v)


class EnrichJobOut(BaseModel):
    job_id: uuid.UUID
    total: int
    done: int
    failed: int
    running: int
    finished: bool
    created_at: datetime
    finished_at: datetime | None = None
    items: list[EnrichJobItemOut]

    @field_validator("created_at", "finished_at")
    @classmethod
    def assume_utc(cls, v: datetime | None) -> datetime | None:
        return as_utc(v)
