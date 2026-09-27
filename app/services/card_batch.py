"""Hàng đợi nền cho upload hàng loạt danh thiếp: giới hạn đồng thời + retry có backoff.

Chủ sở hữu: Q | Task: 5.2 | xem Task.md

Phân vai rõ ràng giữa request và nền:

* **Trong request** (`routers/cards.py`): đọc file, băm, chống trùng, tiền xử lý, ghi ảnh xuống
  volume, tạo bản ghi `pending`. Toàn bộ là việc nhanh và **không lấy lại được nếu mất** — làm
  ngay để ảnh người dùng vừa chọn không bốc hơi cùng với tiến trình.
* **Ở nền** (file này): gọi vision cho từng ảnh. Đây mới là phần mất 3–5 giây/ảnh và là phần
  duy nhất chạm rate limit của rủi ro **R5**.

Hệ quả có chủ đích: mất job (restart container) thì **không mất ảnh**. Bản ghi còn nằm trong DB
ở trạng thái `pending` kèm ghi chú, quét lại được. Nếu làm ngược — giữ byte ảnh trong RAM rồi
lưu ở nền — một lần `docker compose restart` giữa chừng là người dùng phải chụp lại cả chồng.

**Job nằm trong bộ nhớ tiến trình, cố ý không lưu DB.** Job chỉ là *tiến trình hiển thị*, còn
kết quả thật đã nằm ở `business_cards`. Dựng thêm một bảng + Alembic revision cho dữ liệu sống
vài phút là cái giá không tương xứng với một bản demo chạy đúng một tiến trình uvicorn
(`docker-compose.yml`). Chỗ hụt được nói thẳng ra cho người dùng: poll một `job_id` đã mất thì
trả 404 kèm đúng lý do, chứ không im lặng trả một job rỗng.
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

#: Số ảnh được gọi vision cùng lúc. Hai, không phải "càng nhiều càng nhanh": mỗi lượt là một
#: lời gọi Gemini qua CLIProxy bằng **một** credential OAuth duy nhất, bắn 10 request song song
#: chỉ đổi "chờ lâu" lấy "429 rồi hỏng cả loạt" (rủi ro R5, Plan.md mục 6).
MAX_CONCURRENCY = 2

#: Số lượt thử mỗi ảnh ở tầng job. Bên dưới còn hai tầng thử lại nữa và chúng bù cho nhau:
#: `CliProxyClient` retry lỗi mạng/429/5xx, `ocr.extract_card` gọi lại 1 lần khi model trả chữ
#: không phải JSON. Tầng này lo ca còn lại — hai tầng kia đã cạn lượt mà vẫn hỏng.
MAX_ATTEMPTS = 3

#: Nghỉ bao lâu trước lượt thử thứ 2 và thứ 3. Dài hơn backoff 0.5s→1s của `CliProxyClient` vì
#: lỗi đi được tới đây thường là rate limit thật, mà rate limit đo bằng giây chứ không mili giây.
BACKOFF_SECONDS: tuple[float, ...] = (2.0, 5.0)

#: Số job giữ lại trong bộ nhớ. Job cũ nhất bị đẩy ra khi tràn — trang `/cards/batch` chỉ poll
#: job vừa tạo, còn giữ vô hạn thì một tiến trình chạy cả tuần sẽ phình dần.
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

    `image_path` là đường dẫn **tuyệt đối** do `routers/cards.py` giải ra lúc lưu file, cố ý
    không phải đường dẫn tương đối trong DB: giải lại ở đây đồng nghĩa với chép lại logic kiểm
    "có nằm trong `UPLOAD_DIR` không" lần thứ hai, mà hai bản sao của một quy tắc bảo mật là chỗ
    chắc chắn sẽ lệch nhau. Trường này không bao giờ ra tới API — đường dẫn nội bộ của container
    không phải thứ để trả cho trình duyệt.
    """

    filename: str
    #: Chủ sở hữu bản ghi (task 12.5). Job chạy **sau** khi request đã trả 202, tức không còn
    #: cookie phiên nào để hỏi lại "ai đang upload" — người sở hữu phải đi kèm từng mục ngay từ
    #: lúc xếp hàng, nếu không `card_repo.get()` (nay đòi `workspace_id`) không tra lại được bản ghi.
    workspace_id: uuid.UUID | None = None
    #: **Người bấm upload**, giữ riêng khỏi `workspace_id` (task NEXT-05). Lượt quét đi bằng
    #: credential OAuth *của chính người đó* (13.2) và dùng model *họ* chọn — hai thứ ấy thuộc
    #: về cá nhân, không thuộc về tổ chức.
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
    #: Không gian nhận lô này. `GET /api/cards/batch-jobs/{id}` đối chiếu trường **này**:
    #: `job_id` là UUID khó đoán, nhưng "khó đoán" không phải kiểm soát truy cập — tiến trình
    #: quét của tổ chức khác vẫn là dữ liệu của tổ chức khác.
    workspace_id: uuid.UUID
    #: Người bấm nút. Không dùng để phân quyền (`NEXT-05`), chỉ để chọn credential CLIProxy và
    #: model OCR của đúng người ấy — xem `_process()`.
    user_id: uuid.UUID
    items: list[BatchItem]
    created_at: datetime
    finished_at: datetime | None = None
    #: Lý do dừng sớm cả job (hiện chỉ có một: chưa kết nối OAuth — xem `_process`).
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

#: `OrderedDict` chứ không `dict` thường: cần đẩy job **cũ nhất** ra khi tràn `MAX_JOBS`.
_JOBS: OrderedDict[uuid.UUID, BatchJob] = OrderedDict()

#: Giữ tham chiếu mạnh tới task đang chạy. `asyncio.create_task` chỉ giữ tham chiếu yếu — không
#: neo lại thì bộ gom rác có quyền dọn task giữa chừng, và job đứng im không rõ lý do.
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

    Dùng `asyncio.create_task` chứ không `BackgroundTasks` của FastAPI: Starlette chạy background
    task **trước khi nhả kết nối HTTP**, nên một job 30 ảnh (~1 phút) sẽ giữ nguyên connection đó
    suốt thời gian chạy. Trình duyệt chỉ mở tối đa 6 connection mỗi host và trang `/cards/batch`
    cần một cái để poll — giữ lại là tự bóp cổ chính mình.
    """
    task = asyncio.create_task(run_job(job))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def run_job(job: BatchJob) -> None:
    """Quét toàn bộ ảnh trong job, tối đa `MAX_CONCURRENCY` ảnh cùng lúc."""
    pending = [item for item in job.items if item.status is ItemStatus.PENDING]
    logger.info("Batch job %s: bắt đầu quét %d/%d ảnh", job.id, len(pending), job.total)

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    # `return_exceptions=True`: một ảnh ném lỗi ngoài dự kiến không được kéo theo cả loạt còn
    # lại. `_process` đã bọc mọi lỗi đã biết, cái lọt được tới đây là lỗi lập trình — ghi log
    # rồi đi tiếp vẫn hơn là bỏ dở 29 ảnh kia.
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
                # Chưa bấm OAuth thì 29 ảnh còn lại cũng hỏng y hệt. Dừng cả job ngay thay vì
                # đốt một phút để in ra 30 lần cùng một câu.
                job.aborted_reason = (
                    f"Dừng cả lượt vì chưa kết nối CLIProxy: {exc} "
                    "Vào /settings bấm “Kết nối AI” rồi quét lại."
                )
                _fail(item, job.aborted_reason)
                return
            except (llm.LLMInvalidModelError, llm.LLMBlockedError, BatchItemError) as exc:
                # Sai tên model / bị chặn nội dung / bản ghi đã bị xoá: thử lại y nguyên cũng ra
                # đúng kết quả đó, chỉ tốn thêm lượt gọi.
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

    Đọc file **ở đây** chứ không giữ sẵn byte trong `BatchItem`: 30 ảnh × 10MB nằm trong RAM cả
    phút chỉ để chờ tới lượt là cái giá không đáng, trong khi đọc lại từ volume mất vài mili
    giây. Mỗi lượt mở session riêng vì session của request đã đóng từ lúc trả 202.
    """
    if item.card_id is None or item.image_path is None:  # không xảy ra: router luôn gán đủ
        raise BatchItemError("Mục này không có ảnh để quét.")

    try:
        data = item.image_path.read_bytes()
    except OSError as exc:
        raise BatchItemError(f"Không đọc được ảnh đã lưu: {exc}") from exc

    if item.workspace_id is None:  # không xảy ra: router luôn gán (xem `BatchItem.workspace_id`)
        raise BatchItemError("Mục này không biết thuộc về ai.")

    async with SessionLocal() as db:
        # `item.user_id`, KHÔNG phải `workspace_id`: model quét là lựa chọn cá nhân và
        # credential OAuth cũng vậy (13.2 + NEXT-05).
        assert item.user_id is not None
        model = await user_credentials.model_for_user_id(db, item.user_id, "ocr")
    # `extract_and_translate` chứ không `extract_card`: đường batch phải ra đúng cùng một
    # bộ cột như đường upload 1 ảnh, kể cả 4 cột Việt hoá (EX-04).
    result = await ocr.extract_and_translate(data, mime_type=image_service.OUTPUT_MIME, model=model)

    async with SessionLocal() as db:
        card = await card_repo.get(db, item.card_id, workspace_id=item.workspace_id)
        if card is None:
            raise BatchItemError("Bản ghi đã bị xoá trong lúc chờ quét.")

        card_status, notes = ocr.status_and_notes(result, None)
        # Gán tay vì `update_fields(notes=None)` nghĩa là "giữ nguyên", không phải "xoá trắng" —
        # thiếu dòng này thì ghi chú "đang chờ quét" ở lại mãi sau khi đã quét xong.
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

    Ghi vào DB chứ không chỉ giữ trong job: job biến mất sau `MAX_JOBS` lượt hoặc sau restart,
    còn câu hỏi "vì sao thẻ này chưa quét được" thì vẫn phải trả lời được vào ngày mai.
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
