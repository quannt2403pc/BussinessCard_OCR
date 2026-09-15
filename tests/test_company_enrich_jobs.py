import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.core.db import get_db
from app.models.company import Company, EnrichJob, EnrichJobItem
from app.repositories import company as company_repo
from app.repositories import enrich_job as job_repo
from app.routers import companies
from app.services import enrich_jobs
from app.services.enrich_jobs import (
    BACKOFF_SECONDS,
    MAX_ATTEMPTS,
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
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


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

    async def existing(db: Any, ids: list[uuid.UUID]) -> set[uuid.UUID]:
        return set(ids)

    async def fake_create(db: Any, ids: list[uuid.UUID]) -> JobCreated:
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
    async def existing(db: Any, ids: list[uuid.UUID]) -> set[uuid.UUID]:
        return set(ids)

    async def fake_create(db: Any, ids: list[uuid.UUID]) -> JobCreated:
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
    async def existing(db: Any, ids: list[uuid.UUID]) -> set[uuid.UUID]:
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
    async def fake_company(db: Any, company_id: uuid.UUID) -> Company:
        return Company(id=company_id, display_name="ABC", name_normalized="abc")

    async def fake_create(db: Any, ids: list[uuid.UUID]) -> JobCreated:
        assert ids == [COMPANY_ID]
        return JobCreated(job_id=JOB_ID, accepted=1, skipped=[])

    monkeypatch.setattr(company_repo, "get_company", fake_company)
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

    async def fake_company(db: Any, company_id: uuid.UUID) -> Company:
        return Company(id=company_id, display_name="ABC", name_normalized="abc")

    async def fake_create(db: Any, ids: list[uuid.UUID]) -> JobCreated:
        return JobCreated(job_id=JOB_ID, accepted=0, skipped=list(ids))

    async def fake_active(db: Any, company_id: uuid.UUID) -> uuid.UUID:
        return running_job

    monkeypatch.setattr(company_repo, "get_company", fake_company)
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
    async def nothing(db: Any, company_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(company_repo, "get_company", nothing)
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

    async def fake_job(db: Any, job_id: uuid.UUID) -> EnrichJob:
        return job

    async def fake_items(db: Any, job_id: uuid.UUID) -> list[tuple[EnrichJobItem, str]]:
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
    async def nothing(db: Any, job_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(job_repo, "get_job", nothing)
    response = await client.get(f"/api/companies/enrich-jobs/{uuid.uuid4()}")
    assert response.status_code == 404
