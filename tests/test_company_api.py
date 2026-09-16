import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.db import get_db
from app.core.templates import BASE_DIR
from app.models.card import BusinessCard
from app.models.company import Company, CompanyProfile
from app.repositories import company as company_repo
from app.repositories.company import CompanyRow
from app.routers import companies
from app.routers.companies import page_count, to_list_item

COMPANY_ID = uuid.uuid4()


def make_company(**overrides: Any) -> Company:
    values: dict[str, Any] = {
        "id": COMPANY_ID,
        "display_name": "Công ty TNHH ABC",
        "name_normalized": "abc",
        "aliases": None,
    }
    values.update(overrides)
    return Company(**values)


def make_card() -> BusinessCard:
    return BusinessCard(
        id=uuid.uuid4(),
        status="confirmed",
        full_name="Nguyễn Văn A",
        company_id=COMPANY_ID,
        image_path="ab/abc.jpg",
        uploaded_at=datetime(2026, 9, 15, 8, 0),
    )


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(companies.router)
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    async def no_db() -> AsyncIterator[None]:
        yield None

    app.dependency_overrides[get_db] = no_db
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
    assert received == {"q": "abc", "has_profile": False, "page": 3, "size": 20}
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
    assert received == {"q": None, "has_profile": None, "page": 1, "size": 20}
    assert response.json() == {"items": [], "total": 0, "page": 1, "size": 20, "pages": 1}


@pytest.mark.parametrize("params", [{"size": 101}, {"size": 0}, {"page": 0}])
async def test_list_rejects_bad_paging(client: httpx.AsyncClient, params: dict[str, int]) -> None:
    assert (await client.get("/api/companies", params=params)).status_code == 422


async def test_detail_with_profile_and_contacts(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_row(db: Any, company_id: uuid.UUID) -> CompanyRow:
        return CompanyRow(make_company(aliases=None), 1, "generated", datetime(2026, 9, 15, 8, 0))

    async def fake_profile(db: Any, company_id: uuid.UUID) -> CompanyProfile:
        return CompanyProfile(
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
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
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
