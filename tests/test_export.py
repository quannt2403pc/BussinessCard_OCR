import csv
import io
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.card import BusinessCard, CardStatus
from app.models.company import Company, CompanyProfile
from app.models.user import User
from app.routers import export
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
        db_session, "owner-export@example.com", "Chủ sở hữu dữ liệu test", user_id=OWNER_ID
    )


class SessionFactory:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def __call__(self) -> "SessionFactory":
        return self

    async def __aenter__(self) -> AsyncSession:
        return self.session

    async def __aexit__(self, *exc: object) -> bool:
        return False


@pytest.fixture
async def client(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setattr(export, "SessionLocal", SessionFactory(db_session))
    app = FastAPI()
    app.include_router(export.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


def card(**kwargs: object) -> BusinessCard:
    values: dict[str, object] = {
        "user_id": OWNER_ID,
        "image_path": f"export/{uuid.uuid4().hex[:8]}.jpg",
        "image_hash": uuid.uuid4().hex * 2,
        "status": CardStatus.CONFIRMED,
    }
    values.update(kwargs)
    return BusinessCard(**values)


def read_csv(response: httpx.Response) -> list[list[str]]:
    text = response.content.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


async def test_cards_csv_keeps_bom_header_and_vietnamese(
    db_session: AsyncSession, owner: User, client: httpx.AsyncClient
) -> None:
    company = Company(user_id=OWNER_ID, display_name="Công ty FPT", name_normalized="fpt")
    db_session.add(company)
    await db_session.flush()
    db_session.add(card(full_name="Nguyễn Văn A", job_title="Giám đốc", company_id=company.id))
    await db_session.flush()

    response = await client.get("/api/export/cards.csv")

    assert response.status_code == 200
    assert response.content.startswith(b"\xef\xbb\xbf")
    assert "attachment" in response.headers["content-disposition"]
    rows = read_csv(response)
    assert tuple(rows[0]) == export.CardExportRow.columns()
    assert rows[1][rows[0].index("full_name")] == "Nguyễn Văn A"
    assert rows[1][rows[0].index("company_name")] == "Công ty FPT"


async def test_cards_csv_exports_card_without_company(
    db_session: AsyncSession, owner: User, client: httpx.AsyncClient
) -> None:
    db_session.add(card(full_name="Trần Thị B", company_name_raw="Cty chưa gắn"))
    await db_session.flush()

    rows = read_csv(await client.get("/api/export/cards.csv"))

    assert len(rows) == 2
    assert rows[1][rows[0].index("company_name")] == ""
    assert rows[1][rows[0].index("company_name_raw")] == "Cty chưa gắn"


async def test_cards_filter_by_status(
    db_session: AsyncSession, owner: User, client: httpx.AsyncClient
) -> None:
    db_session.add_all(
        [
            card(full_name="Đã xác nhận", status=CardStatus.CONFIRMED),
            card(full_name="Chờ duyệt", status=CardStatus.NEEDS_REVIEW),
        ]
    )
    await db_session.flush()

    payload = json.loads((await client.get("/api/export/cards.json?status=confirmed")).text)

    assert payload["total"] == 1
    assert payload["filters"] == {"status": "confirmed"}
    assert [item["full_name"] for item in payload["items"]] == ["Đã xác nhận"]


async def test_cards_invalid_status_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/export/cards.csv?status=xong")

    assert response.status_code == 400
    assert "pending" in response.json()["detail"]


async def test_companies_export_includes_company_without_profile(
    db_session: AsyncSession, owner: User, client: httpx.AsyncClient
) -> None:
    company = Company(
        user_id=OWNER_ID, display_name="Chưa có hồ sơ", name_normalized="chua co ho so"
    )
    db_session.add(company)
    await db_session.flush()

    rows = read_csv(await client.get("/api/export/companies.csv"))

    assert len(rows) == 2
    assert rows[1][rows[0].index("display_name")] == "Chưa có hồ sơ"
    assert rows[1][rows[0].index("profile_status")] == ""
    assert rows[1][rows[0].index("legal_name")] == ""
    assert rows[1][rows[0].index("sources")] == ""
    assert rows[1][rows[0].index("contact_count")] == "0"


async def test_companies_export_profile_lists_and_sources(
    db_session: AsyncSession, owner: User, client: httpx.AsyncClient
) -> None:
    company = Company(user_id=OWNER_ID, display_name="FPT Software", name_normalized="fpt software")
    db_session.add(company)
    await db_session.flush()
    db_session.add_all(
        [
            card(company_id=company.id),
            card(company_id=company.id),
            CompanyProfile(
                user_id=OWNER_ID,
                company_id=company.id,
                status="generated",
                legal_name="Công ty TNHH Phần mềm FPT",
                industry=["Phần mềm", "Dịch vụ CNTT"],
                sources={"legal_name": [{"url": "https://masothue.com/abc"}]},
                generated_at=datetime.now(UTC).replace(tzinfo=None),
            ),
        ]
    )
    await db_session.flush()

    rows = read_csv(await client.get("/api/export/companies.csv"))
    cells = dict(zip(rows[0], rows[1], strict=True))

    assert cells["contact_count"] == "2"
    assert cells["industry"] == "Phần mềm; Dịch vụ CNTT"
    assert json.loads(cells["sources"])["legal_name"][0]["url"] == "https://masothue.com/abc"

    payload = json.loads((await client.get("/api/export/companies.json")).text)
    item = payload["items"][0]

    assert payload["total"] == 1
    assert item["industry"] == ["Phần mềm", "Dịch vụ CNTT"]
    assert item["sources"]["legal_name"][0]["url"] == "https://masothue.com/abc"
    assert item["profile_status"] == "generated"


async def test_export_reads_every_row_across_batches(
    db_session: AsyncSession,
    owner: User,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(export, "EXPORT_BATCH_SIZE", 2)
    db_session.add_all([card(full_name=f"Người {index}") for index in range(5)])
    await db_session.flush()

    payload = json.loads((await client.get("/api/export/cards.json")).text)
    names = sorted(item["full_name"] for item in payload["items"])

    assert payload["total"] == 5
    assert names == [f"Người {index}" for index in range(5)]
    assert len({item["id"] for item in payload["items"]}) == 5
