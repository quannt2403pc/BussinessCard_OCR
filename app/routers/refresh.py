"""Làm mới hồ sơ doanh nghiệp: hồ sơ nào quá hạn, và lần tra lại thấy gì khác.

Chủ sở hữu: T | Task: NEXT-06 | xem Task.md

**Không có bộ lập lịch chạy ngầm, và đó là quyết định chứ không phải thiếu sót.** Từ `13.2`,
mỗi lời gọi LLM đi bằng credential OAuth **của chính người dùng**; một tiến trình chạy lúc 3
giờ sáng thì không có phiên của ai cả, nên nó sẽ phải tự chọn xem tiêu hạn mức của ai. Thêm
`celery beat` hay `APScheduler` cũng là thêm hạ tầng mà `Plan.md` mục 1.4 để ngoài phạm vi.

Nên "định kỳ" ở đây có nghĩa: **hệ thống tự biết hồ sơ nào quá hạn và gom sẵn thành một lượt
chạy một nút**, dùng lại đúng hàng đợi nền của `5.6` (`POST /api/companies/enrich-batch`). Việc
so sánh nằm trong `enrich_and_save()`, nên nó chạy cho **mọi** lượt tra lại, kể cả khi người
dùng tự bấm *Tạo lại hồ sơ* chứ không đi qua màn hình này.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import CurrentUser
from app.core.workspace import CurrentWorkspace, WriterWorkspace
from app.models.company import ProfileChange
from app.repositories import company as company_repo
from app.schemas.company import (
    ChangeListOut,
    FieldChangeOut,
    ProfileChangeOut,
    StaleListOut,
    StaleProfile,
)
from app.services.profile_diff import NOTABLE_FIELDS

router = APIRouter(prefix="/api/refresh", tags=["refresh"])

Session = Annotated[AsyncSession, Depends(get_db)]
NOT_FOUND = "not found"

#: Mặc định coi hồ sơ quá 90 ngày là cũ. Không phải con số thiêng: địa chỉ và quy mô doanh
#: nghiệp đổi theo quý chứ không theo tuần, mà mỗi lượt tra lại là một lần tiêu hạn mức model.
DEFAULT_STALE_DAYS = 90
MAX_STALE_DAYS = 3650

FIELD_LABELS: dict[str, str] = {
    "legal_name": "Tên pháp lý",
    "tax_code": "Mã số thuế",
    "founded_year": "Năm thành lập",
    "size_label": "Quy mô",
    "employee_range": "Số nhân sự",
    "industry": "Ngành nghề",
    "products": "Sản phẩm / dịch vụ",
    "address": "Địa chỉ",
    "website": "Website",
    "phone": "Điện thoại",
    "email": "Email",
}


def _label(name: str) -> str:
    return FIELD_LABELS.get(name, name)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _change_out(change: ProfileChange, display_name: str) -> ProfileChangeOut:
    payload: dict[str, Any] = change.changes or {}
    fields: dict[str, Any] = payload.get("changes") or {}
    return ProfileChangeOut(
        id=change.id,
        company_id=change.company_id,
        display_name=display_name,
        detected_at=_as_utc(change.detected_at),
        notable=change.notable,
        changes=[
            FieldChangeOut(
                field=name,
                label=_label(name),
                old=values.get("old"),
                new=values.get("new"),
                notable=name in NOTABLE_FIELDS,
            )
            for name, values in fields.items()
        ],
        missing=[_label(name) for name in payload.get("missing") or []],
    )


@router.get("/stale", response_model=StaleListOut)
async def stale_profiles(
    db: Session,
    user: CurrentUser,
    workspace: CurrentWorkspace,
    days: Annotated[int, Query(ge=1, le=MAX_STALE_DAYS)] = DEFAULT_STALE_DAYS,
) -> StaleListOut:
    """Hồ sơ lâu rồi chưa đi tra lại — đưa thẳng danh sách này vào `enrich-batch` là xong."""
    now = datetime.now(UTC)
    rows = await company_repo.stale_profiles(
        db, workspace_id=workspace.id, before=now - timedelta(days=days)
    )
    items = []
    for company, checked_at in rows:
        since = (now - _as_utc(checked_at)).days if checked_at is not None else None
        items.append(
            StaleProfile(
                company_id=company.id,
                display_name=company.display_name,
                display_name_vi=company.display_name_vi,
                checked_at=_as_utc(checked_at) if checked_at is not None else None,
                days_since=since,
            )
        )
    return StaleListOut(days=days, total=len(items), items=items)


@router.get("/changes", response_model=ChangeListOut)
async def list_changes(
    db: Session,
    user: CurrentUser,
    workspace: CurrentWorkspace,
    unseen_only: Annotated[bool, Query()] = True,
) -> ChangeListOut:
    """Lần tra lại gần đây thấy gì khác. Đáng chú ý xếp trước, rồi tới mới nhất."""
    rows = await company_repo.list_changes(db, workspace_id=workspace.id, unseen_only=unseen_only)
    items = [_change_out(change, name) for change, name in rows]
    return ChangeListOut(
        total=len(items),
        notable=sum(1 for item in items if item.notable),
        items=items,
    )


@router.post("/changes/{change_id}/ack", status_code=status.HTTP_204_NO_CONTENT)
async def acknowledge(
    change_id: uuid.UUID, db: Session, user: CurrentUser, workspace: WriterWorkspace
) -> None:
    """Đánh dấu *đã xem*. Dòng nhật ký ở lại, chỉ thôi nằm trong danh sách cần đọc."""
    done = await company_repo.acknowledge_change(
        db, change_id, workspace_id=workspace.id, at=datetime.now(UTC)
    )
    if not done:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    await db.commit()
