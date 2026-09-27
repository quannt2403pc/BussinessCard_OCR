import logging
import math
import uuid
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import CurrentUser
from app.core.templates import templates
from app.core.workspace import CurrentWorkspace
from app.models.company import Company, EnrichJob, EnrichJobItem
from app.models.kb import KBSourceType
from app.repositories import company as company_repo
from app.repositories import enrich_job as job_repo
from app.repositories import kb as kb_repo
from app.schemas.card import CardOut
from app.schemas.company import (
    CompanyDetailOut,
    CompanyListItem,
    CompanyListOut,
    CompanyProfileOut,
    CompanyProfileUpdateIn,
    CompanyRef,
    ProfileStatus,
    RelatedCompany,
)
from app.schemas.enrich_job import (
    MAX_BATCH_COMPANIES,
    EnrichBatchIn,
    EnrichBatchOut,
    EnrichCancelOut,
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
NOT_FOUND = "not found"

Session = Annotated[AsyncSession, Depends(get_db)]


async def owned_company(
    db: AsyncSession, company_id: uuid.UUID, workspace: CurrentWorkspace
) -> Company:
    company = await company_repo.get_owned_company(db, company_id, workspace_id=workspace.id)
    if company is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return company


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
    db: Session,
    user: CurrentUser,
    workspace: CurrentWorkspace,
    q: Annotated[
        str | None, Query(description="Tìm theo tên hiển thị, tên khác hoặc tên đã chuẩn hoá")
    ] = None,
    has_profile: Annotated[
        bool | None,
        Query(description="true: đã có hồ sơ generated/verified · false: chưa có hoặc đang draft"),
    ] = None,
    archived: Annotated[
        bool, Query(description="true: chỉ công ty có hồ sơ đã ẩn · mặc định: bỏ qua hồ sơ đã ẩn")
    ] = False,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=company_repo.MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> CompanyListOut:
    rows, total = await company_repo.list_companies(
        db,
        workspace_id=workspace.id,
        q=q,
        has_profile=has_profile,
        archived=archived,
        page=page,
        size=size,
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
    body: EnrichBatchIn, db: Session, user: CurrentUser, workspace: CurrentWorkspace
) -> EnrichBatchOut:
    owned = await job_repo.existing_company_ids(db, body.company_ids, workspace_id=workspace.id)
    missing = set(body.company_ids) - owned
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
    job_id: uuid.UUID, db: Session, user: CurrentUser, workspace: CurrentWorkspace
) -> EnrichJobOut:
    job = await job_repo.get_job(db, job_id, workspace_id=workspace.id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return to_job_out(job, await job_repo.list_items(db, job_id))


@router.post(
    "/api/companies/enrich-jobs/{job_id}/cancel", response_model=EnrichJobOut, tags=["companies"]
)
async def cancel_enrich_job(
    job_id: uuid.UUID, db: Session, user: CurrentUser, workspace: CurrentWorkspace
) -> EnrichJobOut:
    job = await job_repo.get_job(db, job_id, workspace_id=workspace.id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    cancelled = await enrich_jobs.cancel(db, job_id=job_id)
    await db.commit()
    enrich_jobs.interrupt(cancelled)
    logger.info("Enrich job %s: huỷ %d công ty", job_id, len(cancelled))
    return to_job_out(job, await job_repo.list_items(db, job_id))


@router.get("/api/companies/{company_id}", response_model=CompanyDetailOut, tags=["companies"])
async def get_company(
    company_id: uuid.UUID, db: Session, user: CurrentUser, workspace: CurrentWorkspace
) -> CompanyDetailOut:
    row = await company_repo.get_company_row(db, company_id, workspace_id=workspace.id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    profile = await company_repo.get_profile(db, company_id)
    contacts = await company_repo.list_contacts(db, company_id)
    twins = await company_repo.same_tax_code(
        db, company_id, profile.tax_code if profile else None, workspace_id=workspace.id
    )
    neighbours = await company_repo.same_domain(db, company_id, workspace_id=workspace.id)
    return CompanyDetailOut(
        **to_list_item(row).model_dump(),
        aliases=row.company.aliases or [],
        profile=(
            CompanyProfileOut.model_validate(profile, from_attributes=True) if profile else None
        ),
        contacts=[CardOut.model_validate(card) for card in contacts],
        same_tax_code=[CompanyRef(id=c.id, display_name=c.display_name) for c in twins],
        same_domain=[
            RelatedCompany(id=c.id, display_name=c.display_name, domains=domains)
            for c, domains in neighbours
        ],
    )


@router.get(
    "/api/companies/{company_id}/contacts", response_model=list[CardOut], tags=["companies"]
)
async def list_company_contacts(
    company_id: uuid.UUID, db: Session, user: CurrentUser, workspace: CurrentWorkspace
) -> list[CardOut]:
    await owned_company(db, company_id, workspace)
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
    company_id: uuid.UUID, db: Session, user: CurrentUser, workspace: CurrentWorkspace
) -> EnrichStartOut | JSONResponse:
    await owned_company(db, company_id, workspace)
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


@router.post(
    "/api/companies/{company_id}/enrich/cancel",
    response_model=EnrichCancelOut,
    tags=["companies"],
)
async def cancel_company_enrich(
    company_id: uuid.UUID, db: Session, user: CurrentUser, workspace: CurrentWorkspace
) -> EnrichCancelOut:
    await owned_company(db, company_id, workspace)
    cancelled = await enrich_jobs.cancel(db, company_id=company_id)
    stale = 0 if cancelled else await company_repo.discard_draft_profile(db, company_id)
    if not cancelled and not stale:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Công ty này không có lượt tạo hồ sơ nào đang chạy."
        )
    await db.commit()
    enrich_jobs.interrupt(cancelled)
    logger.info("Company %s: huỷ lượt tạo hồ sơ", company_id)
    return EnrichCancelOut(cancelled=len(cancelled))


@router.post(
    "/api/companies/{company_id}/profile/archive",
    response_model=CompanyProfileOut,
    tags=["companies"],
)
async def archive_company_profile(
    company_id: uuid.UUID, db: Session, user: CurrentUser, workspace: CurrentWorkspace
) -> CompanyProfileOut:
    await owned_company(db, company_id, workspace)
    profile = await company_repo.get_profile(db, company_id)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Công ty này chưa có hồ sơ.")
    if profile.status == ProfileStatus.DRAFT or await job_repo.active_job_id(db, company_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Hồ sơ đang được tạo — huỷ hoặc đợi lượt đang chạy xong rồi mới ẩn.",
        )
    if profile.status != ProfileStatus.ARCHIVED:
        profile.status = ProfileStatus.ARCHIVED.value
        await kb_repo.delete_for_source(
            db,
            workspace_id=workspace.id,
            source_type=KBSourceType.COMPANY_PROFILE,
            source_id=company_id,
        )
        await db.commit()
        logger.info("Company %s: ẩn hồ sơ", company_id)
    return CompanyProfileOut.model_validate(profile, from_attributes=True)


@router.post(
    "/api/companies/{company_id}/profile/restore",
    response_model=CompanyProfileOut,
    tags=["companies"],
)
async def restore_company_profile(
    company_id: uuid.UUID, db: Session, user: CurrentUser, workspace: CurrentWorkspace
) -> CompanyProfileOut:
    await owned_company(db, company_id, workspace)
    profile = await company_repo.get_profile(db, company_id)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Công ty này chưa có hồ sơ.")
    if profile.status != ProfileStatus.ARCHIVED:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Hồ sơ này không ở trạng thái ẩn.")
    profile.status = company_repo.restored_status(profile).value
    await db.commit()
    await enrich_jobs.index_profile(profile.id)
    logger.info("Company %s: hiện lại hồ sơ (%s)", company_id, profile.status)
    return CompanyProfileOut.model_validate(profile, from_attributes=True)


@router.patch(
    "/api/companies/{company_id}/profile",
    response_model=CompanyProfileOut,
    responses={status.HTTP_409_CONFLICT: {"model": EnrichConflictOut}},
    tags=["companies"],
)
async def update_company_profile(
    db: Session,
    user: CurrentUser,
    workspace: CurrentWorkspace,
    company_id: uuid.UUID,
    body: CompanyProfileUpdateIn,
) -> CompanyProfileOut | JSONResponse:
    changes = body.changes()

    if not changes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Không có trường nào để sửa.")

    await owned_company(db, company_id, workspace)

    running = await job_repo.active_job_id(db, company_id)

    if running is not None:
        conflict = EnrichConflictOut(
            detail="Công ty này đang được tạo hồ sơ, sửa bây giờ sẽ bị ghi đè khi job xong.",
            existing_id=running,
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT, content=conflict.model_dump(mode="json")
        )

    try:
        profile = await company_repo.update_profile(db, company_id, changes)
    except company_repo.ProfileArchivedError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Hồ sơ đang ẩn — hiện lại trước khi sửa."
        ) from exc

    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)

    await db.commit()
    await enrich_jobs.index_profile(profile.id)
    logger.info("Company %s: sửa tay %s", company_id, ", ".join(sorted(changes)))
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
        cancelled=statuses.count(JobItemStatus.CANCELLED),
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
