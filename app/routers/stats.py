from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import CurrentUser
from app.core.workspace import CurrentWorkspace
from app.models.card import BusinessCard, CardStatus
from app.models.company import Company, CompanyProfile
from app.repositories.company import FINISHED_PROFILE_STATUSES

router = APIRouter()


class StatsOut(BaseModel):
    total_cards: int
    confirmed_cards: int
    needs_review_cards: int
    total_companies: int
    total_profiles: int
    review_rate: float


@router.get("/dashboard", tags=["ui"])
async def dashboard_page() -> RedirectResponse:
    """`/dashboard` gộp vào trang chủ — chuyển hướng **301** về `/` (QĐ-2 của D14, task 14.6).

    Hai trang đang chồng nhau: `/` là khung rỗng của D1, `/dashboard` mới là chỗ có nội dung. Giữ
    cả hai thì nav 5 mục + 4 liên kết bên phải = 9 mục một hàng, tràn ngang ở 375px.

    301 chứ không 302: đây là chuyển nhà vĩnh viễn, để trình duyệt và trang đánh dấu của người
    dùng cập nhật luôn. `docs/api.md` mục 7 và `docs/demo-runbook.md` còn trỏ vào `/dashboard`,
    nên **không** được xoá thẳng route — xoá là 404 ngay giữa buổi demo.
    """
    return RedirectResponse("/", status_code=status.HTTP_301_MOVED_PERMANENTLY)


@router.get("/api/stats", response_model=StatsOut, tags=["stats"])
async def get_stats(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: CurrentUser,
    workspace: CurrentWorkspace,
) -> StatsOut:
    """Số liệu của **không gian làm việc đang mở**, không phải của riêng người đang xem.

    Đổi khoá ở `NEXT-05`. Trước đó ba câu đếm dưới đây lọc theo `user.id`, và đó là chỗ
    cuối cùng trong app còn tách dữ liệu theo tài khoản: cả `tests/test_stats.py` lẫn
    `tests/test_isolation.py` đều xanh vì mỗi fixture chỉ có đúng một người trong một không
    gian, nên hai khoá trùng nhau. Ca "đồng nghiệp xem số liệu" ở `test_workspaces.py` mới
    tách được hai thứ đó ra.
    """
    companies = (
        select(func.count())
        .select_from(Company)
        .where(Company.workspace_id == workspace.id)
        .scalar_subquery()
    )
    profiles = (
        select(func.count())
        .select_from(CompanyProfile)
        .where(
            CompanyProfile.workspace_id == workspace.id,
            CompanyProfile.status.in_(FINISHED_PROFILE_STATUSES),
        )
        .scalar_subquery()
    )
    row = (
        await db.execute(
            select(
                func.count(BusinessCard.id),
                func.count(BusinessCard.id).filter(
                    BusinessCard.status == CardStatus.CONFIRMED.value
                ),
                func.count(BusinessCard.id).filter(
                    BusinessCard.status == CardStatus.NEEDS_REVIEW.value
                ),
                companies,
                profiles,
            ).where(
                BusinessCard.workspace_id == workspace.id,
                # Bản trùng đã gộp không tính vào số liệu (task NEXT-04).
                BusinessCard.merged_into_id.is_(None),
            )
        )
    ).one()

    total_cards, confirmed, needs_review, total_companies, total_profiles = row
    return StatsOut(
        total_cards=total_cards,
        confirmed_cards=confirmed,
        needs_review_cards=needs_review,
        total_companies=total_companies,
        total_profiles=total_profiles,
        review_rate=round(needs_review / total_cards, 3) if total_cards else 0.0,
    )
