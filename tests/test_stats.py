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
from app.models.user import User
from app.routers import stats
from tests.conftest import make_user

#: Chủ sở hữu của mọi bản ghi trong file này (task 12.8). `0005` đặt `user_id` là NOT NULL
#: trên cả 6 bảng dữ liệu, nên object ORM nào ghi xuống DB cũng phải có nó. Test ở đây không
#: kiểm việc tách dữ liệu (đó là 12.6/12.7) nên một chủ sở hữu duy nhất là đủ.
OWNER_ID = uuid.uuid4()


@pytest.fixture
async def owner(db_session: AsyncSession) -> User:
    """Hàng `users` cho `OWNER_ID` (task 12.8).

    Khoá ngoại `business_cards.user_id` / `companies.user_id` (revision `0005`) đòi chủ sở hữu
    tồn tại thật, nên test nào **ghi xuống DB** cũng phải dựng hàng này trước. Test chạy trên
    `FakeSession` thì không cần — vì thế fixture này không autouse.
    """
    return await make_user(
        db_session, "owner-stats@example.com", "Chủ sở hữu dữ liệu test", user_id=OWNER_ID
    )


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
        user_id=OWNER_ID,
        image_path=f"stats/{uuid.uuid4().hex[:8]}.jpg",
        image_hash=uuid.uuid4().hex * 2,
        status=status,
    )


async def test_dashboard_redirects_to_home(page_client: httpx.AsyncClient) -> None:
    """QĐ-2 của D14: `/dashboard` gộp vào trang chủ, route cũ chuyển hướng 301 (task 14.6).

    Giữ lại phép kiểm chứ không xoá: `docs/api.md` mục 7 và `docs/demo-runbook.md` còn trỏ vào
    `/dashboard`, nên đường dẫn này phải **còn sống**. Xoá route là 404 ngay giữa buổi demo.
    """
    response = await page_client.get("/dashboard")
    assert response.status_code == 301
    assert response.headers["location"] == "/"


async def test_stats_counts_cards_companies_and_profiles(
    db_session: AsyncSession, owner: User
) -> None:
    company = Company(user_id=OWNER_ID, id=uuid.uuid4(), display_name="ABC", name_normalized="abc")
    other = Company(user_id=OWNER_ID, id=uuid.uuid4(), display_name="XYZ", name_normalized="xyz")
    db_session.add_all([company, other])
    await db_session.flush()
    db_session.add_all(
        [
            card(CardStatus.CONFIRMED),
            card(CardStatus.CONFIRMED),
            card(CardStatus.NEEDS_REVIEW),
            card(CardStatus.PENDING),
            CompanyProfile(
                user_id=OWNER_ID,
                company_id=company.id,
                status="generated",
                generated_at=datetime.now(UTC),
            ),
            # Hồ sơ `draft` là lượt đang chạy dở, chưa phải hồ sơ thật → không được đếm.
            CompanyProfile(user_id=OWNER_ID, company_id=other.id, status="draft"),
        ]
    )
    await db_session.flush()

    result = await stats.get_stats(db_session, owner)

    assert result.total_cards == 4
    assert result.confirmed_cards == 2
    assert result.needs_review_cards == 1
    assert result.total_companies == 2
    assert result.total_profiles == 1
    assert result.review_rate == 0.25


async def test_stats_on_empty_database(db_session: AsyncSession, owner: User) -> None:
    result = await stats.get_stats(db_session, owner)

    # Chia cho 0 là lỗi duy nhất mà endpoint này có thể tự gây ra.
    assert result.review_rate == 0.0
    assert (result.total_cards, result.total_companies, result.total_profiles) == (0, 0, 0)
