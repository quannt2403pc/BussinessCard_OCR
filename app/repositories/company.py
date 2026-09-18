import uuid
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import ColumnElement, ScalarSelect, Select, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.card import BusinessCard
from app.models.company import Company, CompanyProfile
from app.schemas.company import SOURCED_FIELDS, CompanyProfileSchema, ProfileStatus
from app.services.normalize_company import normalize_company_name

MAX_PAGE_SIZE = 100
PROFILE_COLUMNS = frozenset({*SOURCED_FIELDS, "description", "sources"})
FINISHED_PROFILE_STATUSES = (ProfileStatus.GENERATED.value, ProfileStatus.VERIFIED.value)


@dataclass(frozen=True)
class CompanyRow:
    company: Company
    contact_count: int
    profile_status: str | None
    profile_generated_at: datetime | None


async def get_company(db: AsyncSession, company_id: uuid.UUID) -> Company | None:
    return await db.get(Company, company_id)


async def get_profile(db: AsyncSession, company_id: uuid.UUID) -> CompanyProfile | None:
    return await db.scalar(select(CompanyProfile).where(CompanyProfile.company_id == company_id))


async def get_company_row(db: AsyncSession, company_id: uuid.UUID) -> CompanyRow | None:
    result = await db.execute(_company_rows().where(Company.id == company_id))
    row = result.tuples().one_or_none()
    return _to_company_row(*row) if row else None


async def list_companies(
    db: AsyncSession,
    *,
    q: str | None = None,
    has_profile: bool | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[list[CompanyRow], int]:
    conditions = _list_conditions(q=q, has_profile=has_profile)
    size = max(1, min(size, MAX_PAGE_SIZE))
    page = max(1, page)

    total = await db.scalar(
        select(func.count())
        .select_from(Company)
        .outerjoin(CompanyProfile, CompanyProfile.company_id == Company.id)
        .where(*conditions)
    )

    rows = await db.execute(
        _company_rows()
        .where(*conditions)
        .order_by(Company.display_name, Company.id)
        .offset((page - 1) * size)
        .limit(size)
    )
    return [_to_company_row(*row) for row in rows.tuples()], int(total or 0)


async def list_contacts(db: AsyncSession, company_id: uuid.UUID) -> Sequence[BusinessCard]:
    rows = await db.execute(
        select(BusinessCard)
        .where(BusinessCard.company_id == company_id)
        .order_by(BusinessCard.uploaded_at.desc(), BusinessCard.id.desc())
    )
    return rows.scalars().all()


async def save_profile(
    db: AsyncSession,
    company_id: uuid.UUID,
    profile: CompanyProfileSchema,
    *,
    status: ProfileStatus = ProfileStatus.GENERATED,
    llm_model: str | None = None,
    generated_at: datetime | None = None,
) -> CompanyProfile:
    values = profile.model_dump(mode="json", include=set(PROFILE_COLUMNS))
    values.update(
        status=status.value,
        llm_model=llm_model,
        generated_at=_naive_utc(generated_at),
    )

    statement = insert(CompanyProfile).values(id=uuid.uuid4(), company_id=company_id, **values)
    statement = statement.on_conflict_do_update(
        constraint="uq_company_profiles_company_id",
        set_={**{name: statement.excluded[name] for name in values}, "updated_at": func.now()},
    )
    profile_id = await db.scalar(statement.returning(CompanyProfile.id))

    result = await db.execute(
        select(CompanyProfile)
        .where(CompanyProfile.id == profile_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


async def ensure_draft_profile(db: AsyncSession, company_id: uuid.UUID) -> None:
    await db.execute(
        insert(CompanyProfile)
        .values(id=uuid.uuid4(), company_id=company_id, status=ProfileStatus.DRAFT.value)
        .on_conflict_do_nothing(constraint="uq_company_profiles_company_id")
    )


async def discard_draft_profile(db: AsyncSession, company_id: uuid.UUID) -> None:
    await db.execute(
        delete(CompanyProfile).where(
            CompanyProfile.company_id == company_id,
            CompanyProfile.status == ProfileStatus.DRAFT.value,
        )
    )


async def update_profile(
    db: AsyncSession,
    company_id: uuid.UUID,
    changes: Mapping[str, Any],
    *,
    status: ProfileStatus = ProfileStatus.VERIFIED,
) -> CompanyProfile | None:
    profile = await get_profile(db, company_id)
    if profile is None:
        return None

    for field, value in changes.items():
        if field not in PROFILE_COLUMNS:
            raise ValueError(f"Invalid profile field: {field}")
        setattr(profile, field, value)

    profile.status = status.value
    profile.sources = {k: v for k, v in (profile.sources or {}).items() if k not in changes}

    await db.flush()

    return profile


def _list_conditions(*, q: str | None, has_profile: bool | None) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []

    if q and (term := " ".join(q.split())):
        pattern = _contains(term)
        matches = [
            Company.display_name.ilike(pattern, escape="\\"),
            func.array_to_string(Company.aliases, " ").ilike(pattern, escape="\\"),
        ]
        with suppress(ValueError):
            key = normalize_company_name(term)
            matches.append(Company.name_normalized.ilike(_contains(key), escape="\\"))
        conditions.append(or_(*matches))

    if has_profile is True:
        conditions.append(CompanyProfile.status.in_(FINISHED_PROFILE_STATUSES))
    elif has_profile is False:
        conditions.append(
            or_(
                CompanyProfile.id.is_(None),
                CompanyProfile.status.not_in(FINISHED_PROFILE_STATUSES),
            )
        )

    return conditions


def contact_count() -> ScalarSelect[int]:
    return (
        select(func.count(BusinessCard.id))
        .where(BusinessCard.company_id == Company.id)
        .correlate(Company)
        .scalar_subquery()
    )


def _company_rows() -> Select[tuple[Company, int, str | None, datetime | None]]:
    profile_status = cast(ColumnElement[str | None], CompanyProfile.status)
    return select(Company, contact_count(), profile_status, CompanyProfile.generated_at).outerjoin(
        CompanyProfile, CompanyProfile.company_id == Company.id
    )


def _to_company_row(
    company: Company, count: int | None, status: str | None, generated_at: datetime | None
) -> CompanyRow:
    return CompanyRow(company, int(count or 0), status, generated_at)


def _contains(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)
