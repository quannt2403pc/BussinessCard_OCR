"""Hàng đợi nền cho upload hàng loạt danh thiếp: giới hạn đồng thời + retry có backoff.

Chủ sở hữu: Q | Task: 5.2

Phân vai: **trong request** (`routers/cards.py`) đọc file, băm, chống trùng, ghi ảnh xuống volume,
tạo bản ghi `pending` — việc nhanh và không lấy lại được nếu mất. **Ở nền** (file này) gọi vision,
phần mất 3–5 giây/ảnh và chạm rate limit. Hệ quả: restart container thì mất job nhưng không mất ảnh.

**Job nằm trong bộ nhớ tiến trình, cố ý không lưu DB** — job chỉ là tiến trình hiển thị, kết quả
thật đã ở `business_cards`. Poll một `job_id` đã mất thì trả 404 kèm lý do, không im lặng trả rỗng.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from app.core.db import SessionLocal
from app.models.card import CardStatus
from app.repositories import card as card_repo
from app.services import image as image_service
from app.services import llm, ocr, user_credentials

logger = logging.getLogger(__name__)

#: Số ảnh gọi vision cùng lúc. Hai, vì cả lô đi bằng **một** credential OAuth — bắn 10 request
#: song song chỉ đổi "chờ lâu" lấy "429 rồi hỏng cả loạt" (rủi ro R5).
MAX_CONCURRENCY = 2

#: Số lượt thử mỗi ảnh ở tầng job. Dưới còn hai tầng nữa: `CliProxyClient` retry lỗi mạng/429/5xx,
#: `ocr.extract_card` gọi lại 1 lần khi model trả chữ không phải JSON.
MAX_ATTEMPTS = 3

#: Nghỉ bao lâu trước lượt thử thứ 2 và thứ 3 — lỗi tới được đây thường là rate limit thật.
BACKOFF_SECONDS: tuple[float, ...] = (2.0, 5.0)

#: Số job giữ lại trong bộ nhớ; job cũ nhất bị đẩy ra khi tràn.
MAX_JOBS = 20


class ItemStatus(StrEnum):
    """Trạng thái một ảnh trong job — khớp hợp đồng ở `docs/api.md` mục 3."""

    PENDING = "pending"  # đã lưu ảnh, đang xếp hàng chờ quét
    RUNNING = "running"  # đang gọi vision
    DONE = "done"  # quét xong, hoặc là ảnh trùng nên không cần quét
    ERROR = "error"  # bỏ cuộc sau khi hết lượt thử


class BatchItemError(RuntimeError):
    """Hỏng theo kiểu thử lại cũng vô ích (bản ghi bị xoá giữa chừng, ảnh mất khỏi volume)."""


@dataclass
class BatchItem:
    """Một ảnh trong một lượt batch.

    `image_path` là đường dẫn **tuyệt đối** do `routers/cards.py` giải ra lúc lưu file. Giải lại
    ở đây là chép lần thứ hai quy tắc "có nằm trong `UPLOAD_DIR` không". Không bao giờ ra tới API.
    """

    filename: str
    #: Không gian sở hữu bản ghi. Job chạy **sau** khi request đã trả 202, không còn cookie phiên
    #: nào để hỏi lại, nên phải đi kèm từng mục ngay từ lúc xếp hàng.
    workspace_id: uuid.UUID | None = None
    #: **Người bấm upload**, giữ riêng khỏi `workspace_id`: credential OAuth và model là của
    #: cá nhân, không của tổ chức.
    user_id: uuid.UUID | None = None
    card_id: uuid.UUID | None = None
    image_path: Path | None = None
    status: ItemStatus = ItemStatus.PENDING
    #: Ảnh đã được quét từ trước → trả lại bản ghi cũ, không gọi model. Không phải lỗi.
    duplicate: bool = False
    error: str | None = None
    attempts: int = 0
    ocr_ms: int | None = None


@dataclass
class BatchJob:
    """Một lượt upload hàng loạt."""

    id: uuid.UUID
    #: Không gian nhận lô này — `job_id` là UUID khó đoán, nhưng "khó đoán" không phải kiểm soát
    #: truy cập.
    workspace_id: uuid.UUID
    #: Người bấm nút. Không dùng để phân quyền, chỉ để chọn credential CLIProxy và model OCR.
    user_id: uuid.UUID
    items: list[BatchItem]
    created_at: datetime
    finished_at: datetime | None = None
    #: Lý do dừng sớm cả job (hiện chỉ có một: chưa kết nối OAuth).
    aborted_reason: str | None = None

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def done(self) -> int:
        return sum(1 for item in self.items if item.status is ItemStatus.DONE)

    @property
    def failed(self) -> int:
        return sum(1 for item in self.items if item.status is ItemStatus.ERROR)

    @property
    def running(self) -> int:
        return sum(1 for item in self.items if item.status is ItemStatus.RUNNING)

    @property
    def finished(self) -> bool:
        return self.finished_at is not None


# --------------------------------------------------------------------------- sổ job

#: `OrderedDict` chứ không `dict`: cần đẩy job **cũ nhất** ra khi tràn `MAX_JOBS`.
_JOBS: OrderedDict[uuid.UUID, BatchJob] = OrderedDict()

#: Giữ tham chiếu mạnh tới task đang chạy — `create_task` chỉ giữ tham chiếu yếu, không neo lại
#: thì bộ gom rác có quyền dọn task giữa chừng.
_TASKS: set[asyncio.Task[None]] = set()


def create_job(items: list[BatchItem], *, workspace_id: uuid.UUID, user_id: uuid.UUID) -> BatchJob:
    """Ghi một job mới vào sổ và trả về. Chưa chạy gì — gọi `start()` để khởi động."""
    job = BatchJob(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        user_id=user_id,
        items=items,
        created_at=datetime.now(UTC),
    )
    _JOBS[job.id] = job
    while len(_JOBS) > MAX_JOBS:
        evicted, _ = _JOBS.popitem(last=False)
        logger.info("Batch job %s bị đẩy khỏi bộ nhớ (chỉ giữ %d job gần nhất)", evicted, MAX_JOBS)
    return job


def get_job(job_id: uuid.UUID) -> BatchJob | None:
    return _JOBS.get(job_id)


def start(job: BatchJob) -> None:
    """Chạy job ở nền, trả về ngay.

    Dùng `asyncio.create_task` chứ không `BackgroundTasks`: Starlette chạy background task
    **trước khi nhả kết nối HTTP**, nên job 30 ảnh sẽ giữ nguyên connection suốt thời gian chạy —
    trong khi trang `/cards/upload` còn cần một connection để poll.
    """
    task = asyncio.create_task(run_job(job))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def run_job(job: BatchJob) -> None:
    """Quét toàn bộ ảnh trong job, tối đa `MAX_CONCURRENCY` ảnh cùng lúc."""
    pending = [item for item in job.items if item.status is ItemStatus.PENDING]
    logger.info("Batch job %s: bắt đầu quét %d/%d ảnh", job.id, len(pending), job.total)

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    # `return_exceptions=True`: một ảnh ném lỗi ngoài dự kiến không được kéo theo cả loạt còn lại.
    results = await asyncio.gather(
        *(_process(job, item, semaphore) for item in pending), return_exceptions=True
    )
    for item, result in zip(pending, results, strict=True):
        if isinstance(result, BaseException):
            logger.error(
                "Batch job %s: lỗi ngoài dự kiến ở %s", job.id, item.filename, exc_info=result
            )
            _fail(item, f"Lỗi ngoài dự kiến: {result}")

    job.finished_at = datetime.now(UTC)
    logger.info(
        "Batch job %s: xong sau %.1fs — %d thành công, %d lỗi",
        job.id,
        (job.finished_at - job.created_at).total_seconds(),
        job.done,
        job.failed,
    )


# --------------------------------------------------------------------------- nội bộ


async def _process(job: BatchJob, item: BatchItem, semaphore: asyncio.Semaphore) -> None:
    """Quét một ảnh, thử lại tối đa `MAX_ATTEMPTS` lượt với backoff tăng dần."""
    async with semaphore:
        if job.aborted_reason is not None:
            _fail(item, job.aborted_reason)
            return

        item.status = ItemStatus.RUNNING

        for attempt in range(1, MAX_ATTEMPTS + 1):
            item.attempts = attempt
            try:
                await _scan(item)
            except llm.LLMNotConnectedError as exc:
                # Chưa bấm OAuth thì 29 ảnh còn lại cũng hỏng y hệt — dừng cả job ngay.
                job.aborted_reason = (
                    f"Dừng cả lượt vì chưa kết nối CLIProxy: {exc} "
                    "Vào /settings bấm “Kết nối AI” rồi quét lại."
                )
                _fail(item, job.aborted_reason)
                return
            except (llm.LLMInvalidModelError, llm.LLMBlockedError, BatchItemError) as exc:
                # Sai tên model / bị chặn nội dung / bản ghi đã bị xoá: thử lại cũng vô ích.
                _fail(item, str(exc))
                return
            except (llm.LLMError, ocr.OcrError) as exc:
                if attempt == MAX_ATTEMPTS:
                    _fail(item, f"Thử {attempt} lượt vẫn hỏng: {exc}")
                    return
                delay = BACKOFF_SECONDS[attempt - 1]
                logger.warning(
                    "Batch: %s hỏng lượt %d/%d (%s) — chờ %.0fs rồi thử lại",
                    item.filename,
                    attempt,
                    MAX_ATTEMPTS,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
            else:
                item.status = ItemStatus.DONE
                item.error = None
                return


async def _scan(item: BatchItem) -> None:
    """Đọc ảnh từ volume → gọi vision → ghi kết quả vào bản ghi `pending` đã có.

    Đọc file ở đây chứ không giữ byte trong `BatchItem`: 30 ảnh × 10MB nằm trong RAM cả phút là
    cái giá không đáng. Mỗi lượt mở session riêng vì session của request đã đóng từ lúc trả 202.
    """
    if item.card_id is None or item.image_path is None:  # không xảy ra: router luôn gán đủ
        raise BatchItemError("Mục này không có ảnh để quét.")

    try:
        data = item.image_path.read_bytes()
    except OSError as exc:
        raise BatchItemError(f"Không đọc được ảnh đã lưu: {exc}") from exc

    if item.workspace_id is None:  # không xảy ra: router luôn gán (xem `BatchItem.workspace_id`)
        raise BatchItemError("Mục này không biết thuộc về ai.")

    # `raise` chứ không `assert`: `assert` không được `_process()` bắt như `BatchItemError` nên
    # nổ thành "Lỗi ngoài dự kiến" kèm traceback, và `python -O` thì bỏ qua hẳn (I-39).
    if item.user_id is None:  # không xảy ra: router luôn gán (xem `BatchItem.user_id`)
        raise BatchItemError("Mục này không biết ai bấm upload.")

    async with SessionLocal() as db:
        # `item.user_id`, KHÔNG phải `workspace_id`: model quét và credential OAuth là lựa chọn
        # cá nhân.
        model = await user_credentials.model_for_user_id(db, item.user_id, "ocr")
    # `extract_and_translate` chứ không `extract_card`: đường batch phải ra đúng cùng một bộ cột
    # như đường upload 1 ảnh, kể cả 4 cột Việt hoá.
    result = await ocr.extract_and_translate(data, mime_type=image_service.OUTPUT_MIME, model=model)

    async with SessionLocal() as db:
        card = await card_repo.get(db, item.card_id, workspace_id=item.workspace_id)
        if card is None:
            raise BatchItemError("Bản ghi đã bị xoá trong lúc chờ quét.")

        card_status, notes = ocr.status_and_notes(result, None)
        # Gán tay vì `update_fields(notes=None)` nghĩa là "giữ nguyên", không phải "xoá trắng".
        card.notes = notes
        await card_repo.update_fields(db, card, result.card_fields(), status=card_status)

    item.ocr_ms = result.elapsed_ms
    logger.info(
        "Batch: %s → card %s (%dms, thiếu %d trường bắt buộc)",
        item.filename,
        item.card_id,
        result.elapsed_ms,
        len(result.extraction.missing_required()),
    )


def _fail(item: BatchItem, reason: str) -> None:
    """Đánh dấu một ảnh hỏng, và ghi lý do lên bản ghi `pending` để còn thấy được ở `/cards`.

    Ghi vào DB chứ không chỉ giữ trong job: job biến mất sau restart, còn câu hỏi "vì sao thẻ
    này chưa quét được" thì vẫn phải trả lời được vào ngày mai.
    """
    item.status = ItemStatus.ERROR
    item.error = reason
    if item.card_id is not None:
        # Hàm này đồng bộ (gọi từ cả `run_job` lẫn `_process`) nên không await được ở đây —
        # giao phần ghi DB cho một task rời.
        task = asyncio.create_task(
            _note_failure(item.card_id, reason, workspace_id=item.workspace_id)
        )
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)


async def _note_failure(card_id: uuid.UUID, reason: str, *, workspace_id: uuid.UUID | None) -> None:
    """Ghi lý do quét hỏng vào `business_cards.notes`, giữ nguyên trạng thái `pending`."""
    if workspace_id is None:
        return
    try:
        async with SessionLocal() as db:
            card = await card_repo.get(db, card_id, workspace_id=workspace_id)
            if card is None:
                return
            card.notes = f"Quét hàng loạt thất bại: {reason}"
            await card_repo.update_fields(db, card, {}, status=CardStatus.PENDING)
    except Exception:  # ghi chú hỏng thì thôi, không được phép làm hỏng thêm thứ gì
        logger.exception("Không ghi được lý do lỗi vào card %s", card_id)
