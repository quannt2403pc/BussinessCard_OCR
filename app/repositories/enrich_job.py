import uuid
from collections.abc import Sequence
from datetime import timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import ACTIVE_JOB_ITEM_STATUSES, Company, EnrichJob, EnrichJobItem
from app.schemas.enrich_job import JobItemStatus

ACTIVE_PREDICATE = text("status IN ('pending', 'running')")


async def existing_company_ids(
    db: AsyncSession, company_ids: Sequence[uuid.UUID]
) -> set[uuid.UUID]:
    rows = await db.scalars(select(Company.id).where(Company.id.in_(company_ids)))
    return set(rows)


async def expire_stale_items(db: AsyncSession, *, older_than: timedelta, message: str) -> int:
    result = await db.execute(
        update(EnrichJobItem)
        .where(
            EnrichJobItem.status.in_(ACTIVE_JOB_ITEM_STATUSES),
            func.coalesce(EnrichJobItem.started_at, EnrichJobItem.created_at)
            < func.now() - older_than,
        )
        .values(status=JobItemStatus.ERROR.value, error=message, finished_at=func.now())
        .execution_options(synchronize_session=False)
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def create_job(db: AsyncSession) -> uuid.UUID:
    job = EnrichJob(id=uuid.uuid4())
    db.add(job)
    await db.flush()
    return job.id


async def add_item(db: AsyncSession, job_id: uuid.UUID, company_id: uuid.UUID) -> uuid.UUID | None:
    return await db.scalar(
        insert(EnrichJobItem)
        .values(
            id=uuid.uuid4(),
            job_id=job_id,
            company_id=company_id,
            status=JobItemStatus.PENDING.value,
        )
        .on_conflict_do_nothing(
            index_elements=[EnrichJobItem.company_id], index_where=ACTIVE_PREDICATE
        )
        .returning(EnrichJobItem.id)
    )


async def active_job_id(db: AsyncSession, company_id: uuid.UUID) -> uuid.UUID | None:
    return await db.scalar(
        select(EnrichJobItem.job_id).where(
            EnrichJobItem.company_id == company_id,
            EnrichJobItem.status.in_(ACTIVE_JOB_ITEM_STATUSES),
        )
    )


async def active_items(
    db: AsyncSession,
    *,
    job_id: uuid.UUID | None = None,
    company_id: uuid.UUID | None = None,
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    stmt = select(EnrichJobItem.id, EnrichJobItem.company_id).where(
        EnrichJobItem.status.in_(ACTIVE_JOB_ITEM_STATUSES)
    )
    if job_id is not None:
        stmt = stmt.where(EnrichJobItem.job_id == job_id)
    if company_id is not None:
        stmt = stmt.where(EnrichJobItem.company_id == company_id)
    rows = await db.execute(stmt.order_by(EnrichJobItem.id))
    return [(item_id, owner) for item_id, owner in rows.tuples()]


async def cancel_items(db: AsyncSession, item_ids: Sequence[uuid.UUID], message: str) -> int:
    if not item_ids:
        return 0
    result = await db.execute(
        update(EnrichJobItem)
        .where(
            EnrichJobItem.id.in_(item_ids),
            EnrichJobItem.status.in_(ACTIVE_JOB_ITEM_STATUSES),
        )
        .values(status=JobItemStatus.CANCELLED.value, error=message, finished_at=func.now())
        .execution_options(synchronize_session=False)
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def get_job(db: AsyncSession, job_id: uuid.UUID) -> EnrichJob | None:
    return await db.get(EnrichJob, job_id)


async def list_items(db: AsyncSession, job_id: uuid.UUID) -> list[tuple[EnrichJobItem, str]]:
    rows = await db.execute(
        select(EnrichJobItem, Company.display_name)
        .join(Company, Company.id == EnrichJobItem.company_id)
        .where(EnrichJobItem.job_id == job_id)
        .order_by(Company.display_name, EnrichJobItem.id)
    )
    return list(rows.tuples().all())


async def pending_item_ids(db: AsyncSession, job_id: uuid.UUID) -> list[uuid.UUID]:
    rows = await db.scalars(
        select(EnrichJobItem.id)
        .where(
            EnrichJobItem.job_id == job_id,
            EnrichJobItem.status == JobItemStatus.PENDING.value,
        )
        .order_by(EnrichJobItem.id)
    )
    return list(rows)


async def claim_item(db: AsyncSession, item_id: uuid.UUID) -> uuid.UUID | None:
    return await db.scalar(
        update(EnrichJobItem)
        .where(
            EnrichJobItem.id == item_id,
            EnrichJobItem.status == JobItemStatus.PENDING.value,
        )
        .values(status=JobItemStatus.RUNNING.value, started_at=func.now())
        .returning(EnrichJobItem.company_id)
        .execution_options(synchronize_session=False)
    )


async def record_attempt(db: AsyncSession, item_id: uuid.UUID, attempt: int) -> None:
    await db.execute(
        update(EnrichJobItem)
        .where(EnrichJobItem.id == item_id)
        .values(attempts=attempt)
        .execution_options(synchronize_session=False)
    )


async def finish_item(
    db: AsyncSession,
    item_id: uuid.UUID,
    *,
    status: JobItemStatus,
    error: str | None = None,
    sourced_fields: int | None = None,
) -> None:
    await db.execute(
        update(EnrichJobItem)
        .where(EnrichJobItem.id == item_id)
        .values(
            status=status.value,
            error=error,
            sourced_fields=sourced_fields,
            finished_at=func.now(),
        )
        .execution_options(synchronize_session=False)
    )


async def abort_pending(db: AsyncSession, job_id: uuid.UUID, message: str) -> int:
    result = await db.execute(
        update(EnrichJobItem)
        .where(
            EnrichJobItem.job_id == job_id,
            EnrichJobItem.status == JobItemStatus.PENDING.value,
        )
        .values(status=JobItemStatus.ERROR.value, error=message, finished_at=func.now())
        .execution_options(synchronize_session=False)
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def finish_job(db: AsyncSession, job_id: uuid.UUID) -> None:
    await db.execute(
        update(EnrichJob)
        .where(EnrichJob.id == job_id, EnrichJob.finished_at.is_(None))
        .values(finished_at=func.now())
        .execution_options(synchronize_session=False)
    )
