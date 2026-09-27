import csv
import io
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import Select, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.core.db import SessionLocal
from app.core.security import CurrentUser
from app.core.workspace import CurrentWorkspace
from app.models.card import BusinessCard, CardStatus, RelationshipStatus
from app.models.company import Company, CompanyProfile
from app.models.privacy import PrivacyAction
from app.repositories import company as company_repo
from app.repositories import privacy as privacy_repo
from app.schemas.export import (
    CSV_BOM,
    EXPORT_BATCH_SIZE,
    VCARD_MEDIA_TYPE,
    CardExportRow,
    CardsExportOut,
    CompaniesExportOut,
    CompanyExportRow,
    ExportMeta,
    ExportRow,
)

router = APIRouter(prefix="/api/export", tags=["export"])

STATUS_CHOICES: tuple[str, ...] = tuple(choice.value for choice in CardStatus)
RELATIONSHIP_CHOICES: tuple[str, ...] = tuple(choice.value for choice in RelationshipStatus)

CSV_MEDIA_TYPE = "text/csv; charset=utf-8"

CardStatusQuery = Annotated[
    str | None, Query(alias="status", description="pending | needs_review | confirmed")
]
#: Lọc theo **vòng đời quan hệ**, độc lập với `status` ở trên (task NEXT-01). Ca dùng thật là
#: "xuất riêng những người đang trao đổi để mang sang công cụ gửi thư".
RelationshipQuery = Annotated[
    str | None,
    Query(alias="relationship", description="new | contacted | talking | won | lost | closed"),
]


def _cards_select(
    workspace_id: uuid.UUID,
    card_status: str | None,
    relationship: str | None = None,
) -> Select[Any]:
    stmt = (
        select(
            BusinessCard.id,
            BusinessCard.full_name,
            BusinessCard.full_name_vi,
            BusinessCard.job_title,
            BusinessCard.job_title_vi,
            BusinessCard.company_name_raw,
            BusinessCard.company_name_vi,
            Company.display_name.label("company_name"),
            BusinessCard.company_id,
            BusinessCard.email,
            BusinessCard.phone,
            BusinessCard.phone_alt,
            BusinessCard.address,
            BusinessCard.address_vi,
            BusinessCard.website,
            BusinessCard.language_detected,
            BusinessCard.status,
            BusinessCard.relationship_status,
            BusinessCard.follow_up_at,
            BusinessCard.notes,
            BusinessCard.uploaded_at,
            BusinessCard.updated_at,
        )
        .outerjoin(Company, Company.id == BusinessCard.company_id)
        # Bản trùng đã gộp không nằm trong bản xuất (task NEXT-04) — xuất ra thì công cụ nhận
        # file lại dựng lại đúng cặp trùng mà người dùng vừa gộp xong.
        .where(BusinessCard.workspace_id == workspace_id, BusinessCard.merged_into_id.is_(None))
        .order_by(BusinessCard.uploaded_at, BusinessCard.id)
    )
    if card_status is not None:
        stmt = stmt.where(BusinessCard.status == card_status)
    if relationship is not None:
        stmt = stmt.where(BusinessCard.relationship_status == relationship)
    return stmt


def _companies_select(workspace_id: uuid.UUID) -> Select[Any]:
    return (
        select(
            Company.id.label("company_id"),
            Company.display_name,
            Company.aliases,
            company_repo.contact_count().label("contact_count"),
            CompanyProfile.status.label("profile_status"),
            CompanyProfile.legal_name,
            CompanyProfile.tax_code,
            CompanyProfile.founded_year,
            CompanyProfile.size_label,
            CompanyProfile.employee_range,
            CompanyProfile.industry,
            CompanyProfile.products,
            CompanyProfile.address,
            CompanyProfile.website,
            CompanyProfile.phone,
            CompanyProfile.email,
            CompanyProfile.description,
            CompanyProfile.sources,
            CompanyProfile.llm_model,
            CompanyProfile.generated_at,
        )
        .outerjoin(CompanyProfile, CompanyProfile.company_id == Company.id)
        .where(Company.workspace_id == workspace_id)
        .order_by(Company.display_name, Company.id)
    )


async def count_cards(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    card_status: str | None,
    relationship: str | None = None,
) -> int:
    stmt = select(func.count(BusinessCard.id)).where(
        BusinessCard.workspace_id == workspace_id, BusinessCard.merged_into_id.is_(None)
    )
    if card_status is not None:
        stmt = stmt.where(BusinessCard.status == card_status)
    if relationship is not None:
        stmt = stmt.where(BusinessCard.relationship_status == relationship)
    return int(await db.scalar(stmt) or 0)


async def count_companies(db: AsyncSession, workspace_id: uuid.UUID) -> int:
    stmt = select(func.count(Company.id)).where(Company.workspace_id == workspace_id)
    return int(await db.scalar(stmt) or 0)


async def iter_cards(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    card_status: str | None,
    relationship: str | None = None,
) -> AsyncIterator[CardExportRow]:
    cursor: tuple[datetime, uuid.UUID] | None = None
    while True:
        stmt = _cards_select(workspace_id, card_status, relationship).limit(EXPORT_BATCH_SIZE)
        if cursor is not None:
            stmt = stmt.where(tuple_(BusinessCard.uploaded_at, BusinessCard.id) > cursor)
        rows = (await db.execute(stmt)).all()
        if not rows:
            return
        for row in rows:
            yield CardExportRow.model_validate(row)
        cursor = (rows[-1].uploaded_at, rows[-1].id)


async def one_card(db: AsyncSession, workspace_id: uuid.UUID, card_id: uuid.UUID) -> CardExportRow:
    """Một danh thiếp **của đúng người này**, dựng qua cùng câu `SELECT` với bản xuất cả lô.

    Dùng lại `_cards_select()` chứ không `card_repo.get()`: chỉ câu này mới `JOIN` sẵn tên công ty,
    và đi chung một đường thì bản xuất một liên hệ không bao giờ lệch nội dung với bản xuất cả lô.
    """
    stmt = _cards_select(workspace_id, None).where(BusinessCard.id == card_id)
    row = (await db.execute(stmt)).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")
    return CardExportRow.model_validate(row)


async def iter_companies(
    db: AsyncSession, workspace_id: uuid.UUID
) -> AsyncIterator[CompanyExportRow]:
    cursor: tuple[str, uuid.UUID] | None = None
    while True:
        stmt = _companies_select(workspace_id).limit(EXPORT_BATCH_SIZE)
        if cursor is not None:
            stmt = stmt.where(tuple_(Company.display_name, Company.id) > cursor)
        rows = (await db.execute(stmt)).all()
        if not rows:
            return
        for row in rows:
            yield CompanyExportRow.model_validate(row)
        cursor = (rows[-1].display_name, rows[-1].company_id)


async def _vcard_body(rows: AsyncIterator[CardExportRow]) -> AsyncIterator[str]:
    async for row in rows:
        yield row.vcard()


def _drain(buffer: io.StringIO) -> str:
    text = buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)
    return text


async def _csv_body(rows: AsyncIterator[ExportRow], columns: tuple[str, ...]) -> AsyncIterator[str]:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    yield CSV_BOM + _drain(buffer)
    async for row in rows:
        writer.writerow(row.csv_row())
        yield _drain(buffer)


async def _json_body(rows: AsyncIterator[ExportRow], meta: ExportMeta) -> AsyncIterator[str]:
    yield meta.model_dump_json()[:-1] + ',"items":['
    separator = ""
    async for row in rows:
        yield separator + row.model_dump_json()
        separator = ","
    yield "]}"


def _meta(total: int, filters: dict[str, str]) -> ExportMeta:
    return ExportMeta(exported_at=datetime.now(UTC), total=total, filters=filters)


def _headers(name: str, suffix: str) -> dict[str, str]:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    return {"Content-Disposition": f'attachment; filename="{name}-{today}.{suffix}"'}


class _Counter:
    """Đếm số bản ghi **thật sự chảy ra ngoài**, để nhật ký của `NEXT-07` ghi đúng con số.

    Đếm ở đây chứ không dùng lại `count_cards()`: hai câu truy vấn riêng có thể lệch nhau, mà
    một nhật ký bảo vệ dữ liệu cá nhân nói sai số bản ghi thì còn tệ hơn không có nhật ký.
    """

    def __init__(self) -> None:
        self.rows = 0

    async def watch(self, rows: AsyncIterator[Any]) -> AsyncIterator[Any]:
        async for row in rows:
            self.rows += 1
            yield row


def _audit(
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    kind: str,
    fmt: str,
    filters: dict[str, str],
    counter: _Counter,
) -> BackgroundTask:
    """Ghi nhật ký **sau khi** file đã gửi xong (task NEXT-07).

    `BackgroundTask` chạy sau lượt truyền, nên con số ghi lại là số bản ghi thật sự ra khỏi hệ
    thống: tải nửa chừng rồi ngắt thì nhật ký ghi đúng phần đã đi, không ghi phần định gửi.
    """

    async def write() -> None:
        async with SessionLocal() as db:
            await privacy_repo.log(
                db,
                workspace_id=workspace_id,
                user_id=user_id,
                action=PrivacyAction.EXPORT,
                detail={"kind": kind, "format": fmt, "filters": filters},
                record_count=counter.rows,
            )
            await db.commit()

    return BackgroundTask(write)


def _validated_status(card_status: str | None) -> str | None:
    if card_status is not None and card_status not in STATUS_CHOICES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"status phải là một trong {', '.join(STATUS_CHOICES)}.",
        )
    return card_status


def _validated_relationship(relationship: str | None) -> str | None:
    if relationship is not None and relationship not in RELATIONSHIP_CHOICES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"relationship phải là một trong {', '.join(RELATIONSHIP_CHOICES)}.",
        )
    return relationship


def _filters(card_status: str | None, relationship: str | None) -> dict[str, str]:
    chosen = {"status": card_status, "relationship": relationship}
    return {key: value for key, value in chosen.items() if value is not None}


@router.get("/cards.csv")
async def export_cards_csv(
    user: CurrentUser,
    workspace: CurrentWorkspace,
    card_status: CardStatusQuery = None,
    relationship: RelationshipQuery = None,
) -> StreamingResponse:
    selected = _validated_status(card_status)
    stage = _validated_relationship(relationship)
    workspace_id = workspace.id
    user_id = user.id
    counter = _Counter()

    async def body() -> AsyncIterator[str]:
        async with SessionLocal() as db:
            rows = counter.watch(iter_cards(db, workspace_id, selected, stage))
            async for chunk in _csv_body(rows, CardExportRow.columns()):
                yield chunk

    return StreamingResponse(
        body(),
        media_type=CSV_MEDIA_TYPE,
        headers=_headers("cards", "csv"),
        background=_audit(
            workspace_id, user_id, "cards", "csv", _filters(selected, stage), counter
        ),
    )


@router.get("/cards.json", responses={200: {"model": CardsExportOut}})
async def export_cards_json(
    user: CurrentUser,
    workspace: CurrentWorkspace,
    card_status: CardStatusQuery = None,
    relationship: RelationshipQuery = None,
) -> StreamingResponse:
    selected = _validated_status(card_status)
    stage = _validated_relationship(relationship)
    workspace_id = workspace.id
    user_id = user.id
    counter = _Counter()

    async def body() -> AsyncIterator[str]:
        async with SessionLocal() as db:
            total = await count_cards(db, workspace_id, selected, stage)
            meta = _meta(total, _filters(selected, stage))
            rows = counter.watch(iter_cards(db, workspace_id, selected, stage))
            async for chunk in _json_body(rows, meta):
                yield chunk

    return StreamingResponse(
        body(),
        media_type="application/json",
        headers=_headers("cards", "json"),
        background=_audit(
            workspace_id, user_id, "cards", "json", _filters(selected, stage), counter
        ),
    )


@router.get("/cards.vcf")
async def export_cards_vcf(
    user: CurrentUser,
    workspace: CurrentWorkspace,
    card_status: CardStatusQuery = None,
    relationship: RelationshipQuery = None,
) -> StreamingResponse:
    """Cả lô danh thiếp dưới dạng vCard 3.0 — thả thẳng vào danh bạ điện thoại hoặc Outlook.

    Không BOM, khác `cards.csv`: BOM là mẹo cho Excel đọc UTF-8, còn trình đọc vCard thì coi nó
    là rác ngay ở dòng `BEGIN:VCARD` đầu tiên.
    """
    selected = _validated_status(card_status)
    stage = _validated_relationship(relationship)
    workspace_id = workspace.id
    user_id = user.id
    counter = _Counter()

    async def body() -> AsyncIterator[str]:
        async with SessionLocal() as db:
            rows = counter.watch(iter_cards(db, workspace_id, selected, stage))
            async for chunk in _vcard_body(rows):
                yield chunk

    return StreamingResponse(
        body(),
        media_type=VCARD_MEDIA_TYPE,
        headers=_headers("danh-thiep", "vcf"),
        background=_audit(
            workspace_id, user_id, "cards", "vcf", _filters(selected, stage), counter
        ),
    )


@router.get("/cards/{card_id}.vcf")
async def export_card_vcf(
    card_id: uuid.UUID, user: CurrentUser, workspace: CurrentWorkspace
) -> Response:
    """Một danh thiếp dưới dạng vCard. Thẻ của người khác trả **404**, không phải 403."""
    async with SessionLocal() as db:
        row = await one_card(db, workspace.id, card_id)
        await privacy_repo.log(
            db,
            workspace_id=workspace.id,
            user_id=user.id,
            action=PrivacyAction.EXPORT,
            detail={"kind": "card", "format": "vcf", "card_id": str(card_id)},
            record_count=1,
        )
        await db.commit()
    return Response(
        row.vcard(),
        media_type=VCARD_MEDIA_TYPE,
        headers=_headers(f"danh-thiep-{card_id.hex[:8]}", "vcf"),
    )


@router.get("/companies.csv")
async def export_companies_csv(user: CurrentUser, workspace: CurrentWorkspace) -> StreamingResponse:
    workspace_id = workspace.id
    user_id = user.id
    counter = _Counter()

    async def body() -> AsyncIterator[str]:
        async with SessionLocal() as db:
            rows = counter.watch(iter_companies(db, workspace_id))
            async for chunk in _csv_body(rows, CompanyExportRow.columns()):
                yield chunk

    return StreamingResponse(
        body(),
        media_type=CSV_MEDIA_TYPE,
        headers=_headers("companies", "csv"),
        background=_audit(workspace_id, user_id, "companies", "csv", {}, counter),
    )


@router.get("/companies.json", responses={200: {"model": CompaniesExportOut}})
async def export_companies_json(
    user: CurrentUser, workspace: CurrentWorkspace
) -> StreamingResponse:
    workspace_id = workspace.id
    user_id = user.id
    counter = _Counter()

    async def body() -> AsyncIterator[str]:
        async with SessionLocal() as db:
            meta = _meta(await count_companies(db, workspace_id), {})
            async for chunk in _json_body(counter.watch(iter_companies(db, workspace_id)), meta):
                yield chunk

    return StreamingResponse(
        body(),
        media_type="application/json",
        headers=_headers("companies", "json"),
        background=_audit(workspace_id, user_id, "companies", "json", {}, counter),
    )
