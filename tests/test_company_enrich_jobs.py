import asyncio
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import current_user
from app.models.company import Company, CompanyProfile, EnrichJob, EnrichJobItem
from app.models.user import User
from app.repositories import company as company_repo
from app.repositories import enrich_job as job_repo
from app.routers import companies
from app.schemas.company import CompanyProfileOut, ProfileStatus
from app.schemas.enrich_job import JobItemStatus
from app.services import enrich_jobs, kb
from app.services.cliproxy_client import CliProxyUnavailableError
from app.services.embeddings import EmbedderUnavailableError
from app.services.enrich_jobs import (
    BACKOFF_SECONDS,
    MAX_ATTEMPTS,
    STALE_AFTER,
    STALE_MESSAGE,
    CompanyGoneError,
    JobCreated,
    NoSourcedDataError,
    describe_failure,
    run_with_retry,
)
from app.services.enrichment import EnrichmentParseError
from app.services.llm import (
    LLMBlockedError,
    LLMError,
    LLMInvalidModelError,
    LLMNotConnectedError,
)
from app.services.normalize_company import normalize_company_name
from tests.conftest import make_user

#: Chủ sở hữu của mọi bản ghi trong file này (task 12.8). `0005` đặt `user_id` là NOT NULL
#: trên cả 6 bảng dữ liệu, nên object ORM nào ghi xuống DB cũng phải có nó. File này không
#: kiểm việc tách dữ liệu (đó là 12.6/12.7 của T) nên một chủ sở hữu duy nhất là đủ.
OWNER_ID = uuid.uuid4()
OWNER = User(id=OWNER_ID, email="owner-enrich@example.com", password_hash="!")


@pytest.fixture
async def owner(db_session: AsyncSession) -> User:
    """Hàng `users` cho `OWNER_ID` (task 12.8).

    Khoá ngoại `business_cards.user_id` / `companies.user_id` (revision `0005`) đòi chủ sở hữu
    tồn tại thật, nên test nào **ghi xuống DB** cũng phải dựng hàng này trước. Test chạy trên
    `FakeSession` thì không cần — vì thế fixture này không autouse.
    """
    return await make_user(
        db_session,
        "owner-company_enrich_jobs@example.com",
        "Chủ sở hữu dữ liệu test",
        user_id=OWNER_ID,
    )


JOB_ID = uuid.uuid4()
COMPANY_ID = uuid.uuid4()


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
def started(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    calls: list[uuid.UUID] = []
    monkeypatch.setattr(enrich_jobs, "start", calls.append)
    return calls


@pytest.fixture
async def client(session: FakeSession) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(companies.router)

    async def fake_db() -> AsyncIterator[FakeSession]:
        yield session

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[current_user] = lambda: OWNER
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


def proxy_error(status_code: int | None) -> LLMError:
    error = LLMError("Không gọi được model qua CLIProxy: Name or service not known")
    error.__cause__ = CliProxyUnavailableError("down", status_code=status_code)
    return error


@pytest.mark.parametrize(
    ("exc", "retry", "abort_job", "fragment"),
    [
        (LLMNotConnectedError("x"), False, True, "Chưa kết nối CLIProxy"),
        (LLMInvalidModelError("gemini-x"), False, True, "LLM_MODEL"),
        (LLMBlockedError("SAFETY"), False, False, "từ chối"),
        (NoSourcedDataError("ABC"), False, False, "nguồn kiểm chứng"),
        (CompanyGoneError("id"), False, False, "bị xoá"),
        (EnrichmentParseError("bad json"), True, False, "không đọc được"),
        (LLMError("ReadTimeout"), True, False, "ReadTimeout"),
        (proxy_error(503), True, False, "Không gọi được LLM"),
        (proxy_error(None), False, False, "docker compose up -d cliproxy"),
        (ValueError("boom"), False, False, "ngoài dự kiến"),
    ],
)
def test_describe_failure(exc: Exception, retry: bool, abort_job: bool, fragment: str) -> None:
    failure = describe_failure(exc)
    assert (failure.retry, failure.abort_job) == (retry, abort_job)
    assert fragment in failure.message


def test_unexpected_failure_message_hides_details() -> None:
    exc = RuntimeError("INSERT INTO company_profiles ... [parameters: {'tax_code': '0300588569'}]")
    message = describe_failure(exc).message
    assert message == "Lỗi ngoài dự kiến (RuntimeError)."
    assert "INSERT" not in message


class Recorder:
    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.attempts: list[int] = []
        self.sleeps: list[float] = []

    async def operation(self, attempt: int) -> int:
        self.attempts.append(attempt)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        assert isinstance(result, int)
        return result

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)


async def test_retry_success_first_time() -> None:
    recorder = Recorder(11)
    outcome = await run_with_retry(recorder.operation, sleep=recorder.sleep)
    assert (outcome.attempts, outcome.sourced_fields, outcome.failure) == (1, 11, None)
    assert recorder.sleeps == []


async def test_retry_then_success_uses_backoff() -> None:
    recorder = Recorder(LLMError("timeout"), 7)
    outcome = await run_with_retry(recorder.operation, sleep=recorder.sleep)
    assert (outcome.attempts, outcome.sourced_fields, outcome.failure) == (2, 7, None)
    assert recorder.sleeps == [BACKOFF_SECONDS[0]]


async def test_retry_gives_up_after_max_attempts() -> None:
    recorder = Recorder(*[LLMError("timeout")] * MAX_ATTEMPTS)
    outcome = await run_with_retry(recorder.operation, sleep=recorder.sleep)
    assert outcome.attempts == MAX_ATTEMPTS
    assert outcome.failure is not None
    assert outcome.failure.message.startswith(f"Thử {MAX_ATTEMPTS} lượt vẫn hỏng")
    assert recorder.attempts == list(range(1, MAX_ATTEMPTS + 1))
    assert recorder.sleeps == list(BACKOFF_SECONDS)


async def test_unreachable_proxy_is_not_retried() -> None:
    recorder = Recorder(proxy_error(None), 7)
    outcome = await run_with_retry(recorder.operation, sleep=recorder.sleep)
    assert outcome.attempts == 1
    assert outcome.failure is not None
    assert "docker compose up -d cliproxy" in outcome.failure.message
    assert recorder.sleeps == []


async def test_retry_stops_on_final_errors() -> None:
    recorder = Recorder(NoSourcedDataError("ABC"), 5)
    outcome = await run_with_retry(recorder.operation, sleep=recorder.sleep)
    assert outcome.attempts == 1
    assert outcome.failure is not None and not outcome.failure.retry
    assert recorder.sleeps == []


async def test_retry_reports_abort_when_not_connected() -> None:
    recorder = Recorder(LLMNotConnectedError("no auth"))
    outcome = await run_with_retry(recorder.operation, sleep=recorder.sleep)
    assert outcome.failure is not None and outcome.failure.abort_job


async def test_enrich_batch_accepts_and_starts(
    client: httpx.AsyncClient,
    session: FakeSession,
    started: list[uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = uuid.uuid4()

    async def existing(db: Any, ids: list[uuid.UUID], **_: Any) -> set[uuid.UUID]:
        return set(ids)

    async def fake_create(db: Any, ids: list[uuid.UUID], **_: Any) -> JobCreated:
        return JobCreated(job_id=JOB_ID, accepted=1, skipped=[other])

    monkeypatch.setattr(job_repo, "existing_company_ids", existing)
    monkeypatch.setattr(enrich_jobs, "create_job", fake_create)

    response = await client.post(
        "/api/companies/enrich-batch", json={"company_ids": [str(COMPANY_ID), str(other)]}
    )

    assert response.status_code == 202
    assert response.json() == {"job_id": str(JOB_ID), "accepted": 1, "skipped": 1}
    assert session.commits == 1
    assert started == [JOB_ID]


async def test_enrich_batch_all_skipped_does_not_start(
    client: httpx.AsyncClient, started: list[uuid.UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def existing(db: Any, ids: list[uuid.UUID], **_: Any) -> set[uuid.UUID]:
        return set(ids)

    async def fake_create(db: Any, ids: list[uuid.UUID], **_: Any) -> JobCreated:
        return JobCreated(job_id=JOB_ID, accepted=0, skipped=list(ids))

    monkeypatch.setattr(job_repo, "existing_company_ids", existing)
    monkeypatch.setattr(enrich_jobs, "create_job", fake_create)

    response = await client.post(
        "/api/companies/enrich-batch", json={"company_ids": [str(COMPANY_ID)]}
    )
    assert response.json() == {"job_id": str(JOB_ID), "accepted": 0, "skipped": 1}
    assert started == []


async def test_enrich_batch_unknown_company(
    client: httpx.AsyncClient, started: list[uuid.UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def existing(db: Any, ids: list[uuid.UUID], **_: Any) -> set[uuid.UUID]:
        return set()

    monkeypatch.setattr(job_repo, "existing_company_ids", existing)
    response = await client.post(
        "/api/companies/enrich-batch", json={"company_ids": [str(COMPANY_ID)]}
    )
    assert response.status_code == 404
    assert str(COMPANY_ID) in response.json()["detail"]
    assert started == []


@pytest.mark.parametrize(
    "body",
    [{"company_ids": []}, {"company_ids": ["abc"]}, {}, {"company_ids": [str(uuid.uuid4())] * 51}],
)
async def test_enrich_batch_validates_body(client: httpx.AsyncClient, body: dict[str, Any]) -> None:
    assert (await client.post("/api/companies/enrich-batch", json=body)).status_code == 422


async def test_enrich_one_starts_job(
    client: httpx.AsyncClient,
    session: FakeSession,
    started: list[uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_company(db: Any, company_id: uuid.UUID, **_: Any) -> Company:
        return Company(user_id=OWNER_ID, id=company_id, display_name="ABC", name_normalized="abc")

    async def fake_create(db: Any, ids: list[uuid.UUID], **_: Any) -> JobCreated:
        assert ids == [COMPANY_ID]
        return JobCreated(job_id=JOB_ID, accepted=1, skipped=[])

    monkeypatch.setattr(company_repo, "get_owned_company", fake_company)
    monkeypatch.setattr(enrich_jobs, "create_job", fake_create)

    response = await client.post(f"/api/companies/{COMPANY_ID}/enrich")
    assert response.status_code == 202
    assert response.json() == {"job_id": str(JOB_ID)}
    assert (session.commits, session.rollbacks) == (1, 0)
    assert started == [JOB_ID]


async def test_enrich_one_conflict_returns_existing_job(
    client: httpx.AsyncClient,
    session: FakeSession,
    started: list[uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    running_job = uuid.uuid4()

    async def fake_company(db: Any, company_id: uuid.UUID, **_: Any) -> Company:
        return Company(user_id=OWNER_ID, id=company_id, display_name="ABC", name_normalized="abc")

    async def fake_create(db: Any, ids: list[uuid.UUID], **_: Any) -> JobCreated:
        return JobCreated(job_id=JOB_ID, accepted=0, skipped=list(ids))

    async def fake_active(db: Any, company_id: uuid.UUID, **_: Any) -> uuid.UUID:
        return running_job

    monkeypatch.setattr(company_repo, "get_owned_company", fake_company)
    monkeypatch.setattr(enrich_jobs, "create_job", fake_create)
    monkeypatch.setattr(job_repo, "active_job_id", fake_active)

    response = await client.post(f"/api/companies/{COMPANY_ID}/enrich")
    assert response.status_code == 409
    assert response.json()["existing_id"] == str(running_job)
    assert (session.commits, session.rollbacks) == (0, 1)
    assert started == []


async def test_enrich_one_unknown_company(
    client: httpx.AsyncClient, started: list[uuid.UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def nothing(db: Any, company_id: uuid.UUID, **_: Any) -> None:
        return None

    monkeypatch.setattr(company_repo, "get_owned_company", nothing)
    response = await client.post(f"/api/companies/{uuid.uuid4()}/enrich")
    assert response.status_code == 404
    assert started == []


async def test_job_progress(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    job = EnrichJob(id=JOB_ID, created_at=datetime(2026, 9, 15, 8, 0), finished_at=None)

    def item(status: str, **extra: Any) -> EnrichJobItem:
        return EnrichJobItem(
            id=uuid.uuid4(), job_id=JOB_ID, company_id=uuid.uuid4(), status=status, **extra
        )

    items = [
        (item("done", attempts=1, sourced_fields=11), "ABC"),
        (item("running", attempts=2, started_at=datetime(2026, 9, 15, 8, 1)), "FPT"),
        (item("error", attempts=1, error="Không tìm thấy thông tin"), "KFT"),
        (item("pending", attempts=0), "XYZ"),
    ]

    async def fake_job(db: Any, job_id: uuid.UUID, **_: Any) -> EnrichJob:
        return job

    async def fake_items(db: Any, job_id: uuid.UUID, **_: Any) -> list[tuple[EnrichJobItem, str]]:
        return items

    monkeypatch.setattr(job_repo, "get_job", fake_job)
    monkeypatch.setattr(job_repo, "list_items", fake_items)

    body = (await client.get(f"/api/companies/enrich-jobs/{JOB_ID}")).json()
    assert (body["total"], body["done"], body["failed"], body["running"], body["finished"]) == (
        4,
        1,
        1,
        1,
        False,
    )
    assert body["created_at"] == "2026-09-15T08:00:00Z"
    assert [(entry["display_name"], entry["status"]) for entry in body["items"]] == [
        ("ABC", "done"),
        ("FPT", "running"),
        ("KFT", "error"),
        ("XYZ", "pending"),
    ]
    assert body["items"][0]["sourced_fields"] == 11
    assert body["items"][1]["started_at"] == "2026-09-15T08:01:00Z"
    assert body["items"][2]["error"] == "Không tìm thấy thông tin"


async def test_job_unknown(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def nothing(db: Any, job_id: uuid.UUID, **_: Any) -> None:
        return None

    monkeypatch.setattr(job_repo, "get_job", nothing)
    response = await client.get(f"/api/companies/enrich-jobs/{uuid.uuid4()}")
    assert response.status_code == 404


class FakeDb:
    def __init__(self, profile: Any) -> None:
        self.profile = profile
        self.commits = 0

    async def get(self, model: Any, pk: Any) -> Any:
        return self.profile

    async def commit(self) -> None:
        self.commits += 1


class FakeSessionFactory:
    def __init__(self, profile: Any) -> None:
        self.db = FakeDb(profile)

    def __call__(self) -> "FakeSessionFactory":
        return self

    async def __aenter__(self) -> FakeDb:
        return self.db

    async def __aexit__(self, *exc: Any) -> bool:
        return False


async def test_index_profile_sends_profile_to_kb(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = object()
    received: list[Any] = []

    async def fake_ingest(db: Any, given: Any) -> int:
        received.append(given)
        return 3

    monkeypatch.setattr(enrich_jobs, "SessionLocal", FakeSessionFactory(profile))
    monkeypatch.setattr(kb, "ingest_company_profile", fake_ingest)

    await enrich_jobs.index_profile(uuid.uuid4())
    assert received == [profile]


async def test_index_profile_skips_missing_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[Any] = []

    async def fake_ingest(db: Any, given: Any) -> int:
        called.append(given)
        return 1

    monkeypatch.setattr(enrich_jobs, "SessionLocal", FakeSessionFactory(None))
    monkeypatch.setattr(kb, "ingest_company_profile", fake_ingest)

    await enrich_jobs.index_profile(uuid.uuid4())
    assert called == []


async def test_index_profile_swallows_embedder_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(db: Any, given: Any) -> int:
        raise EmbedderUnavailableError("embedder chet")

    monkeypatch.setattr(enrich_jobs, "SessionLocal", FakeSessionFactory(object()))
    monkeypatch.setattr(kb, "ingest_company_profile", broken)

    # Khong duoc nem ra ngoai: ho so da luu roi, KB dung lai duoc bang POST /api/kb/reindex.
    # Nem ra thi run_with_retry se goi lai LLM them 2 luot cho mot su co khong lien quan.
    await enrich_jobs.index_profile(uuid.uuid4())


async def test_enrich_and_save_indexes_after_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    company_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    order: list[str] = []

    class Saved:
        id = profile_id

    class Profile:
        llm_model = "gemini-3-flash"
        generated_at = None

        def sourced_field_count(self) -> int:
            return 7

    async def fake_get_company(db: Any, cid: uuid.UUID) -> Company:
        return Company(user_id=OWNER_ID, id=cid, display_name="ABC", name_normalized="abc")

    async def fake_contacts(db: Any, cid: uuid.UUID) -> list[Any]:
        return []

    async def fake_draft(db: Any, cid: uuid.UUID) -> None:
        return None

    models: list[str] = []

    async def fake_model(db: Any, user_id: uuid.UUID, feature: str) -> str:
        assert user_id == OWNER_ID
        # Luồng enrich phải xin đúng khoá `enrich` — xin nhầm `ocr` thì job chạy bằng model người
        # dùng chọn để quét ảnh, mà model đó có thể không tra cứu được Internet (ADR mục 3).
        assert feature == "enrich"
        return "uowner/gemini-3-flash"

    async def fake_enrich(name: str, hints: Any, *, model: str) -> Profile:
        models.append(model)
        return Profile()

    async def fake_save(db: Any, cid: uuid.UUID, profile: Any, **kwargs: Any) -> Saved:
        order.append("save")
        return Saved()

    async def fake_index(pid: uuid.UUID) -> None:
        order.append(f"index:{pid}")

    monkeypatch.setattr(enrich_jobs, "SessionLocal", FakeSessionFactory(None))
    monkeypatch.setattr(company_repo, "get_company", fake_get_company)
    monkeypatch.setattr(company_repo, "list_contacts", fake_contacts)
    monkeypatch.setattr(company_repo, "ensure_draft_profile", fake_draft)
    monkeypatch.setattr(company_repo, "save_profile", fake_save)
    monkeypatch.setattr(enrich_jobs, "enrich_company", fake_enrich)
    monkeypatch.setattr(enrich_jobs, "build_hints", lambda cards: {})
    monkeypatch.setattr(enrich_jobs, "index_profile", fake_index)
    monkeypatch.setattr(enrich_jobs.user_credentials, "model_for_user_id", fake_model)

    assert await enrich_jobs.enrich_and_save(company_id) == 7
    assert order == ["save", f"index:{profile_id}"]
    assert models == ["uowner/gemini-3-flash"]


async def add_company(db: AsyncSession, display_name: str) -> Company:
    company = Company(
        user_id=OWNER_ID,
        display_name=display_name,
        name_normalized=normalize_company_name(display_name),
    )
    db.add(company)
    await db.flush()
    return company


async def item_status(db: AsyncSession, item_id: uuid.UUID) -> tuple[str, str | None]:
    row = (
        await db.execute(
            select(EnrichJobItem.status, EnrichJobItem.error)
            .where(EnrichJobItem.id == item_id)
            .execution_options(populate_existing=True)
        )
    ).one()
    return row.status, row.error


async def test_db_second_active_item_for_same_company_is_rejected(
    db_session: AsyncSession,
    owner: User,
) -> None:
    company = await add_company(db_session, "Công ty TNHH Logistics Đại Việt")
    first_job = await job_repo.create_job(db_session)
    second_job = await job_repo.create_job(db_session)

    first = await job_repo.add_item(db_session, first_job, company.id)
    second = await job_repo.add_item(db_session, second_job, company.id)

    assert first is not None
    assert second is None
    assert await job_repo.active_job_id(db_session, company.id) == first_job


async def test_db_running_item_still_blocks_a_new_one(
    db_session: AsyncSession, owner: User
) -> None:
    company = await add_company(db_session, "Hanwha Precision Vietnam")
    job_id = await job_repo.create_job(db_session)
    item_id = await job_repo.add_item(db_session, job_id, company.id)
    assert item_id is not None

    assert await job_repo.claim_item(db_session, item_id) == company.id
    assert await job_repo.claim_item(db_session, item_id) is None
    assert (
        await job_repo.add_item(db_session, await job_repo.create_job(db_session), company.id)
        is None
    )


@pytest.mark.parametrize("final", [JobItemStatus.DONE, JobItemStatus.ERROR])
async def test_db_finished_item_frees_the_company(
    db_session: AsyncSession, owner: User, final: JobItemStatus
) -> None:
    company = await add_company(db_session, "Công ty CP Sữa Mộc Châu")
    job_id = await job_repo.create_job(db_session)
    item_id = await job_repo.add_item(db_session, job_id, company.id)
    assert item_id is not None
    await job_repo.finish_item(db_session, item_id, status=final)

    again = await job_repo.add_item(db_session, await job_repo.create_job(db_session), company.id)

    assert again is not None
    assert await job_repo.active_job_id(db_session, company.id) is not None


async def test_db_stale_items_expire_and_fresh_ones_stay(
    db_session: AsyncSession, owner: User
) -> None:
    stale_company = await add_company(db_session, "Alpha Stale")
    fresh_company = await add_company(db_session, "Beta Fresh")
    job_id = await job_repo.create_job(db_session)
    stale = await job_repo.add_item(db_session, job_id, stale_company.id)
    fresh = await job_repo.add_item(db_session, job_id, fresh_company.id)
    assert stale is not None and fresh is not None
    await job_repo.claim_item(db_session, stale)
    await db_session.execute(
        update(EnrichJobItem)
        .where(EnrichJobItem.id == stale)
        .values(started_at=datetime(2020, 1, 1))
    )

    expired = await job_repo.expire_stale_items(
        db_session, older_than=STALE_AFTER, message=STALE_MESSAGE
    )

    assert expired == 1
    assert await item_status(db_session, stale) == (JobItemStatus.ERROR.value, STALE_MESSAGE)
    assert await item_status(db_session, fresh) == (JobItemStatus.PENDING.value, None)


async def test_db_create_job_dedupes_input_and_reports_busy_companies(
    db_session: AsyncSession,
    owner: User,
) -> None:
    idle = await add_company(db_session, "Alpha Idle")
    busy = await add_company(db_session, "Beta Busy")
    running = await job_repo.create_job(db_session)
    await job_repo.add_item(db_session, running, busy.id)

    created = await enrich_jobs.create_job(db_session, [idle.id, idle.id, busy.id])

    assert created.accepted == 1
    assert created.skipped == [busy.id]
    assert await job_repo.pending_item_ids(db_session, created.job_id) != []


async def test_db_create_job_with_nothing_accepted_is_finished(
    db_session: AsyncSession, owner: User
) -> None:
    busy = await add_company(db_session, "Beta Busy")
    await job_repo.add_item(db_session, await job_repo.create_job(db_session), busy.id)

    created = await enrich_jobs.create_job(db_session, [busy.id])
    job = await db_session.scalar(
        select(EnrichJob)
        .where(EnrichJob.id == created.job_id)
        .execution_options(populate_existing=True)
    )

    assert created.accepted == 0
    assert job is not None and job.finished_at is not None


class SharedSession:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def __call__(self) -> "SharedSession":
        return self

    async def __aenter__(self) -> AsyncSession:
        return self.session

    async def __aexit__(self, *exc: object) -> bool:
        return False


@dataclass
class Gate:
    started: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass
class Pipeline:
    outcomes: dict[str, list[object]] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    indexed: list[uuid.UUID] = field(default_factory=list)

    async def enrich(self, name: str, hints: Any = None, **kwargs: Any) -> CompanyProfileOut:
        self.calls.append(name)
        result = self.outcomes[name].pop(0)
        if isinstance(result, Gate):
            result.started.set()
            await result.release.wait()
            return sourced_profile()
        if isinstance(result, BaseException):
            raise result
        assert isinstance(result, CompanyProfileOut)
        return result

    async def index(self, profile_id: uuid.UUID) -> None:
        self.indexed.append(profile_id)


@dataclass(frozen=True)
class ItemState:
    status: str
    error: str | None
    attempts: int
    sourced_fields: int | None


@pytest.fixture
def pipeline(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> Pipeline:
    fake = Pipeline()
    monkeypatch.setattr(enrich_jobs, "SessionLocal", SharedSession(db_session))
    monkeypatch.setattr(enrich_jobs, "MAX_CONCURRENCY", 1)
    monkeypatch.setattr(enrich_jobs, "BACKOFF_SECONDS", (0.0, 0.0))
    monkeypatch.setattr(enrich_jobs, "enrich_company", fake.enrich)
    monkeypatch.setattr(enrich_jobs, "index_profile", fake.index)
    return fake


def sourced_profile() -> CompanyProfileOut:
    return CompanyProfileOut.model_validate(
        {
            "tax_code": "0301234567",
            "sources": {"tax_code": [{"url": "https://masothue.com/0301234567"}]},
            "llm_model": "gemini-3-flash",
            "generated_at": datetime(2026, 9, 18, tzinfo=UTC),
            "status": ProfileStatus.GENERATED,
        }
    )


async def run_enrich(
    db: AsyncSession, pipeline: Pipeline, outcomes: dict[str, Sequence[object]]
) -> uuid.UUID:
    companies = [await add_company(db, name) for name in outcomes]
    pipeline.outcomes.update({name: list(values) for name, values in outcomes.items()})
    created = await enrich_jobs.create_job(db, [company.id for company in companies])
    await enrich_jobs.run_job(created.job_id)
    return created.job_id


async def item_states(db: AsyncSession, job_id: uuid.UUID) -> dict[str, ItemState]:
    rows = await db.execute(
        select(
            Company.display_name,
            EnrichJobItem.status,
            EnrichJobItem.error,
            EnrichJobItem.attempts,
            EnrichJobItem.sourced_fields,
        )
        .join(Company, Company.id == EnrichJobItem.company_id)
        .where(EnrichJobItem.job_id == job_id)
    )
    return {
        row.display_name: ItemState(row.status, row.error, row.attempts, row.sourced_fields)
        for row in rows
    }


async def profile_status(db: AsyncSession, display_name: str) -> str | None:
    return await db.scalar(
        select(CompanyProfile.status)
        .join(Company, Company.id == CompanyProfile.company_id)
        .where(Company.display_name == display_name)
    )


async def job_finished(db: AsyncSession, job_id: uuid.UUID) -> bool:
    finished_at = await db.scalar(select(EnrichJob.finished_at).where(EnrichJob.id == job_id))
    return finished_at is not None


async def test_db_run_job_saves_every_company_and_closes_the_job(
    db_session: AsyncSession, owner: User, pipeline: Pipeline
) -> None:
    job_id = await run_enrich(
        db_session, pipeline, {"Alpha": [sourced_profile()], "Beta": [sourced_profile()]}
    )

    states = await item_states(db_session, job_id)
    assert {name: state.status for name, state in states.items()} == {
        "Alpha": "done",
        "Beta": "done",
    }
    assert states["Alpha"].sourced_fields == 1
    assert await profile_status(db_session, "Alpha") == ProfileStatus.GENERATED.value
    assert len(pipeline.indexed) == 2
    assert await job_finished(db_session, job_id)


async def test_db_run_job_isolates_a_failing_company(
    db_session: AsyncSession, owner: User, pipeline: Pipeline
) -> None:
    job_id = await run_enrich(
        db_session,
        pipeline,
        {"Alpha": [sourced_profile()], "Beta": [NoSourcedDataError("Beta")]},
    )

    states = await item_states(db_session, job_id)
    assert states["Alpha"].status == "done"
    assert states["Beta"].status == "error"
    assert "nguồn kiểm chứng" in (states["Beta"].error or "")
    assert await profile_status(db_session, "Alpha") == ProfileStatus.GENERATED.value
    assert await profile_status(db_session, "Beta") is None
    assert await job_finished(db_session, job_id)


async def test_db_run_job_retries_transient_errors_until_success(
    db_session: AsyncSession, owner: User, pipeline: Pipeline
) -> None:
    job_id = await run_enrich(
        db_session,
        pipeline,
        {"Alpha": [LLMError("timeout"), EnrichmentParseError("junk"), sourced_profile()]},
    )

    state = (await item_states(db_session, job_id))["Alpha"]
    assert (state.status, state.attempts) == ("done", 3)
    assert pipeline.calls == ["Alpha"] * 3


async def test_db_run_job_gives_up_after_max_attempts(
    db_session: AsyncSession, owner: User, pipeline: Pipeline
) -> None:
    job_id = await run_enrich(db_session, pipeline, {"Alpha": [LLMError("timeout")] * MAX_ATTEMPTS})

    state = (await item_states(db_session, job_id))["Alpha"]
    assert (state.status, state.attempts) == ("error", MAX_ATTEMPTS)
    assert (state.error or "").startswith(f"Thử {MAX_ATTEMPTS} lượt vẫn hỏng")
    assert await profile_status(db_session, "Alpha") is None


async def test_db_run_job_aborts_remaining_items_when_cliproxy_is_not_connected(
    db_session: AsyncSession, owner: User, pipeline: Pipeline
) -> None:
    offline = [LLMNotConnectedError("no auth")]
    job_id = await run_enrich(
        db_session, pipeline, {"Alpha": offline, "Beta": offline, "Gamma": offline}
    )

    states = await item_states(db_session, job_id)
    assert len(pipeline.calls) == 1
    assert {state.status for state in states.values()} == {"error"}
    assert all("Chưa kết nối CLIProxy" in (state.error or "") for state in states.values())
    assert await job_finished(db_session, job_id)


async def test_db_failed_regeneration_keeps_the_existing_profile(
    db_session: AsyncSession, owner: User, pipeline: Pipeline
) -> None:
    company = await add_company(db_session, "Alpha")
    db_session.add(
        CompanyProfile(
            user_id=OWNER_ID, company_id=company.id, tax_code="0309999999", status="verified"
        )
    )
    await db_session.flush()
    pipeline.outcomes["Alpha"] = [NoSourcedDataError("Alpha")]

    created = await enrich_jobs.create_job(db_session, [company.id])
    await enrich_jobs.run_job(created.job_id)

    assert (await item_states(db_session, created.job_id))["Alpha"].status == "error"
    assert await profile_status(db_session, "Alpha") == "verified"


async def test_db_unexpected_error_still_closes_item_and_job(
    db_session: AsyncSession, owner: User, pipeline: Pipeline, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(db: Any, company_id: uuid.UUID) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(company_repo, "discard_draft_profile", broken)
    job_id = await run_enrich(db_session, pipeline, {"Alpha": [NoSourcedDataError("Alpha")]})

    state = (await item_states(db_session, job_id))["Alpha"]
    assert state.status == "error"
    assert state.error == "Lỗi ngoài dự kiến (RuntimeError)."
    assert await job_finished(db_session, job_id)


async def start_enrich(
    db: AsyncSession, pipeline: Pipeline, outcomes: dict[str, Sequence[object]]
) -> tuple[uuid.UUID, dict[str, Company], "asyncio.Task[None]"]:
    companies = {name: await add_company(db, name) for name in outcomes}
    pipeline.outcomes.update({name: list(values) for name, values in outcomes.items()})
    created = await enrich_jobs.create_job(db, [company.id for company in companies.values()])
    task = asyncio.create_task(enrich_jobs.run_job(created.job_id))
    return created.job_id, companies, task


async def cancel_now(db: AsyncSession, **target: uuid.UUID) -> list[uuid.UUID]:
    cancelled = await enrich_jobs.cancel(db, **target)
    await db.commit()
    enrich_jobs.interrupt(cancelled)
    return cancelled


async def test_db_cancel_running_company_stops_it_and_drops_the_draft(
    db_session: AsyncSession, owner: User, pipeline: Pipeline
) -> None:
    gate = Gate()
    job_id, companies, task = await start_enrich(db_session, pipeline, {"Alpha": [gate]})
    await asyncio.wait_for(gate.started.wait(), timeout=5)
    assert await profile_status(db_session, "Alpha") == ProfileStatus.DRAFT.value

    cancelled = await cancel_now(db_session, company_id=companies["Alpha"].id)
    await asyncio.wait_for(task, timeout=5)

    state = (await item_states(db_session, job_id))["Alpha"]
    assert len(cancelled) == 1
    assert (state.status, state.error) == ("cancelled", enrich_jobs.CANCEL_MESSAGE)
    assert await profile_status(db_session, "Alpha") is None
    assert pipeline.indexed == []
    assert await job_finished(db_session, job_id)


async def test_db_cancel_job_also_skips_companies_still_waiting(
    db_session: AsyncSession, owner: User, pipeline: Pipeline
) -> None:
    gate = Gate()
    job_id, _, task = await start_enrich(
        db_session, pipeline, {"Alpha": [gate], "Beta": [gate], "Gamma": [gate]}
    )
    await asyncio.wait_for(gate.started.wait(), timeout=5)

    cancelled = await cancel_now(db_session, job_id=job_id)
    await asyncio.wait_for(task, timeout=5)

    states = await item_states(db_session, job_id)
    assert len(cancelled) == 3
    assert len(pipeline.calls) == 1
    assert {state.status for state in states.values()} == {"cancelled"}
    assert await job_finished(db_session, job_id)


async def test_db_cancelled_regeneration_keeps_the_existing_profile(
    db_session: AsyncSession, owner: User, pipeline: Pipeline
) -> None:
    company = await add_company(db_session, "Alpha")
    db_session.add(
        CompanyProfile(
            user_id=OWNER_ID, company_id=company.id, tax_code="0309999999", status="verified"
        )
    )
    await db_session.flush()
    gate = Gate()
    pipeline.outcomes["Alpha"] = [gate]
    created = await enrich_jobs.create_job(db_session, [company.id])
    task = asyncio.create_task(enrich_jobs.run_job(created.job_id))
    await asyncio.wait_for(gate.started.wait(), timeout=5)

    await cancel_now(db_session, company_id=company.id)
    await asyncio.wait_for(task, timeout=5)

    assert (await item_states(db_session, created.job_id))["Alpha"].status == "cancelled"
    assert await profile_status(db_session, "Alpha") == "verified"


async def test_db_cancel_cleans_an_orphaned_run_after_a_restart(
    db_session: AsyncSession, owner: User, pipeline: Pipeline
) -> None:
    company = await add_company(db_session, "Alpha")
    job_id = await job_repo.create_job(db_session)
    item_id = await job_repo.add_item(db_session, job_id, company.id)
    assert item_id is not None
    await job_repo.claim_item(db_session, item_id)
    await company_repo.ensure_draft_profile(db_session, company.id)

    cancelled = await cancel_now(db_session, company_id=company.id)

    assert cancelled == [item_id]
    assert (await item_states(db_session, job_id))["Alpha"].status == "cancelled"
    assert await profile_status(db_session, "Alpha") is None
    assert await job_repo.active_job_id(db_session, company.id) is None


async def test_db_cancel_without_a_running_job_does_nothing(
    db_session: AsyncSession, owner: User, pipeline: Pipeline
) -> None:
    company = await add_company(db_session, "Alpha")

    assert await cancel_now(db_session, company_id=company.id) == []
