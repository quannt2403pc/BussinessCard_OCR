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
from app.services.company_matching import domains_overlap, extract_domains
from app.services.normalize_company import normalize_company_name

MAX_PAGE_SIZE = 100
PROFILE_COLUMNS = frozenset({*SOURCED_FIELDS, "description", "sources"})
FINISHED_PROFILE_STATUSES = (ProfileStatus.GENERATED.value, ProfileStatus.VERIFIED.value)
ARCHIVED = ProfileStatus.ARCHIVED.value


class ProfileArchivedError(RuntimeError):
    pass


@dataclass(frozen=True)
class CompanyRow:
    company: Company
    contact_count: int
    profile_status: str | None
    profile_generated_at: datetime | None


async def get_company(db: AsyncSession, company_id: uuid.UUID) -> Company | None:
    return await db.get(Company, company_id)


async def get_owned_company(
    db: AsyncSession, company_id: uuid.UUID, *, user_id: uuid.UUID
) -> Company | None:
    return await db.scalar(
        select(Company).where(Company.id == company_id, Company.user_id == user_id)
    )


async def get_profile(db: AsyncSession, company_id: uuid.UUID) -> CompanyProfile | None:
    return await db.scalar(select(CompanyProfile).where(CompanyProfile.company_id == company_id))


async def get_company_row(
    db: AsyncSession, company_id: uuid.UUID, *, user_id: uuid.UUID
) -> CompanyRow | None:
    result = await db.execute(
        _company_rows().where(Company.id == company_id, Company.user_id == user_id)
    )
    row = result.tuples().one_or_none()
    return _to_company_row(*row) if row else None


async def list_companies(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    q: str | None = None,
    has_profile: bool | None = None,
    archived: bool = False,
    page: int = 1,
    size: int = 20,
) -> tuple[list[CompanyRow], int]:
    conditions = [
        Company.user_id == user_id,
        *_list_conditions(q=q, has_profile=has_profile, archived=archived),
    ]
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

    statement = insert(CompanyProfile).values(
        id=uuid.uuid4(), company_id=company_id, user_id=_owner_of(company_id), **values
    )
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
        .values(
            id=uuid.uuid4(),
            company_id=company_id,
            user_id=_owner_of(company_id),
            status=ProfileStatus.DRAFT.value,
        )
        .on_conflict_do_nothing(constraint="uq_company_profiles_company_id")
    )


def _owner_of(company_id: uuid.UUID) -> ScalarSelect[uuid.UUID]:
    """`SELECT user_id FROM companies WHERE id = :company_id` — chủ sở hữu của hồ sơ sắp ghi.

    ⚠️ **Q thêm hàm này ở task 12.5 trong file của T** (luật nới D12, quy ước 2 — T review PR).
    Chỉ để hai câu `INSERT` ở trên **ghi được**: `company_profiles.user_id` là `NOT NULL` từ
    revision `0005`, nên thiếu nó thì luồng enrich của F2 chết ngay ở bước tạo hồ sơ nháp.
    **Phần lọc theo `user_id` khi ĐỌC vẫn là task 12.6 của T** — ở đây không chạm câu `SELECT` nào.

    Lấy bằng truy vấn con thay vì thêm tham số `user_id`: chữ ký hai hàm không đổi nên không chỗ
    gọi nào của T phải sửa, và **không có đường nào ghi một hồ sơ thuộc người khác với công ty
    của nó** — điều mà một tham số truyền tay thì luôn có thể làm sai.
    """
    return select(Company.user_id).where(Company.id == company_id).scalar_subquery()


async def discard_draft_profile(db: AsyncSession, company_id: uuid.UUID) -> int:
    result = await db.execute(
        delete(CompanyProfile).where(
            CompanyProfile.company_id == company_id,
            CompanyProfile.status == ProfileStatus.DRAFT.value,
        )
    )
    return int(getattr(result, "rowcount", 0) or 0)


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
    if profile.status == ARCHIVED:
        raise ProfileArchivedError(str(company_id))

    for field, value in changes.items():
        if field not in PROFILE_COLUMNS:
            raise ValueError(f"Invalid profile field: {field}")
        setattr(profile, field, value)

    profile.status = status.value
    profile.sources = {k: v for k, v in (profile.sources or {}).items() if k not in changes}

    await db.flush()

    return profile


def _list_conditions(
    *, q: str | None, has_profile: bool | None, archived: bool = False
) -> list[ColumnElement[bool]]:
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

    if archived:
        conditions.append(CompanyProfile.status == ARCHIVED)
        return conditions
    conditions.append(or_(CompanyProfile.id.is_(None), CompanyProfile.status != ARCHIVED))

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


def restored_status(profile: CompanyProfile) -> ProfileStatus:
    values = CompanyProfileSchema.model_validate(profile, from_attributes=True)
    return ProfileStatus.VERIFIED if values.fields_missing_source() else ProfileStatus.GENERATED


async def same_tax_code(
    db: AsyncSession, company_id: uuid.UUID, tax_code: str | None, *, user_id: uuid.UUID
) -> Sequence[Company]:
    code = (tax_code or "").strip()
    if not code:
        return []
    rows = await db.scalars(
        select(Company)
        .join(CompanyProfile, CompanyProfile.company_id == Company.id)
        .where(
            Company.user_id == user_id,
            func.trim(CompanyProfile.tax_code) == code,
            Company.id != company_id,
        )
        .order_by(Company.display_name, Company.id)
    )
    return rows.all()


async def same_domain(
    db: AsyncSession, company_id: uuid.UUID, *, user_id: uuid.UUID
) -> list[tuple[Company, list[str]]]:
    rows = await db.execute(
        select(BusinessCard.company_id, BusinessCard.email, BusinessCard.website).where(
            BusinessCard.user_id == user_id, BusinessCard.company_id.is_not(None)
        )
    )
    domains: dict[uuid.UUID, set[str]] = {}
    for owner, email, website in rows.tuples():
        if owner is not None:
            domains.setdefault(owner, set()).update(extract_domains(email, website))
    own = frozenset(domains.pop(company_id, set()))
    if not own:
        return []
    shared = {
        other: sorted(d for d in found if domains_overlap(own, frozenset({d})))
        for other, found in domains.items()
        if domains_overlap(own, frozenset(found))
    }
    if not shared:
        return []
    companies = await db.scalars(
        select(Company)
        .where(Company.user_id == user_id, Company.id.in_(shared))
        .order_by(Company.display_name, Company.id)
    )
    return [(company, shared[company.id]) for company in companies]


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
