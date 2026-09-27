"""Tuân thủ Nghị định 13/2023 về bảo vệ dữ liệu cá nhân.

Chủ sở hữu: T | Task: NEXT-07 | xem Task.md

Dữ liệu trong hệ thống này **là dữ liệu cá nhân của người khác** — tên, số điện thoại, email,
địa chỉ của người đưa danh thiếp. Bốn việc ở đây trả lời bốn câu pháp chế doanh nghiệp sẽ hỏi
trước khi ký:

| Câu hỏi | Ở đâu |
|---|---|
| Ai đã lấy dữ liệu ra khỏi hệ thống, lúc nào? | `GET /api/privacy/logs` — `routers/export.py` tự ghi |
| Giữ dữ liệu bao lâu? | `GET|PUT /api/privacy/retention` |
| Người ta yêu cầu xoá thì làm thế nào? | `POST /api/privacy/subject` rồi `POST /api/privacy/erase` |
| Thu thập để làm gì? | trang `/privacy` |

**Không có gì tự xoá.** Quá hạn lưu trữ thì hệ thống đếm ra và chờ người bấm; tự xoá dữ liệu
của người dùng theo một con số họ đặt ba tháng trước là việc không ai cho phép.
"""

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.security import CurrentUser
from app.core.templates import templates
from app.core.workspace import CurrentWorkspace, WriterWorkspace
from app.models.privacy import PrivacyAction, PrivacyLog
from app.repositories import privacy as privacy_repo
from app.schemas.privacy import (
    EraseOut,
    LogListOut,
    PrivacyLogOut,
    PurgeOut,
    RetentionIn,
    RetentionOut,
    SubjectCard,
    SubjectIn,
    SubjectOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["privacy"])

Session = Annotated[AsyncSession, Depends(get_db)]

ACTION_LABELS: dict[str, str] = {
    PrivacyAction.EXPORT: "Xuất dữ liệu ra file",
    PrivacyAction.ERASE: "Xoá theo yêu cầu của chủ thể dữ liệu",
    PrivacyAction.RETENTION: "Xoá vì quá hạn lưu trữ",
}


def _log_out(entry: PrivacyLog) -> PrivacyLogOut:
    out = PrivacyLogOut.model_validate(entry)
    out.label = ACTION_LABELS.get(entry.action, entry.action)
    out.created_at = (
        out.created_at.replace(tzinfo=UTC) if not out.created_at.tzinfo else out.created_at
    )
    return out


def _remove_images(paths: list[str]) -> int:
    """Xoá file ảnh sau khi hàng đã đi. Trả số file xoá được.

    Xoá hàng trước, xoá file sau — cùng luật với `4.2` của Q: transaction hỏng mà file đã mất
    thì còn lại một bản ghi trỏ vào hư không, hỏng im lặng. Ngược lại, file thừa nằm lại trong
    volume không làm hỏng gì, chỉ tốn chỗ.
    """
    root = settings.upload_dir.resolve()
    removed = 0
    for image_path in paths:
        try:
            target = (root / image_path).resolve()
            target.relative_to(root)
        except (OSError, ValueError):
            logger.warning("image_path %r nằm ngoài UPLOAD_DIR, không xoá", image_path)
            continue
        try:
            target.unlink()
            removed += 1
        except OSError as exc:
            logger.warning("Không xoá được %s: %s", target, exc)
    return removed


@router.get("/privacy", response_class=HTMLResponse, tags=["ui"])
async def privacy_page(request: Request) -> HTMLResponse:
    """Trang **mục đích thu thập** + nhật ký + hạn lưu trữ + xoá theo yêu cầu."""
    return templates.TemplateResponse(
        request, "privacy.html", {"active_nav": None, "title": "Dữ liệu cá nhân"}
    )


@router.get("/api/privacy/logs", response_model=LogListOut)
async def list_logs(db: Session, user: CurrentUser, workspace: CurrentWorkspace) -> LogListOut:
    entries = await privacy_repo.recent_logs(db, workspace_id=workspace.id)
    return LogListOut(total=len(entries), items=[_log_out(entry) for entry in entries])


@router.get("/api/privacy/retention", response_model=RetentionOut)
async def get_retention(
    db: Session, user: CurrentUser, workspace: CurrentWorkspace
) -> RetentionOut:
    now = datetime.now(UTC)
    expired = (
        await privacy_repo.count_expired(
            db, workspace_id=workspace.id, days=user.retention_days, now=now
        )
        if user.retention_days
        else 0
    )
    oldest = await privacy_repo.oldest_upload(db, workspace_id=workspace.id)
    return RetentionOut(
        days=user.retention_days,
        total_contacts=await privacy_repo.count_contacts(db, workspace_id=workspace.id),
        expired=expired,
        oldest_upload=oldest.replace(tzinfo=UTC) if oldest else None,
    )


@router.put("/api/privacy/retention", response_model=RetentionOut)
async def put_retention(
    body: RetentionIn, db: Session, user: CurrentUser, workspace: WriterWorkspace
) -> RetentionOut:
    """Đặt hoặc gỡ hạn lưu trữ. **Đặt hạn không xoá gì cả** — xoá là một cú bấm riêng."""
    await privacy_repo.set_retention(db, user, body.days)
    await db.commit()
    return await get_retention(db, user, workspace)


@router.post("/api/privacy/purge", response_model=PurgeOut)
async def purge_expired(db: Session, user: CurrentUser, workspace: WriterWorkspace) -> PurgeOut:
    """Xoá những danh thiếp đã quá hạn lưu trữ đang đặt. Không có hạn thì không xoá gì."""
    if not user.retention_days:
        return PurgeOut(days=0, erased=0, images_removed=0)

    now = datetime.now(UTC)
    cards = await privacy_repo.expired_cards(
        db, workspace_id=workspace.id, days=user.retention_days, now=now
    )
    paths = await privacy_repo.erase_cards(db, cards, workspace_id=workspace.id)
    await privacy_repo.log(
        db,
        workspace_id=workspace.id,
        user_id=user.id,
        action=PrivacyAction.RETENTION,
        detail={"days": user.retention_days},
        record_count=len(cards),
    )
    await db.commit()

    removed = _remove_images(paths)
    logger.info("Quá hạn lưu trữ: xoá %d danh thiếp của %s", len(cards), user.id)
    return PurgeOut(days=user.retention_days, erased=len(cards), images_removed=removed)


@router.post("/api/privacy/subject", response_model=SubjectOut)
async def find_subject(
    body: SubjectIn, db: Session, user: CurrentUser, workspace: CurrentWorkspace
) -> SubjectOut:
    """Tìm mọi danh thiếp của một chủ thể dữ liệu. **Chỉ tìm, không xoá.**

    Bước riêng là có chủ đích: người dùng phải nhìn thấy đúng bao nhiêu bản ghi sắp mất trước
    khi bấm xoá, vì sau đó không có đường nào lấy lại.
    """
    cards = await privacy_repo.find_subject_cards(db, body.term, workspace_id=workspace.id)
    return SubjectOut(
        term=body.term,
        total=len(cards),
        items=[SubjectCard.model_validate(card) for card in cards],
    )


@router.post("/api/privacy/erase", response_model=EraseOut)
async def erase_subject(
    body: SubjectIn, db: Session, user: CurrentUser, workspace: WriterWorkspace
) -> EraseOut:
    """Xoá **vĩnh viễn** mọi dữ liệu của một chủ thể: hàng, ảnh, ghi chú, chunk Knowledge Base.

    Xoá thật chứ không gắn cờ ẩn, khác hẳn `merged_into_id` của `NEXT-04`: ở đó máy *đoán*, ở
    đây chủ thể dữ liệu **yêu cầu**, và quyền ấy không được phục vụ bằng một cờ.
    """
    cards = await privacy_repo.find_subject_cards(db, body.term, workspace_id=workspace.id)
    paths = await privacy_repo.erase_cards(db, cards, workspace_id=workspace.id)
    await privacy_repo.log(
        db,
        workspace_id=workspace.id,
        user_id=user.id,
        action=PrivacyAction.ERASE,
        detail={"term": body.term},
        record_count=len(cards),
    )
    await db.commit()

    removed = _remove_images(paths)
    logger.info("Xoá theo yêu cầu: %d danh thiếp của %s", len(cards), user.id)
    return EraseOut(term=body.term, erased=len(cards), images_removed=removed)
