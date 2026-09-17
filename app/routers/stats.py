from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.templates import templates
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


@router.get("/dashboard", response_class=HTMLResponse, tags=["ui"])
async def dashboard_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "dashboard.html", {"active_nav": "dashboard"})


@router.get("/api/stats", response_model=StatsOut, tags=["stats"])
async def get_stats(db: Annotated[AsyncSession, Depends(get_db)]) -> StatsOut:
    companies = select(func.count()).select_from(Company).scalar_subquery()
    profiles = (
        select(func.count())
        .select_from(CompanyProfile)
        .where(CompanyProfile.status.in_(FINISHED_PROFILE_STATUSES))
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
