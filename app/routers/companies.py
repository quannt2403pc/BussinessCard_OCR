import logging
import math
import uuid
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.templates import templates
from app.models.company import EnrichJob, EnrichJobItem
from app.repositories import company as company_repo
from app.repositories import enrich_job as job_repo
from app.schemas.card import CardOut
from app.schemas.company import (
    CompanyDetailOut,
    CompanyListItem,
    CompanyListOut,
    CompanyProfileOut,
    CompanyProfileUpdateIn,
    ProfileStatus,
)
from app.schemas.enrich_job import (
    MAX_BATCH_COMPANIES,
    EnrichBatchIn,
    EnrichBatchOut,
    EnrichConflictOut,
    EnrichJobItemOut,
    EnrichJobOut,
    EnrichStartOut,
    JobItemStatus,
)
from app.services import enrich_jobs

router = APIRouter()

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 20


@router.get("/companies", response_class=HTMLResponse, tags=["ui"])
async def companies_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "companies/list.html",
        {
            "active_nav": "companies",
            "default_page_size": DEFAULT_PAGE_SIZE,
            "max_batch_companies": MAX_BATCH_COMPANIES,
            "max_concurrency": enrich_jobs.MAX_CONCURRENCY,
        },
    )


@router.get("/companies/{company_id}", response_class=HTMLResponse, tags=["ui"])
async def company_detail_page(request: Request, company_id: uuid.UUID) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "companies/detail.html",
        {"active_nav": "companies", "company_id": str(company_id)},
    )


@router.get("/api/companies", response_model=CompanyListOut, tags=["companies"])
async def list_companies(
    db: Annotated[AsyncSession, Depends(get_db)],
    q: Annotated[
        str | None, Query(description="Tìm theo tên hiển thị, tên khác hoặc tên đã chuẩn hoá")
    ] = None,
    has_profile: Annotated[
        bool | None,
        Query(description="true: đã có hồ sơ generated/verified · false: chưa có hoặc đang draft"),
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=company_repo.MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> CompanyListOut:
    rows, total = await company_repo.list_companies(
        db, q=q, has_profile=has_profile, page=page, size=size
    )
    return CompanyListOut(
        items=[to_list_item(row) for row in rows],
        total=total,
        page=page,
        size=size,
        pages=page_count(total, size),
    )


@router.post(
    "/api/companies/enrich-batch",
    response_model=EnrichBatchOut,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["companies"],
)
async def enrich_batch(
    body: EnrichBatchIn,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> EnrichBatchOut:
    missing = set(body.company_ids) - await job_repo.existing_company_ids(db, body.company_ids)
    if missing:
        names = ", ".join(sorted(str(company_id) for company_id in missing))
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Không có công ty: {names}")
    created = await enrich_jobs.create_job(db, body.company_ids)
    await db.commit()
    if created.accepted:
        enrich_jobs.start(created.job_id)
    return EnrichBatchOut(
        job_id=created.job_id, accepted=created.accepted, skipped=len(created.skipped)
    )


@router.get("/api/companies/enrich-jobs/{job_id}", response_model=EnrichJobOut, tags=["companies"])
async def get_enrich_job(
    job_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> EnrichJobOut:
    job = await job_repo.get_job(db, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")
    return to_job_out(job, await job_repo.list_items(db, job_id))


@router.get("/api/companies/{company_id}", response_model=CompanyDetailOut, tags=["companies"])
async def get_company(
    company_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CompanyDetailOut:
    row = await company_repo.get_company_row(db, company_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")
    profile = await company_repo.get_profile(db, company_id)
    contacts = await company_repo.list_contacts(db, company_id)
    return CompanyDetailOut(
        **to_list_item(row).model_dump(),
        aliases=row.company.aliases or [],
        profile=(
            CompanyProfileOut.model_validate(profile, from_attributes=True) if profile else None
        ),
        contacts=[CardOut.model_validate(card) for card in contacts],
    )


@router.get(
    "/api/companies/{company_id}/contacts", response_model=list[CardOut], tags=["companies"]
)
async def list_company_contacts(
    company_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[CardOut]:
    if await company_repo.get_company(db, company_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")
    contacts = await company_repo.list_contacts(db, company_id)
    return [CardOut.model_validate(card) for card in contacts]


@router.post(
    "/api/companies/{company_id}/enrich",
    response_model=EnrichStartOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses={status.HTTP_409_CONFLICT: {"model": EnrichConflictOut}},
    tags=["companies"],
)
async def enrich_company(
    company_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> EnrichStartOut | JSONResponse:
    if await company_repo.get_company(db, company_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")
    created = await enrich_jobs.create_job(db, [company_id])
    if not created.accepted:
        existing = await job_repo.active_job_id(db, company_id)
        await db.rollback()
        conflict = EnrichConflictOut(
            detail="Công ty này đang được tạo hồ sơ, đợi lượt đang chạy xong.",
            existing_id=existing,
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT, content=conflict.model_dump(mode="json")
        )
    await db.commit()
    enrich_jobs.start(created.job_id)
    return EnrichStartOut(job_id=created.job_id)


@router.patch(
    "/api/companies/{company_id}/profile",
    response_model=CompanyProfileOut,
    responses={status.HTTP_409_CONFLICT: {"model": EnrichConflictOut}},
    tags=["companies"],
)
async def update_company_profile(
    db: Annotated[AsyncSession, Depends(get_db)],
    company_id: uuid.UUID,
    body: CompanyProfileUpdateIn,
) -> CompanyProfileOut | JSONResponse:
    changes = body.changes()

    if not changes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Không có trường nào để sửa.")

    running = await job_repo.active_job_id(db, company_id)

    if running is not None:
        conflict = EnrichConflictOut(
            detail="Công ty này đang được tạo hồ sơ, sửa bây giờ sẽ bị ghi đè khi job xong.",
            existing_id=running,
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT, content=conflict.model_dump(mode="json")
        )

    profile = await company_repo.update_profile(db, company_id, changes)

    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")

    await db.commit()

    logger.info("Company %s: sửa tay %s", company_id, ", ".join(sorted(changes)))
    return CompanyProfileOut.model_validate(profile, from_attributes=True)

    return CompanyProfileOut.model_validate(profile, from_attributes=True)


def page_count(total: int, size: int) -> int:
    return max(1, math.ceil(total / size))


def to_list_item(row: company_repo.CompanyRow) -> CompanyListItem:
    return CompanyListItem(
        id=row.company.id,
        display_name=row.company.display_name,
        name_normalized=row.company.name_normalized,
        contact_count=row.contact_count,
        profile_status=ProfileStatus(row.profile_status) if row.profile_status else None,
        profile_generated_at=row.profile_generated_at,
    )


def to_job_out(job: EnrichJob, items: Sequence[tuple[EnrichJobItem, str]]) -> EnrichJobOut:
    statuses = [JobItemStatus(item.status) for item, _ in items]
    return EnrichJobOut(
        job_id=job.id,
        total=len(items),
        done=statuses.count(JobItemStatus.DONE),
        failed=statuses.count(JobItemStatus.ERROR),
        running=statuses.count(JobItemStatus.RUNNING),
        finished=job.finished_at is not None,
        created_at=job.created_at,
        finished_at=job.finished_at,
        items=[
            EnrichJobItemOut(
                company_id=item.company_id,
                display_name=display_name,
                status=JobItemStatus(item.status),
                error=item.error,
                attempts=item.attempts,
                sourced_fields=item.sourced_fields,
                started_at=item.started_at,
                finished_at=item.finished_at,
            )
            for item, display_name in items
        ],
    )
