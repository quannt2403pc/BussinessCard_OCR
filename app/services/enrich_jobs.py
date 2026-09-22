import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.models.company import CompanyProfile
from app.repositories import company as company_repo
from app.repositories import enrich_job as job_repo
from app.schemas.enrich_job import JobItemStatus
from app.services import kb, user_credentials
from app.services.cliproxy_client import CliProxyUnavailableError
from app.services.enrichment import EnrichmentParseError, build_hints, enrich_company
from app.services.llm import (
    LLMBlockedError,
    LLMError,
    LLMInvalidModelError,
    LLMNotConnectedError,
)

logger = logging.getLogger(__name__)

MAX_CONCURRENCY = 2
MAX_ATTEMPTS = 3
BACKOFF_SECONDS: tuple[float, ...] = (5.0, 15.0)
STALE_AFTER = timedelta(minutes=30)
STALE_MESSAGE = "Bị gián đoạn giữa chừng (api khởi động lại) — chạy lại để tạo hồ sơ."
CANCEL_MESSAGE = "Đã huỷ theo yêu cầu."
UNREACHABLE_MESSAGE = (
    "Không gọi được CLIProxy — kiểm tra container đang chạy (`docker compose up -d cliproxy`) "
    "rồi chạy lại"
)

_TASKS: set[asyncio.Task[None]] = set()
_RUNNING: dict[uuid.UUID, asyncio.Task[None]] = {}


class CompanyGoneError(RuntimeError):
    pass


class NoSourcedDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class Failure:
    message: str
    retry: bool = False
    abort_job: bool = False


@dataclass(frozen=True)
class Outcome:
    attempts: int
    sourced_fields: int | None = None
    failure: Failure | None = None


@dataclass(frozen=True)
class JobCreated:
    job_id: uuid.UUID
    accepted: int
    skipped: list[uuid.UUID]


def is_unreachable(exc: BaseException) -> bool:
    cause = exc.__cause__
    return isinstance(cause, CliProxyUnavailableError) and cause.status_code is None


def describe_failure(exc: BaseException) -> Failure:
    if isinstance(exc, LLMNotConnectedError):
        return Failure(
            "Chưa kết nối CLIProxy — vào /settings bấm “Kết nối CLIProxy (OAuth)” rồi chạy lại.",
            abort_job=True,
        )
    if isinstance(exc, LLMInvalidModelError):
        return Failure(f"LLM_MODEL cấu hình sai: {exc}", abort_job=True)
    if isinstance(exc, LLMBlockedError):
        return Failure("Nhà cung cấp LLM từ chối trả lời cho công ty này.")
    if isinstance(exc, NoSourcedDataError):
        return Failure("Không tìm thấy thông tin công khai nào có nguồn kiểm chứng được.")
    if isinstance(exc, CompanyGoneError):
        return Failure("Công ty đã bị xoá trong lúc chờ tạo hồ sơ.")
    if isinstance(exc, EnrichmentParseError):
        return Failure("Model trả kết quả không đọc được.", retry=True)
    if isinstance(exc, LLMError) and is_unreachable(exc):
        return Failure(f"{UNREACHABLE_MESSAGE}: {exc}")
    if isinstance(exc, LLMError):
        return Failure(f"Không gọi được LLM: {exc}", retry=True)
    return Failure(f"Lỗi ngoài dự kiến ({type(exc).__name__}).")


async def run_with_retry(
    operation: Callable[[int], Awaitable[int]],
    *,
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
) -> Outcome:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return Outcome(attempts=attempt, sourced_fields=await operation(attempt))
        except Exception as exc:
            failure = describe_failure(exc)
            if not failure.retry:
                logger.warning("Enrich hỏng lượt %d, không thử lại: %r", attempt, exc)
                return Outcome(attempts=attempt, failure=failure)
            if attempt == MAX_ATTEMPTS:
                message = f"Thử {attempt} lượt vẫn hỏng: {failure.message}"
                return Outcome(attempts=attempt, failure=replace(failure, message=message))
            delay = BACKOFF_SECONDS[attempt - 1]
            logger.warning(
                "Enrich hỏng lượt %d/%d (%s) — chờ %.0fs rồi thử lại",
                attempt,
                MAX_ATTEMPTS,
                exc,
                delay,
            )
            await sleep(delay)
    raise AssertionError("MAX_ATTEMPTS must be at least 1")


async def create_job(db: AsyncSession, company_ids: Sequence[uuid.UUID]) -> JobCreated:
    await job_repo.expire_stale_items(db, older_than=STALE_AFTER, message=STALE_MESSAGE)
    job_id = await job_repo.create_job(db)
    accepted = 0
    skipped: list[uuid.UUID] = []
    for company_id in dict.fromkeys(company_ids):
        if await job_repo.add_item(db, job_id, company_id) is None:
            skipped.append(company_id)
        else:
            accepted += 1
    if not accepted:
        await job_repo.finish_job(db, job_id)
    return JobCreated(job_id=job_id, accepted=accepted, skipped=skipped)


def start(job_id: uuid.UUID) -> None:
    task = asyncio.create_task(run_job(job_id))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def run_job(job_id: uuid.UUID) -> None:
    async with SessionLocal() as db:
        item_ids = await job_repo.pending_item_ids(db, job_id)
    logger.info("Enrich job %s: bắt đầu %d công ty", job_id, len(item_ids))

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    tasks = {
        item_id: asyncio.create_task(_process(job_id, item_id, semaphore)) for item_id in item_ids
    }
    _RUNNING.update(tasks)
    try:
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    finally:
        for item_id in tasks:
            _RUNNING.pop(item_id, None)
    for item_id, result in zip(tasks, results, strict=True):
        if isinstance(result, asyncio.CancelledError):
            continue
        if isinstance(result, BaseException):
            logger.error("Enrich job %s: lỗi ngoài dự kiến ở %s", job_id, item_id, exc_info=result)
            await _finish_item(
                item_id, JobItemStatus.ERROR, error=f"Lỗi ngoài dự kiến ({type(result).__name__})."
            )

    async with SessionLocal() as db:
        await job_repo.finish_job(db, job_id)
        await db.commit()
    logger.info("Enrich job %s: xong", job_id)


async def enrich_and_save(company_id: uuid.UUID) -> int:
    async with SessionLocal() as db:
        company = await company_repo.get_company(db, company_id)
        if company is None:
            raise CompanyGoneError(str(company_id))
        name = company.display_name
        model = await user_credentials.model_for_user_id(db, company.user_id)
        hints = build_hints(await company_repo.list_contacts(db, company_id))
        await company_repo.ensure_draft_profile(db, company_id)
        await db.commit()

    profile = await enrich_company(name, hints, model=model)
    sourced = profile.sourced_field_count()
    if not sourced:
        raise NoSourcedDataError(name)

    async with SessionLocal() as db:
        saved = await company_repo.save_profile(
            db,
            company_id,
            profile,
            llm_model=profile.llm_model,
            generated_at=profile.generated_at,
        )
        await db.commit()
    await index_profile(saved.id)
    return sourced


async def index_profile(profile_id: uuid.UUID) -> None:
    try:
        async with SessionLocal() as db:
            profile = await db.get(CompanyProfile, profile_id)
            if profile is None:
                return
            chunks = await kb.ingest_company_profile(db, profile)
        logger.info("Hồ sơ %s: đã index %d chunk vào KB", profile_id, chunks)
    except Exception:
        logger.exception("Không index được hồ sơ %s", profile_id)


async def cancel(
    db: AsyncSession,
    *,
    job_id: uuid.UUID | None = None,
    company_id: uuid.UUID | None = None,
) -> list[uuid.UUID]:
    items = await job_repo.active_items(db, job_id=job_id, company_id=company_id)
    item_ids = [item_id for item_id, _ in items]
    await job_repo.cancel_items(db, item_ids, CANCEL_MESSAGE)
    for owner in dict.fromkeys(owner for _, owner in items):
        await company_repo.discard_draft_profile(db, owner)
    return item_ids


def interrupt(item_ids: Iterable[uuid.UUID]) -> None:
    for item_id in item_ids:
        task = _RUNNING.get(item_id)
        if task is not None and not task.done():
            task.cancel()


async def _process(job_id: uuid.UUID, item_id: uuid.UUID, semaphore: asyncio.Semaphore) -> None:
    async with semaphore:
        async with SessionLocal() as db:
            company_id = await job_repo.claim_item(db, item_id)
            await db.commit()
        if company_id is None:
            return
        try:
            await _enrich_item(job_id, item_id, company_id)
        except asyncio.CancelledError:
            await _discard_cancelled(item_id, company_id)
            raise


async def _enrich_item(job_id: uuid.UUID, item_id: uuid.UUID, company_id: uuid.UUID) -> None:
    async def attempt(number: int) -> int:
        async with SessionLocal() as db:
            await job_repo.record_attempt(db, item_id, number)
            await db.commit()
        return await enrich_and_save(company_id)

    outcome = await run_with_retry(attempt)
    if outcome.failure is None:
        await _finish_item(item_id, JobItemStatus.DONE, sourced_fields=outcome.sourced_fields)
        return

    logger.warning("Enrich %s hỏng: %s", company_id, outcome.failure.message)
    async with SessionLocal() as db:
        await company_repo.discard_draft_profile(db, company_id)
        await job_repo.finish_item(
            db, item_id, status=JobItemStatus.ERROR, error=outcome.failure.message
        )
        if outcome.failure.abort_job:
            await job_repo.abort_pending(db, job_id, outcome.failure.message)
        await db.commit()


async def _discard_cancelled(item_id: uuid.UUID, company_id: uuid.UUID) -> None:
    async with SessionLocal() as db:
        await company_repo.discard_draft_profile(db, company_id)
        await job_repo.cancel_items(db, [item_id], CANCEL_MESSAGE)
        await db.commit()
    logger.info("Enrich %s: đã huỷ giữa chừng", company_id)


async def _finish_item(
    item_id: uuid.UUID,
    status: JobItemStatus,
    *,
    error: str | None = None,
    sourced_fields: int | None = None,
) -> None:
    async with SessionLocal() as db:
        await job_repo.finish_item(
            db, item_id, status=status, error=error, sourced_fields=sourced_fields
        )
        await db.commit()
