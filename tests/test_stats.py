import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.templates import BASE_DIR
from app.models.card import BusinessCard, CardStatus
from app.models.company import Company, CompanyProfile
from app.routers import stats


@pytest.fixture
async def page_client() -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(stats.router)
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


def card(status: CardStatus) -> BusinessCard:
    return BusinessCard(
        image_path=f"stats/{uuid.uuid4().hex[:8]}.jpg",
        image_hash=uuid.uuid4().hex * 2,
        status=status,
    )


async def test_dashboard_page_renders(page_client: httpx.AsyncClient) -> None:
    response = await page_client.get("/dashboard")
    assert response.status_code == 200
    assert "Bảng số liệu" in response.text


async def test_stats_counts_cards_companies_and_profiles(db_session: AsyncSession) -> None:
    company = Company(id=uuid.uuid4(), display_name="ABC", name_normalized="abc")
    other = Company(id=uuid.uuid4(), display_name="XYZ", name_normalized="xyz")
    db_session.add_all([company, other])
    await db_session.flush()
    db_session.add_all(
        [
            card(CardStatus.CONFIRMED),
            card(CardStatus.CONFIRMED),
            card(CardStatus.NEEDS_REVIEW),
            card(CardStatus.PENDING),
            CompanyProfile(
                company_id=company.id, status="generated", generated_at=datetime.now(UTC)
            ),
            # Hồ sơ `draft` là lượt đang chạy dở, chưa phải hồ sơ thật → không được đếm.
            CompanyProfile(company_id=other.id, status="draft"),
        ]
    )
    await db_session.flush()

    result = await stats.get_stats(db_session)

    assert result.total_cards == 4
    assert result.confirmed_cards == 2
    assert result.needs_review_cards == 1
    assert result.total_companies == 2
    assert result.total_profiles == 1
    assert result.review_rate == 0.25


async def test_stats_on_empty_database(db_session: AsyncSession) -> None:
    result = await stats.get_stats(db_session)

    # Chia cho 0 là lỗi duy nhất mà endpoint này có thể tự gây ra.
    assert result.review_rate == 0.0
    assert (result.total_cards, result.total_companies, result.total_profiles) == (0, 0, 0)
