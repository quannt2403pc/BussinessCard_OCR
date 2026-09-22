import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.templates import BASE_DIR
from app.models.card import BusinessCard
from app.models.company import Company, CompanyProfile
from app.models.kb import KBChunk, KBSourceType
from app.models.user import User
from app.repositories import company as company_repo
from app.repositories import enrich_job as job_repo
from app.repositories.company import CompanyRow
from app.routers import companies
from app.routers.companies import page_count, to_list_item
from app.services import enrich_jobs
from app.services.normalize_company import normalize_company_name
from tests.conftest import make_user

#: Chủ sở hữu của mọi bản ghi trong file này (task 12.8). `0005` đặt `user_id` là NOT NULL
#: trên cả 6 bảng dữ liệu, nên object ORM nào ghi xuống DB cũng phải có nó. File này không
#: kiểm việc tách dữ liệu (đó là 12.6/12.7 của T) nên một chủ sở hữu duy nhất là đủ.
OWNER_ID = uuid.uuid4()


@pytest.fixture
async def owner(db_session: AsyncSession) -> User:
    """Hàng `users` cho `OWNER_ID` (task 12.8).

    Khoá ngoại `business_cards.user_id` / `companies.user_id` (revision `0005`) đòi chủ sở hữu
    tồn tại thật, nên test nào **ghi xuống DB** cũng phải dựng hàng này trước. Test chạy trên
    `FakeSession` thì không cần — vì thế fixture này không autouse.
    """
    return await make_user(
        db_session, "owner-company_api@example.com", "Chủ sở hữu dữ liệu test", user_id=OWNER_ID
    )


COMPANY_ID = uuid.uuid4()


@pytest.fixture(autouse=True)
def indexed(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    calls: list[uuid.UUID] = []

    async def record(profile_id: uuid.UUID) -> None:
        calls.append(profile_id)

    monkeypatch.setattr(enrich_jobs, "index_profile", record)
    return calls


def make_company(**overrides: Any) -> Company:
    values: dict[str, Any] = {
        "id": COMPANY_ID,
        "user_id": OWNER_ID,
        "display_name": "Công ty TNHH ABC",
        "name_normalized": "abc",
        "aliases": None,
    }
    values.update(overrides)
    return Company(**values)


def make_card() -> BusinessCard:
    return BusinessCard(
        user_id=OWNER_ID,
        id=uuid.uuid4(),
        status="confirmed",
        full_name="Nguyễn Văn A",
        company_id=COMPANY_ID,
        image_path="ab/abc.jpg",
        uploaded_at=datetime(2026, 9, 15, 8, 0),
    )


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def no_related(monkeypatch: pytest.MonkeyPatch) -> None:
    async def none_by_tax(db: Any, company_id: uuid.UUID, tax_code: str | None) -> list[Company]:
        return []

    async def none_by_domain(db: Any, company_id: uuid.UUID) -> list[Any]:
        return []

    monkeypatch.setattr(company_repo, "same_tax_code", none_by_tax)
    monkeypatch.setattr(company_repo, "same_domain", none_by_domain)


@pytest.fixture
async def client(session: FakeSession) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(companies.router)
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    async def fake_db() -> AsyncIterator[FakeSession]:
        yield session

    app.dependency_overrides[get_db] = fake_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def test_list_page_renders(client: httpx.AsyncClient) -> None:
    response = await client.get("/companies")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Tạo hồ sơ doanh nghiệp" in response.text


async def test_detail_page_renders_with_company_id(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/companies/{COMPANY_ID}")
    assert response.status_code == 200
    assert str(COMPANY_ID) in response.text


async def test_detail_page_rejects_non_uuid(client: httpx.AsyncClient) -> None:
    assert (await client.get("/companies/abc")).status_code == 422


@pytest.mark.parametrize(
    ("total", "size", "expected"), [(0, 20, 1), (20, 20, 1), (21, 20, 2), (101, 100, 2)]
)
def test_page_count(total: int, size: int, expected: int) -> None:
    assert page_count(total, size) == expected


def test_list_item_from_row() -> None:
    row = CompanyRow(make_company(), 3, "generated", datetime(2026, 9, 15, 8, 0))
    assert to_list_item(row).model_dump(mode="json") == {
        "id": str(COMPANY_ID),
        "display_name": "Công ty TNHH ABC",
        "name_normalized": "abc",
        "contact_count": 3,
        "profile_status": "generated",
        "profile_generated_at": "2026-09-15T08:00:00Z",
    }


def test_list_item_without_profile() -> None:
    item = to_list_item(CompanyRow(make_company(), 0, None, None))
    assert item.profile_status is None
    assert item.profile_generated_at is None


async def test_list_passes_filters_and_paginates(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    received: dict[str, Any] = {}

    async def fake_list(db: Any, **kwargs: Any) -> tuple[list[CompanyRow], int]:
        received.update(kwargs)
        return [CompanyRow(make_company(), 2, None, None)], 41

    monkeypatch.setattr(company_repo, "list_companies", fake_list)
    response = await client.get(
        "/api/companies", params={"q": "abc", "has_profile": "false", "page": 3, "size": 20}
    )

    assert response.status_code == 200
    assert received == {
        "q": "abc",
        "has_profile": False,
        "archived": False,
        "page": 3,
        "size": 20,
    }
    body = response.json()
    assert (body["total"], body["page"], body["size"], body["pages"]) == (41, 3, 20, 3)
    assert body["items"][0]["contact_count"] == 2


async def test_list_uses_defaults(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    received: dict[str, Any] = {}

    async def fake_list(db: Any, **kwargs: Any) -> tuple[list[CompanyRow], int]:
        received.update(kwargs)
        return [], 0

    monkeypatch.setattr(company_repo, "list_companies", fake_list)
    response = await client.get("/api/companies")

    assert response.status_code == 200
    assert received == {
        "q": None,
        "has_profile": None,
        "archived": False,
        "page": 1,
        "size": 20,
    }
    assert response.json() == {"items": [], "total": 0, "page": 1, "size": 20, "pages": 1}


@pytest.mark.parametrize("params", [{"size": 101}, {"size": 0}, {"page": 0}])
async def test_list_rejects_bad_paging(client: httpx.AsyncClient, params: dict[str, int]) -> None:
    assert (await client.get("/api/companies", params=params)).status_code == 422


async def test_detail_with_profile_and_contacts(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, no_related: None
) -> None:
    async def fake_row(db: Any, company_id: uuid.UUID) -> CompanyRow:
        return CompanyRow(make_company(aliases=None), 1, "generated", datetime(2026, 9, 15, 8, 0))

    async def fake_profile(db: Any, company_id: uuid.UUID) -> CompanyProfile:
        return CompanyProfile(
            user_id=OWNER_ID,
            company_id=company_id,
            tax_code="0301234567",
            industry=None,
            products=None,
            sources={
                "tax_code": [
                    {
                        "url": "https://masothue.com/0301234567",
                        "title": "MST",
                        "retrieved_at": "2026-09-15T08:00:00+00:00",
                    }
                ]
            },
            status="generated",
            generated_at=datetime(2026, 9, 15, 8, 0),
        )

    async def fake_contacts(db: Any, company_id: uuid.UUID) -> list[BusinessCard]:
        return [make_card()]

    monkeypatch.setattr(company_repo, "get_company_row", fake_row)
    monkeypatch.setattr(company_repo, "get_profile", fake_profile)
    monkeypatch.setattr(company_repo, "list_contacts", fake_contacts)

    response = await client.get(f"/api/companies/{COMPANY_ID}")

    assert response.status_code == 200
    body = response.json()
    assert body["aliases"] == []
    assert body["profile"]["tax_code"] == "0301234567"
    assert body["profile"]["industry"] == []
    assert body["profile"]["generated_at"] == "2026-09-15T08:00:00Z"
    assert body["profile"]["sources"]["tax_code"][0]["url"] == "https://masothue.com/0301234567"
    assert body["profile"]["unverified_fields"] == []
    assert [contact["full_name"] for contact in body["contacts"]] == ["Nguyễn Văn A"]


async def test_detail_without_profile(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, no_related: None
) -> None:
    async def fake_row(db: Any, company_id: uuid.UUID) -> CompanyRow:
        return CompanyRow(make_company(aliases=["ABC Co"]), 0, None, None)

    async def nothing(db: Any, company_id: uuid.UUID) -> None:
        return None

    async def no_contacts(db: Any, company_id: uuid.UUID) -> list[BusinessCard]:
        return []

    monkeypatch.setattr(company_repo, "get_company_row", fake_row)
    monkeypatch.setattr(company_repo, "get_profile", nothing)
    monkeypatch.setattr(company_repo, "list_contacts", no_contacts)

    body = (await client.get(f"/api/companies/{COMPANY_ID}")).json()
    assert body["profile"] is None
    assert body["aliases"] == ["ABC Co"]
    assert body["contacts"] == []


async def test_detail_unknown_company(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def nothing(db: Any, company_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(company_repo, "get_company_row", nothing)
    response = await client.get(f"/api/companies/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json() == {"detail": "not found"}


async def test_detail_rejects_non_uuid(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/companies/abc")).status_code == 422


async def test_contacts_unknown_company(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def nothing(db: Any, company_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(company_repo, "get_company", nothing)
    assert (await client.get(f"/api/companies/{uuid.uuid4()}/contacts")).status_code == 404


def make_profile(**overrides: Any) -> CompanyProfile:
    values: dict[str, Any] = {
        "user_id": OWNER_ID,
        "company_id": COMPANY_ID,
        "tax_code": "0301234567",
        "status": "verified",
        "sources": {},
        "industry": None,
        "products": None,
    }
    values.update(overrides)
    return CompanyProfile(**values)


@pytest.fixture
def no_running_job(monkeypatch: pytest.MonkeyPatch) -> None:
    async def nothing(db: Any, company_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(job_repo, "active_job_id", nothing)


async def test_patch_profile_saves_and_commits(
    client: httpx.AsyncClient,
    session: FakeSession,
    no_running_job: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: dict[str, Any] = {}

    async def fake_update(
        db: Any, company_id: uuid.UUID, changes: dict[str, Any]
    ) -> CompanyProfile:
        received.update(changes)
        return make_profile(tax_code=changes.get("tax_code", "0301234567"))

    monkeypatch.setattr(company_repo, "update_profile", fake_update)
    response = await client.patch(
        f"/api/companies/{COMPANY_ID}/profile", json={"tax_code": "0309999999"}
    )

    assert response.status_code == 200
    assert received == {"tax_code": "0309999999"}
    assert session.commits == 1
    body = response.json()
    assert body["tax_code"] == "0309999999"
    assert body["status"] == "verified"


async def test_patch_profile_sends_only_given_fields(
    client: httpx.AsyncClient, no_running_job: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    received: dict[str, Any] = {}

    async def fake_update(
        db: Any, company_id: uuid.UUID, changes: dict[str, Any]
    ) -> CompanyProfile:
        received.update(changes)
        return make_profile()

    monkeypatch.setattr(company_repo, "update_profile", fake_update)
    await client.patch(
        f"/api/companies/{COMPANY_ID}/profile", json={"phone": None, "industry": None}
    )

    assert received == {"phone": None, "industry": []}


async def test_patch_profile_rejects_empty_body(
    client: httpx.AsyncClient, session: FakeSession
) -> None:
    response = await client.patch(f"/api/companies/{COMPANY_ID}/profile", json={})
    assert response.status_code == 400
    assert session.commits == 0


@pytest.mark.parametrize(
    "body",
    [
        {"status": "verified"},
        {"sources": {}},
        {"tax_code": "x" * 65},
        {"founded_year": 1700},
    ],
)
async def test_patch_profile_rejects_bad_body(
    client: httpx.AsyncClient, body: dict[str, Any]
) -> None:
    response = await client.patch(f"/api/companies/{COMPANY_ID}/profile", json=body)
    assert response.status_code == 422


async def test_patch_profile_without_profile(
    client: httpx.AsyncClient,
    session: FakeSession,
    no_running_job: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def nothing(db: Any, company_id: uuid.UUID, changes: dict[str, Any]) -> None:
        return None

    monkeypatch.setattr(company_repo, "update_profile", nothing)
    response = await client.patch(
        f"/api/companies/{COMPANY_ID}/profile", json={"tax_code": "0301234567"}
    )

    assert response.status_code == 404
    assert session.commits == 0


async def test_patch_profile_blocked_while_enriching(
    client: httpx.AsyncClient, session: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    running_job = uuid.uuid4()
    calls: list[str] = []

    async def active(db: Any, company_id: uuid.UUID) -> uuid.UUID:
        return running_job

    async def fake_update(
        db: Any, company_id: uuid.UUID, changes: dict[str, Any]
    ) -> CompanyProfile:
        calls.append("update")
        return make_profile()

    monkeypatch.setattr(job_repo, "active_job_id", active)
    monkeypatch.setattr(company_repo, "update_profile", fake_update)
    response = await client.patch(
        f"/api/companies/{COMPANY_ID}/profile", json={"tax_code": "0301234567"}
    )

    assert response.status_code == 409
    assert response.json()["existing_id"] == str(running_job)
    assert calls == []
    assert session.commits == 0


@pytest.fixture
async def db_client(db_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(companies.router)

    async def real_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = real_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def add_company(
    db: AsyncSession, display_name: str, aliases: list[str] | None = None
) -> Company:
    company = Company(
        user_id=OWNER_ID,
        display_name=display_name,
        name_normalized=normalize_company_name(display_name),
        aliases=aliases,
    )
    db.add(company)
    await db.flush()
    return company


async def add_card(db: AsyncSession, company: Company, full_name: str) -> BusinessCard:
    card = BusinessCard(
        user_id=OWNER_ID,
        image_path=f"test/{uuid.uuid4().hex[:8]}.jpg",
        image_hash=uuid.uuid4().hex * 2,
        status="confirmed",
        full_name=full_name,
        company_id=company.id,
    )
    db.add(card)
    await db.flush()
    return card


async def add_profile(db: AsyncSession, company: Company, **values: Any) -> CompanyProfile:
    profile = CompanyProfile(
        user_id=OWNER_ID, company_id=company.id, **{"status": "generated", **values}
    )
    db.add(profile)
    await db.flush()
    return profile


async def listed_names(client: httpx.AsyncClient, **params: Any) -> list[str]:
    response = await client.get("/api/companies", params=params)
    assert response.status_code == 200
    return [item["display_name"] for item in response.json()["items"]]


async def test_db_list_searches_name_alias_and_normalized_key(
    db_session: AsyncSession, owner: User, db_client: httpx.AsyncClient
) -> None:
    await add_company(db_session, "Công ty TNHH Logistics Đại Việt", ["Đại Việt Logistics"])
    await add_company(db_session, "Hanwha Precision Vietnam", ["한화정밀기계"])

    assert await listed_names(db_client, q="logistics") == ["Công ty TNHH Logistics Đại Việt"]
    assert await listed_names(db_client, q="한화") == ["Hanwha Precision Vietnam"]
    assert await listed_names(db_client, q="dai viet") == ["Công ty TNHH Logistics Đại Việt"]
    assert await listed_names(db_client, q="  hanwha   precision ") == ["Hanwha Precision Vietnam"]


async def test_db_list_treats_like_wildcards_literally(
    db_session: AsyncSession, owner: User, db_client: httpx.AsyncClient
) -> None:
    await add_company(db_session, "Công ty 100% Việt")
    await add_company(db_session, "Hanwha Precision Vietnam")

    assert await listed_names(db_client, q="%") == ["Công ty 100% Việt"]
    assert await listed_names(db_client, q="_") == []


async def test_db_list_filters_by_profile_state(
    db_session: AsyncSession, owner: User, db_client: httpx.AsyncClient
) -> None:
    generated = await add_company(db_session, "Alpha Generated")
    draft = await add_company(db_session, "Beta Draft")
    await add_company(db_session, "Gamma None")
    verified = await add_company(db_session, "Delta Verified")
    await add_profile(db_session, generated)
    await add_profile(db_session, draft, status="draft")
    await add_profile(db_session, verified, status="verified")

    assert await listed_names(db_client, has_profile="true") == [
        "Alpha Generated",
        "Delta Verified",
    ]
    assert await listed_names(db_client, has_profile="false") == ["Beta Draft", "Gamma None"]


async def test_db_list_paginates_and_counts_contacts(
    db_session: AsyncSession, owner: User, db_client: httpx.AsyncClient
) -> None:
    alpha = await add_company(db_session, "Alpha")
    await add_company(db_session, "Beta")
    await add_company(db_session, "Gamma")
    await add_card(db_session, alpha, "Người 1")
    await add_card(db_session, alpha, "Người 2")

    first = (await db_client.get("/api/companies", params={"size": 2})).json()
    second = (await db_client.get("/api/companies", params={"size": 2, "page": 2})).json()

    assert [item["display_name"] for item in first["items"]] == ["Alpha", "Beta"]
    assert [item["display_name"] for item in second["items"]] == ["Gamma"]
    assert (first["total"], first["pages"]) == (3, 2)
    assert first["items"][0]["contact_count"] == 2


async def test_db_detail_returns_profile_and_own_contacts_only(
    db_session: AsyncSession, owner: User, db_client: httpx.AsyncClient
) -> None:
    company = await add_company(db_session, "Công ty CP Sữa Mộc Châu")
    other = await add_company(db_session, "Hanwha Precision Vietnam")
    await add_profile(db_session, company, tax_code="0100233468", industry=["Sản xuất sữa"])
    await add_card(db_session, company, "Trần Thị Bình")
    await add_card(db_session, company, "Lê Văn Cường")
    await add_card(db_session, other, "Kim Min-jun")

    response = await db_client.get(f"/api/companies/{company.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["profile"]["tax_code"] == "0100233468"
    assert body["profile"]["industry"] == ["Sản xuất sữa"]
    assert body["contact_count"] == 2
    assert sorted(card["full_name"] for card in body["contacts"]) == [
        "Lê Văn Cường",
        "Trần Thị Bình",
    ]


async def test_db_patch_profile_persists_and_drops_only_edited_sources(
    db_session: AsyncSession, owner: User, db_client: httpx.AsyncClient, indexed: list[uuid.UUID]
) -> None:
    company = await add_company(db_session, "Công ty TNHH Logistics Đại Việt")
    profile = await add_profile(
        db_session,
        company,
        tax_code="0301234567",
        legal_name="Công ty TNHH Logistics Đại Việt",
        sources={
            "tax_code": [{"url": "https://masothue.com/0301234567"}],
            "legal_name": [{"url": "https://daiviet-logistics.vn"}],
        },
    )

    response = await db_client.patch(
        f"/api/companies/{company.id}/profile", json={"tax_code": "0309999999"}
    )

    assert response.status_code == 200
    stored = await db_session.scalar(
        select(CompanyProfile)
        .where(CompanyProfile.company_id == company.id)
        .execution_options(populate_existing=True)
    )
    assert stored is not None
    assert stored.tax_code == "0309999999"
    assert stored.status == "verified"
    assert set(stored.sources or {}) == {"legal_name"}
    assert indexed == [profile.id]


async def test_db_patch_profile_blocked_by_active_enrich_item(
    db_session: AsyncSession, owner: User, db_client: httpx.AsyncClient, indexed: list[uuid.UUID]
) -> None:
    company = await add_company(db_session, "Hanwha Precision Vietnam")
    await add_profile(db_session, company, tax_code="0312345678")
    job_id = await job_repo.create_job(db_session)
    await job_repo.add_item(db_session, job_id, company.id)

    response = await db_client.patch(
        f"/api/companies/{company.id}/profile", json={"tax_code": "0300000000"}
    )

    assert response.status_code == 409
    assert response.json()["existing_id"] == str(job_id)
    stored = await db_session.scalar(
        select(CompanyProfile.tax_code).where(CompanyProfile.company_id == company.id)
    )
    assert stored == "0312345678"
    assert indexed == []


async def test_db_detail_while_first_enrich_is_running(
    db_session: AsyncSession, owner: User, db_client: httpx.AsyncClient
) -> None:
    company = await add_company(db_session, "Công ty TNHH Logistics Đại Việt")
    await company_repo.ensure_draft_profile(db_session, company.id)

    response = await db_client.get(f"/api/companies/{company.id}")

    assert response.status_code == 200
    profile = response.json()["profile"]
    assert profile["status"] == "draft"
    assert profile["sources"] == {}


SOURCED = {"tax_code": [{"url": "https://masothue.com/0101248141"}]}


async def add_kb_chunk(db: AsyncSession, company: Company) -> None:
    db.add(
        KBChunk(
            user_id=OWNER_ID,
            source_type=KBSourceType.COMPANY_PROFILE.value,
            source_id=company.id,
            content="Hồ sơ FPT",
        )
    )
    await db.flush()


async def kb_chunks_of(db: AsyncSession, company: Company) -> int:
    count = await db.scalar(
        select(func.count()).where(
            KBChunk.source_type == KBSourceType.COMPANY_PROFILE.value,
            KBChunk.source_id == company.id,
        )
    )
    return int(count or 0)


async def stored_status(db: AsyncSession, company: Company) -> str | None:
    return await db.scalar(
        select(CompanyProfile.status)
        .where(CompanyProfile.company_id == company.id)
        .execution_options(populate_existing=True)
    )


async def test_db_archive_hides_profile_from_list_and_kb(
    db_session: AsyncSession, owner: User, db_client: httpx.AsyncClient
) -> None:
    fpt = await add_company(db_session, "Tập đoàn FPT")
    await add_company(db_session, "Hanwha Precision Vietnam")
    await add_profile(db_session, fpt, tax_code="0101248141", sources=SOURCED)
    await add_kb_chunk(db_session, fpt)

    response = await db_client.post(f"/api/companies/{fpt.id}/profile/archive")

    assert response.status_code == 200
    assert response.json()["status"] == "archived"
    assert await stored_status(db_session, fpt) == "archived"
    assert await kb_chunks_of(db_session, fpt) == 0
    assert await listed_names(db_client) == ["Hanwha Precision Vietnam"]
    assert await listed_names(db_client, has_profile="false") == ["Hanwha Precision Vietnam"]
    assert await listed_names(db_client, q="fpt") == []
    assert await listed_names(db_client, archived="true") == ["Tập đoàn FPT"]


async def test_db_restore_brings_profile_back_with_inferred_status(
    db_session: AsyncSession, owner: User, db_client: httpx.AsyncClient, indexed: list[uuid.UUID]
) -> None:
    generated = await add_company(db_session, "Alpha")
    edited = await add_company(db_session, "Beta")
    first = await add_profile(
        db_session, generated, tax_code="0101248141", sources=SOURCED, status="archived"
    )
    await add_profile(
        db_session, edited, tax_code="0309999999", size_label="SME", status="archived"
    )

    alpha = await db_client.post(f"/api/companies/{generated.id}/profile/restore")
    beta = await db_client.post(f"/api/companies/{edited.id}/profile/restore")

    assert (alpha.status_code, alpha.json()["status"]) == (200, "generated")
    assert (beta.status_code, beta.json()["status"]) == (200, "verified")
    assert first.id in indexed
    assert await listed_names(db_client) == ["Alpha", "Beta"]


async def test_db_archive_and_restore_reject_wrong_states(
    db_session: AsyncSession, owner: User, db_client: httpx.AsyncClient
) -> None:
    bare = await add_company(db_session, "Alpha")
    drafting = await add_company(db_session, "Beta")
    live = await add_company(db_session, "Gamma")
    await add_profile(db_session, drafting, status="draft")
    await add_profile(db_session, live, tax_code="0101248141", sources=SOURCED)

    assert (await db_client.post(f"/api/companies/{bare.id}/profile/archive")).status_code == 404
    assert (
        await db_client.post(f"/api/companies/{drafting.id}/profile/archive")
    ).status_code == 409
    assert (await db_client.post(f"/api/companies/{live.id}/profile/restore")).status_code == 409


async def test_db_patch_is_refused_while_profile_is_archived(
    db_session: AsyncSession, owner: User, db_client: httpx.AsyncClient, indexed: list[uuid.UUID]
) -> None:
    company = await add_company(db_session, "Alpha")
    await add_profile(db_session, company, tax_code="0101248141", status="archived")

    response = await db_client.patch(
        f"/api/companies/{company.id}/profile", json={"tax_code": "0309999999"}
    )

    assert response.status_code == 409
    assert await stored_status(db_session, company) == "archived"
    assert indexed == []


async def test_db_cancel_endpoints(
    db_session: AsyncSession, owner: User, db_client: httpx.AsyncClient
) -> None:
    company = await add_company(db_session, "Alpha")
    idle = await add_company(db_session, "Beta")
    job_id = await job_repo.create_job(db_session)
    item_id = await job_repo.add_item(db_session, job_id, company.id)
    assert item_id is not None
    await job_repo.claim_item(db_session, item_id)
    await company_repo.ensure_draft_profile(db_session, company.id)

    nothing = await db_client.post(f"/api/companies/{idle.id}/enrich/cancel")
    unknown = await db_client.post(f"/api/companies/{uuid.uuid4()}/enrich/cancel")
    missing_job = await db_client.post(f"/api/companies/enrich-jobs/{uuid.uuid4()}/cancel")
    job = await db_client.post(f"/api/companies/enrich-jobs/{job_id}/cancel")

    assert (nothing.status_code, unknown.status_code, missing_job.status_code) == (409, 404, 404)
    assert job.status_code == 200
    assert (job.json()["cancelled"], job.json()["running"]) == (1, 0)
    assert job.json()["items"][0]["status"] == "cancelled"
    assert await stored_status(db_session, company) is None


async def test_db_detail_lists_same_tax_code_and_same_domain(
    db_session: AsyncSession, owner: User, db_client: httpx.AsyncClient
) -> None:
    group = await add_company(db_session, "Tập đoàn FPT")
    twin = await add_company(db_session, "Công ty Cổ phần FPT Việt Nam")
    japan = await add_company(db_session, "FPTジャパン株式会社")
    stranger = await add_company(db_session, "Hanwha Precision Vietnam")
    await add_profile(db_session, group, tax_code="0101248141", sources=SOURCED)
    await add_profile(db_session, twin, tax_code=" 0101248141 ", sources=SOURCED)
    await add_profile(db_session, japan, tax_code="5010701021223")
    for company, email, website in (
        (group, "khoa@fpt.com", None),
        (japan, "tanaka@fpt.com", "https://fpt.com"),
        (stranger, "kim@gmail.com", None),
    ):
        db_session.add(
            BusinessCard(
                user_id=OWNER_ID,
                image_path=f"test/{uuid.uuid4().hex[:8]}.jpg",
                image_hash=uuid.uuid4().hex * 2,
                status="confirmed",
                company_id=company.id,
                email=email,
                website=website,
            )
        )
    await db_session.flush()

    body = (await db_client.get(f"/api/companies/{group.id}")).json()

    assert [ref["display_name"] for ref in body["same_tax_code"]] == [
        "Công ty Cổ phần FPT Việt Nam"
    ]
    assert body["same_domain"] == [
        {"id": str(japan.id), "display_name": "FPTジャパン株式会社", "domains": ["fpt.com"]}
    ]


async def test_db_cancel_clears_a_draft_left_after_its_item_expired(
    db_session: AsyncSession, owner: User, db_client: httpx.AsyncClient
) -> None:
    company = await add_company(db_session, "Alpha")
    await company_repo.ensure_draft_profile(db_session, company.id)

    response = await db_client.post(f"/api/companies/{company.id}/enrich/cancel")

    assert (response.status_code, response.json()) == (200, {"cancelled": 0})
    assert await stored_status(db_session, company) is None
