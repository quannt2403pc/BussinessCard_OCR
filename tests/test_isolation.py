import json
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.card import BusinessCard, CardStatus
from app.models.company import Company, CompanyProfile, EnrichJobItem
from app.models.kb import KBSourceType
from app.models.user import User
from app.prompts import assistant as prompt
from app.repositories import kb as kb_repo
from app.routers import kb as kb_router
from app.services import enrich_jobs, retriever
from app.services.company_matching import upsert_company
from app.services.normalize_company import normalize_company_name
from tests.conftest import CliProxyStub

ClientFactory = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]

NOW = datetime(2026, 9, 22, 9, 0, tzinfo=UTC)
SHARED_TAX_CODE = "0300000001"
SHARED_DOMAIN = "shared-group.vn"


@dataclass(frozen=True)
class Tenant:
    user: User
    card: BusinessCard
    company: Company
    profile: CompanyProfile


async def seed_tenant(db: AsyncSession, user: User, company_name: str, person: str) -> Tenant:
    company = Company(
        id=uuid.uuid4(),
        user_id=user.id,
        display_name=company_name,
        name_normalized=normalize_company_name(company_name),
        aliases=[company_name],
    )
    db.add(company)
    await db.flush()
    profile = CompanyProfile(
        id=uuid.uuid4(),
        user_id=user.id,
        company_id=company.id,
        legal_name=company_name,
        tax_code=SHARED_TAX_CODE,
        industry=["Thép"],
        products=[f"Sản phẩm riêng của {company_name}"],
        address="Hà Nội",
        status="generated",
        generated_at=NOW,
        sources={"tax_code": [{"url": f"https://masothue.example/{SHARED_TAX_CODE}"}]},
    )
    card = BusinessCard(
        id=uuid.uuid4(),
        user_id=user.id,
        image_path=f"isolation/{uuid.uuid4().hex[:8]}.jpg",
        image_hash=uuid.uuid4().hex * 2,
        uploaded_at=NOW,
        status=CardStatus.CONFIRMED.value,
        company_id=company.id,
        full_name=person,
        job_title="Giám đốc",
        company_name_raw=company_name,
        email=f"contact@{SHARED_DOMAIN}",
        website=f"https://{SHARED_DOMAIN}",
        language_detected="vi",
    )
    db.add_all([profile, card])
    await db.flush()
    await kb_router.reindex(db, user)
    return Tenant(user, card, company, profile)


@pytest.fixture
async def tenants(
    db_session: AsyncSession, user_a: User, user_b: User, embedder: object
) -> AsyncIterator[tuple[Tenant, Tenant]]:
    a = await seed_tenant(db_session, user_a, "Công ty CP Thép An Phát", "Nguyễn Văn An")
    b = await seed_tenant(db_session, user_b, "Công ty TNHH Minh Khuê Zeta", "Lê Hoàng Yến")
    yield a, b


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    calls: list[uuid.UUID] = []
    monkeypatch.setattr(enrich_jobs, "start", calls.append)
    return calls


def leaked(text: str, other: Tenant) -> list[str]:
    markers = [
        str(other.card.id),
        str(other.company.id),
        other.company.display_name,
        other.card.full_name or "",
    ]
    return [marker for marker in markers if marker and marker in text]


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/cards/{card}", None),
        ("GET", "/api/cards/{card}/image", None),
        ("PATCH", "/api/cards/{card}", {"full_name": "Bị sửa"}),
        ("POST", "/api/cards/{card}/confirm", None),
        ("DELETE", "/api/cards/{card}", None),
    ],
)
async def test_other_users_card_is_404(
    db_session: AsyncSession,
    app_client: ClientFactory,
    tenants: tuple[Tenant, Tenant],
    method: str,
    path: str,
    body: dict[str, str] | None,
) -> None:
    a, b = tenants
    async with app_client(a.user) as http:
        response = await http.request(method, path.format(card=b.card.id), json=body)
    assert response.status_code == 404
    assert leaked(response.text, b) == []
    await db_session.refresh(b.card)
    assert b.card.full_name == "Lê Hoàng Yến"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/companies/{company}", None),
        ("GET", "/api/companies/{company}/contacts", None),
        ("POST", "/api/companies/{company}/enrich", None),
        ("POST", "/api/companies/{company}/enrich/cancel", None),
        ("POST", "/api/companies/{company}/profile/archive", None),
        ("POST", "/api/companies/{company}/profile/restore", None),
        ("PATCH", "/api/companies/{company}/profile", {"tax_code": "0399999999"}),
    ],
)
async def test_other_users_company_is_404(
    db_session: AsyncSession,
    app_client: ClientFactory,
    tenants: tuple[Tenant, Tenant],
    started: list[uuid.UUID],
    method: str,
    path: str,
    body: dict[str, str] | None,
) -> None:
    a, b = tenants
    async with app_client(a.user) as http:
        response = await http.request(method, path.format(company=b.company.id), json=body)
    assert response.status_code == 404
    assert leaked(response.text, b) == []
    assert started == []
    await db_session.refresh(b.profile)
    assert (b.profile.status, b.profile.tax_code) == ("generated", SHARED_TAX_CODE)
    items = await db_session.scalar(
        select(func.count())
        .select_from(EnrichJobItem)
        .where(EnrichJobItem.company_id == b.company.id)
    )
    assert items == 0


async def test_enrich_batch_rejects_other_users_company(
    db_session: AsyncSession,
    app_client: ClientFactory,
    tenants: tuple[Tenant, Tenant],
    started: list[uuid.UUID],
) -> None:
    a, b = tenants
    async with app_client(a.user) as http:
        response = await http.post(
            "/api/companies/enrich-batch",
            json={"company_ids": [str(a.company.id), str(b.company.id)]},
        )
    assert response.status_code == 404
    assert str(b.company.id) in response.json()["detail"]
    assert b.company.display_name not in response.text
    assert started == []
    assert await db_session.scalar(select(func.count()).select_from(EnrichJobItem)) == 0


async def test_other_users_enrich_job_is_404(
    db_session: AsyncSession, app_client: ClientFactory, tenants: tuple[Tenant, Tenant]
) -> None:
    a, b = tenants
    created = await enrich_jobs.create_job(db_session, [b.company.id])
    await db_session.flush()

    async with app_client(a.user) as http:
        seen = await http.get(f"/api/companies/enrich-jobs/{created.job_id}")
        cancelled = await http.post(f"/api/companies/enrich-jobs/{created.job_id}/cancel")
    assert (seen.status_code, cancelled.status_code) == (404, 404)
    assert leaked(seen.text + cancelled.text, b) == []

    async with app_client(b.user) as http:
        own = await http.get(f"/api/companies/enrich-jobs/{created.job_id}")
    assert own.status_code == 200
    assert [item["status"] for item in own.json()["items"]] == ["pending"]


@pytest.mark.parametrize(
    "path",
    [
        "/api/cards",
        "/api/cards?q=Hoàng Yến",
        "/api/companies",
        "/api/companies?q=Minh Khuê",
        "/api/companies?archived=true",
        "/api/companies?has_profile=true",
    ],
)
async def test_lists_and_search_show_only_own_data(
    app_client: ClientFactory, tenants: tuple[Tenant, Tenant], path: str
) -> None:
    a, b = tenants
    async with app_client(a.user) as http:
        response = await http.get(path)
    assert response.status_code == 200
    assert leaked(response.text, b) == []


async def test_own_list_still_shows_own_data(
    app_client: ClientFactory, tenants: tuple[Tenant, Tenant]
) -> None:
    a, _ = tenants
    async with app_client(a.user) as http:
        companies = (await http.get("/api/companies")).json()
        cards = (await http.get("/api/cards")).json()
    assert [item["id"] for item in companies["items"]] == [str(a.company.id)]
    assert companies["total"] == 1
    assert str(a.card.id) in json.dumps(cards)


async def test_related_companies_never_cross_users(
    app_client: ClientFactory, tenants: tuple[Tenant, Tenant]
) -> None:
    a, b = tenants
    async with app_client(a.user) as http:
        detail = (await http.get(f"/api/companies/{a.company.id}")).json()
    assert detail["same_tax_code"] == []
    assert detail["same_domain"] == []
    assert [contact["id"] for contact in detail["contacts"]] == [str(a.card.id)]
    assert leaked(json.dumps(detail, ensure_ascii=False), b) == []


async def test_stats_count_only_own_data(
    db_session: AsyncSession, app_client: ClientFactory, tenants: tuple[Tenant, Tenant]
) -> None:
    a, _ = tenants
    extra = Company(
        id=uuid.uuid4(), user_id=a.user.id, display_name="Công ty phụ", name_normalized="phu"
    )
    db_session.add(extra)
    await db_session.flush()
    async with app_client(a.user) as http:
        stats = (await http.get("/api/stats")).json()
    assert (
        stats["total_cards"],
        stats["confirmed_cards"],
        stats["total_companies"],
        stats["total_profiles"],
    ) == (1, 1, 2, 1)


@pytest.mark.parametrize(
    "path",
    [
        "/api/export/cards.csv",
        "/api/export/cards.json",
        "/api/export/companies.csv",
        "/api/export/companies.json",
    ],
)
async def test_exports_contain_only_own_data(
    db_session: AsyncSession,
    app_client: ClientFactory,
    tenants: tuple[Tenant, Tenant],
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    from app.routers import export

    class SameSession:
        def __call__(self) -> "SameSession":
            return self

        async def __aenter__(self) -> AsyncSession:
            return db_session

        async def __aexit__(self, *exc: object) -> bool:
            return False

    monkeypatch.setattr(export, "SessionLocal", SameSession())
    a, b = tenants
    async with app_client(a.user) as http:
        response = await http.get(path)
    assert response.status_code == 200
    assert leaked(response.text, b) == []
    assert str(a.card.id) in response.text or str(a.company.id) in response.text
    if path.endswith(".json"):
        assert response.json()["total"] == 1


async def own_chunk(db: AsyncSession, tenant: Tenant, source_type: str) -> str:
    rows = await kb_repo.search_similar(
        db,
        [0.0] * settings.embedding_dim,
        user_id=tenant.user.id,
        top_k=1,
        source_type=source_type,
    )
    return rows[0][0].content


@pytest.mark.parametrize(
    "source_type", [KBSourceType.CARD.value, KBSourceType.COMPANY_PROFILE.value]
)
async def test_retrieval_never_returns_other_users_chunks(
    db_session: AsyncSession, tenants: tuple[Tenant, Tenant], source_type: str
) -> None:
    a, b = tenants
    question = await own_chunk(db_session, b, source_type)

    hits_a = await retriever.search(db_session, question, user_id=a.user.id)
    hits_b = await retriever.search(db_session, question, user_id=b.user.id)

    other_sources = {b.card.id, b.company.id}
    assert [hit for hit in hits_a if hit.source_id in other_sources] == []
    assert any(hit.source_id in other_sources for hit in hits_b)


ONLY_B_QUESTION = "Lê Hoàng Yến bên Minh Khuê là ai?"


def texts(node: object) -> list[str]:
    if isinstance(node, dict):
        found: list[str] = []
        for key, value in node.items():
            if key == "text" and isinstance(value, str):
                found.append(value)
            else:
                found.extend(texts(value))
        return found
    if isinstance(node, list):
        return [text for item in node for text in texts(item)]
    return []


def model_calls(cliproxy: CliProxyStub) -> list[str]:
    return [
        "\n".join(texts(json.loads(call.request.content)))
        for call in cliproxy.calls
        if "generateContent" in str(call.request.url)
    ]


async def test_assistant_has_no_information_about_other_users_company(
    app_client: ClientFactory, tenants: tuple[Tenant, Tenant], cliproxy: CliProxyStub
) -> None:
    a, b = tenants
    cliproxy.reply(f"{b.card.full_name} là giám đốc {b.company.display_name} [1].")

    async with app_client(a.user) as http:
        response = await http.post("/api/chat", json={"question": ONLY_B_QUESTION})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == prompt.NO_ANSWER_TEXT
    assert body["citations"] == []
    assert leaked(json.dumps(body, ensure_ascii=False), b) == []
    assert model_calls(cliproxy) == []


async def test_assistant_answers_the_owner_the_same_question(
    app_client: ClientFactory, tenants: tuple[Tenant, Tenant], cliproxy: CliProxyStub
) -> None:
    _, b = tenants
    cliproxy.reply(f"{b.card.full_name} là giám đốc {b.company.display_name} [1].")

    async with app_client(b.user) as http:
        body = (await http.post("/api/chat", json={"question": ONLY_B_QUESTION})).json()

    assert body["answer"] != prompt.NO_ANSWER_TEXT
    assert {citation["source_id"] for citation in body["citations"]} <= {
        str(b.card.id),
        str(b.company.id),
    }
    assert body["citations"]


async def test_prompt_never_carries_other_users_chunks(
    app_client: ClientFactory,
    db_session: AsyncSession,
    tenants: tuple[Tenant, Tenant],
    cliproxy: CliProxyStub,
) -> None:
    a, b = tenants
    question = await own_chunk(db_session, b, KBSourceType.COMPANY_PROFILE.value)
    cliproxy.reply("Trả lời [1].")

    async with app_client(a.user) as http:
        body = (await http.post("/api/chat", json={"question": question})).json()

    sent = model_calls(cliproxy)
    assert sent
    context = "".join(sent).split("CÂU HỎI:")[0]
    assert "NGỮ CẢNH" in context
    assert leaked(context, b) == []
    assert all(
        citation["source_id"] not in {str(b.card.id), str(b.company.id)}
        for citation in body["citations"]
    )


async def test_other_users_chat_session_is_404(
    app_client: ClientFactory,
    db_session: AsyncSession,
    tenants: tuple[Tenant, Tenant],
    cliproxy: CliProxyStub,
) -> None:
    a, b = tenants
    question = await own_chunk(db_session, b, KBSourceType.CARD.value)
    cliproxy.reply("Trả lời [1].")
    async with app_client(b.user) as http:
        session_id = (await http.post("/api/chat", json={"question": question})).json()[
            "session_id"
        ]

    async with app_client(a.user) as http:
        history = await http.get(f"/api/chat/{session_id}")
        follow_up = await http.post(
            "/api/chat", json={"question": "Tiếp tục", "session_id": session_id}
        )
    assert (history.status_code, follow_up.status_code) == (404, 404)
    assert question not in history.text + follow_up.text


async def test_same_company_name_creates_separate_companies(
    db_session: AsyncSession, user_a: User, user_b: User
) -> None:
    first = await upsert_company(db_session, "Công ty CP Sữa Việt Nam", user_id=user_a.id)
    second = await upsert_company(db_session, "CÔNG TY CỔ PHẦN SỮA VIỆT NAM", user_id=user_b.id)
    again = await upsert_company(db_session, "Công ty CP Sữa Việt Nam", user_id=user_a.id)
    assert first != second
    assert again == first
