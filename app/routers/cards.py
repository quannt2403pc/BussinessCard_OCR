"""F1 — upload, danh sách, chi tiết, sửa, xoá, confirm danh thiếp.

Chủ sở hữu: Q | Task: 3.1, 4.1, 4.2, 4.3, 4.4, 4.5, 5.1, 5.2, 5.3, 12.5 | xem Task.md

Router khai **đường dẫn đầy đủ** thay vì đặt `prefix="/api/cards"`, theo đúng tiền lệ
`routers/integration.py`: từ task 4.4 file này phục vụ cả API (`/api/cards/*`) lẫn ba trang HTML
(`/cards`, `/cards/upload`, `/cards/{id}`) cộng một chuyển hướng cũ (`/cards/batch`, gộp ở I-38).
Gom vào một router để không phải đụng `app/main.py` (quy ước số 4).

⚠️ **Thứ tự khai báo route HTML là một phần của thiết kế**: `/cards/upload` và `/cards/batch` phải
đứng trước `/cards/{card_id}`, nếu không hai chữ `upload`/`batch` sẽ rơi vào route chi tiết và
chết ở bước parse UUID.

Một lượt upload đi qua đúng bốn bước, theo thứ tự:

1. **Đọc có giới hạn** — cắt ở `MAX_UPLOAD_MB` ngay lúc đọc, không đọc hết rồi mới đo. Đọc hết
   một file 2GB vào RAM để sau đó trả 413 là tự mở cửa cho việc treo container.
2. **Chống trùng** — SHA-256 của **file gốc người dùng gửi**, không phải ảnh sau khi nén: nén
   JPEG không tất định giữa các phiên bản Pillow, băm bản đã nén thì cùng một ảnh vẫn có thể ra
   hai hash khác nhau sau khi nâng thư viện.
3. **Tiền xử lý + lưu volume** (task 3.2).
4. **OCR + lưu DB** (task 3.4, 3.5).

Quyết định đáng chú ý: **OCR hỏng không làm hỏng lượt upload.** Chưa bấm OAuth, CLIProxy chết,
model trả JSON rác — ảnh vẫn được lưu, bản ghi vẫn được tạo ở trạng thái `pending` kèm lý do
trong `ocr_error`, và người dùng quét lại sau. Trả 5xx rồi vứt ảnh đi là bắt người dùng chụp
lại chồng danh thiếp chỉ vì token hết hạn.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.security import CurrentUser
from app.core.templates import templates
from app.core.workspace import CurrentWorkspace, WriterWorkspace
from app.models.card import BusinessCard, CardStatus
from app.models.kb import KBSourceType
from app.repositories import card as card_repo
from app.repositories import kb as kb_repo
from app.schemas.card import (
    BatchItemOut,
    BatchJobOut,
    BatchUploadOut,
    CardConfirmOut,
    CardDetailOut,
    CardListOut,
    CardOut,
    CardUpdateIn,
    CardUploadOut,
)
from app.services import card_batch, kb, llm, normalize, ocr, translate, user_credentials
from app.services import image as image_service
from app.services.embeddings import EmbeddingError

logger = logging.getLogger(__name__)

router = APIRouter()

#: Đọc từng khối 1MB để giữ RAM phẳng và để dừng được ngay khi vượt ngưỡng.
CHUNK_SIZE = 1024 * 1024

#: Số bản ghi mỗi trang khi UI không nói gì (task 4.1).
DEFAULT_PAGE_SIZE = 20

#: Bộ lọc trạng thái hợp lệ của ô select trên `templates/cards/list.html`.
STATUS_CHOICES: tuple[str, ...] = tuple(s.value for s in CardStatus)

#: Trần số ảnh một lượt batch (task 5.2). Không phải giới hạn kỹ thuật mà là giới hạn về sự
#: kiên nhẫn: 50 ảnh × ~4s / 2 luồng đã là hơn 1 phút rưỡi ngồi nhìn thanh tiến trình.
MAX_BATCH_FILES = 50

#: Điểm `confidence` dưới mức này thì `templates/cards/detail.html` tô vàng (task 5.1). 0.75 đo
#: từ kết quả thật ở task 3.4: chữ rõ model chấm 0.9–1.0, chỗ mờ tụt hẳn xuống 0.5–0.7 — đặt
#: ngưỡng ở giữa thì vàng nghĩa là "đáng kiểm", không phải "vàng cả thẻ nên thôi kệ".
LOW_CONFIDENCE = 0.75


# --------------------------------------------------------------------------- trang HTML


@router.get("/cards", response_class=HTMLResponse, tags=["ui"])
async def cards_page(request: Request) -> HTMLResponse:
    """Màn hình danh sách danh thiếp (task 4.4).

    Trang render rỗng rồi để JavaScript gọi `GET /api/cards` đổ dữ liệu vào — cùng lối với
    `/settings` (task 2.5): tìm kiếm và lọc phải chạy được mà không tải lại trang, và DB chết
    thì trang vẫn mở được để hiện đúng lý do.
    """
    return templates.TemplateResponse(
        request,
        "cards/list.html",
        {
            "active_nav": "cards",
            "status_choices": STATUS_CHOICES,
            "default_page_size": DEFAULT_PAGE_SIZE,
        },
    )


@router.get("/cards/upload", response_class=HTMLResponse, tags=["ui"])
async def cards_upload_page(request: Request) -> HTMLResponse:
    """Màn hình quét danh thiếp — **một ảnh hay ba mươi ảnh đều vào đây** (task 4.5, 5.3, I-38).

    Gộp từ hai màn hình cũ. Chúng chỉ khác nhau ở số ảnh người dùng định chọn, mà đó là thứ họ
    chưa biết cho tới khi mở hộp chọn file ra — bắt chọn trước là bắt trả lời một câu hỏi của hệ
    thống chứ không phải của họ.
    """
    return templates.TemplateResponse(
        request,
        "cards/upload.html",
        {
            "active_nav": "cards",
            "max_upload_mb": settings.max_upload_mb,
            "max_batch_files": MAX_BATCH_FILES,
            "max_concurrency": card_batch.MAX_CONCURRENCY,
        },
    )


@router.get("/cards/batch", tags=["ui"])
async def cards_batch_page() -> RedirectResponse:
    """`/cards/batch` gộp vào `/cards/upload` — chuyển hướng **301** (I-38).

    **Phải khai trước `/cards/{card_id}`.** Starlette so đường dẫn theo thứ tự khai báo, đặt sau
    thì `/cards/batch` rơi vào route chi tiết, `batch` không parse được thành UUID và người dùng
    nhận 422 thay vì trang này.

    301 chứ không xoá thẳng, cùng lý do với `/dashboard` (14.6) và `/assistant` (EX-09): đường
    dẫn này nằm trong `docs/user-guide.md`, `docs/demo-runbook.md` và trong trang đánh dấu của
    hai người đã dùng nó suốt ba ngày. Xoá là 404 ngay giữa buổi demo.
    """
    return RedirectResponse("/cards/upload", status_code=status.HTTP_301_MOVED_PERMANENTLY)


@router.get("/cards/{card_id}", response_class=HTMLResponse, tags=["ui"])
async def card_detail_page(request: Request, card_id: uuid.UUID) -> HTMLResponse:
    """Màn hình review một danh thiếp: ảnh trái, form phải (task 5.1).

    Route **không** đọc DB, chỉ truyền `card_id` xuống template rồi để JavaScript gọi
    `GET /api/cards/{id}` — cùng lối với `/cards` (task 4.4) và `/settings` (task 2.5). Lý do
    không phải là lười: sau mỗi lần Lưu, server chuẩn hoá lại SĐT/email và trả bản đã chuẩn hoá
    về, trang phải vẽ lại từ đúng payload đó. Render sẵn từ Jinja thì trang có hai nguồn sự
    thật — bản lúc mở trang và bản sau khi lưu — và chúng sẽ lệch nhau ngay lần sửa đầu tiên.
    """
    return templates.TemplateResponse(
        request,
        "cards/detail.html",
        {
            "active_nav": "cards",
            "card_id": str(card_id),
            "low_confidence": LOW_CONFIDENCE,
        },
    )


# --------------------------------------------------------------------------- API


@router.post(
    "/api/cards/upload",
    tags=["cards"],
    response_model=CardUploadOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload 1 ảnh danh thiếp, quét và lưu",
)
async def upload_card(
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: CurrentUser,
    workspace: WriterWorkspace,
    file: Annotated[UploadFile, File(description="Ảnh danh thiếp: JPEG/PNG/WEBP/BMP/TIFF/GIF")],
) -> CardUploadOut:
    """Nhận 1 ảnh → tiền xử lý → gọi Gemini Vision → lưu `business_cards`.

    Trả **201** cho ảnh mới, **200** khi ảnh đã được quét trước đó (`duplicate=true`, trả lại
    đúng bản ghi cũ và không gọi lại model — tiêu chí hoàn thành D3).
    """
    started = time.perf_counter()
    raw = await _read_limited(file)
    image_hash = hashlib.sha256(raw).hexdigest()

    existing = await card_repo.get_by_hash(db, image_hash, workspace_id=workspace.id)
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        logger.info("Ảnh đã quét trước đó, trả lại card %s", existing.id)
        return CardUploadOut(
            card=CardOut.model_validate(existing),
            duplicate=True,
            elapsed_ms=_ms_since(started),
        )

    try:
        processed = image_service.preprocess(raw)
    except image_service.ImageError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    relative_path = _store(processed.data, image_hash)

    # --- OCR: hỏng thì ghi lại lý do chứ không huỷ lượt upload (xem docstring đầu file) ---
    ocr_result: ocr.OcrResult | None = None
    ocr_error: str | None = None
    try:
        ocr_result = await ocr.extract_and_translate(
            processed.data,
            mime_type=processed.mime_type,
            model=await user_credentials.model_for(db, user, "ocr"),
        )
    except (llm.LLMError, ocr.OcrError) as exc:
        ocr_error = str(exc)
        logger.warning("Quét ảnh %s thất bại: %s", relative_path, exc)

    fields = ocr_result.card_fields() if ocr_result else {}
    card_status, notes = ocr.status_and_notes(ocr_result, ocr_error)

    try:
        card = await card_repo.create_card(
            db,
            workspace_id=workspace.id,
            user_id=user.id,
            image_path=relative_path,
            image_hash=image_hash,
            fields=fields,
            ocr_raw_json=ocr_result.raw_json if ocr_result else None,
            status=card_status,
            notes=notes,
        )
    except card_repo.DuplicateImageError:
        # Hai lượt upload cùng một ảnh chạy song song; lượt kia đã ghi xong (task 5.2).
        raced = await card_repo.get_by_hash(db, image_hash, workspace_id=workspace.id)
        if raced is None:  # không xảy ra trên PostgreSQL, nhưng đừng trả None cho client
            raise
        response.status_code = status.HTTP_200_OK
        return CardUploadOut(
            card=CardOut.model_validate(raced), duplicate=True, elapsed_ms=_ms_since(started)
        )

    elapsed_ms = _ms_since(started)
    logger.info(
        "Card %s: %s %dx%d → JPEG %d byte, OCR %s, tổng %dms",
        card.id,
        processed.source_format,
        processed.source_width,
        processed.source_height,
        processed.size_bytes,
        f"{ocr_result.elapsed_ms}ms" if ocr_result else f"lỗi ({ocr_error})",
        elapsed_ms,
    )
    return CardUploadOut(
        card=CardOut.model_validate(card),
        duplicate=False,
        missing_required=ocr_result.extraction.missing_required() if ocr_result else [],
        ocr_ms=ocr_result.elapsed_ms if ocr_result else None,
        ocr_error=ocr_error,
        elapsed_ms=elapsed_ms,
    )


@router.post(
    "/api/cards/batch-upload",
    tags=["cards"],
    response_model=BatchUploadOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload nhiều ảnh danh thiếp, quét ở nền",
)
async def batch_upload_cards(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: CurrentUser,
    workspace: WriterWorkspace,
    files: Annotated[list[UploadFile], File(description="Nhiều ảnh danh thiếp")],
) -> BatchUploadOut:
    """Nhận nhiều ảnh → lưu hết ngay → trả `job_id`, việc quét chạy nền (task 5.2).

    Ranh giới giữa "làm ngay" và "làm sau" đặt ở chỗ **mất thì có lấy lại được không**: file
    người dùng vừa chọn chỉ tồn tại trong request này, nên đọc–băm–nén–ghi volume–tạo bản ghi
    `pending` đều làm ngay tại đây. Gọi vision mới đẩy ra nền, vì đó là phần mất 3–5 giây/ảnh và
    là phần duy nhất chạm rate limit. Chi tiết hàng đợi: `app/services/card_batch.py`.

    **Một ảnh hỏng không làm hỏng cả lượt.** Ảnh quá dung lượng hay không phải file ảnh chỉ làm
    hỏng đúng dòng của nó và lý do nằm ngay trong `items[]`; trả 4xx cho cả request là bắt người
    dùng chọn lại 29 ảnh còn lại chỉ vì một file lỡ tay.
    """
    if not files:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Chưa chọn ảnh nào.")
    if len(files) > MAX_BATCH_FILES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Tối đa {MAX_BATCH_FILES} ảnh một lượt, lượt này có {len(files)}.",
        )

    started = time.perf_counter()
    items = [
        await _stage(db, upload, workspace_id=workspace.id, user_id=user.id) for upload in files
    ]

    job = card_batch.create_job(items, workspace_id=workspace.id, user_id=user.id)
    card_batch.start(job)

    queued = sum(1 for item in items if item.status is card_batch.ItemStatus.PENDING)
    duplicates = sum(1 for item in items if item.duplicate)
    rejected = sum(1 for item in items if item.status is card_batch.ItemStatus.ERROR)
    logger.info(
        "Batch job %s: nhận %d ảnh trong %dms — %d xếp hàng, %d trùng, %d bị từ chối",
        job.id,
        len(items),
        _ms_since(started),
        queued,
        duplicates,
        rejected,
    )
    return BatchUploadOut(
        job_id=job.id,
        total=len(items),
        queued=queued,
        duplicates=duplicates,
        rejected=rejected,
    )


@router.get("/api/cards/batch-jobs/{job_id}", response_model=BatchJobOut, tags=["cards"])
async def get_batch_job(
    job_id: uuid.UUID, user: CurrentUser, workspace: CurrentWorkspace
) -> BatchJobOut:
    """Tiến trình một lượt batch — `templates/cards/upload.html` poll endpoint này (task 5.3).

    Job của **không gian khác** trả 404 y như job không tồn tại (task 12.5, đổi khoá ở
    `NEXT-05`): phân biệt hai ca đó là tự xác nhận "có một lượt quét mang id này, chỉ không phải
    của bạn". Đối chiếu theo không gian chứ không theo người bấm nút: từ `NEXT-05` danh thiếp
    quét ra thuộc về tổ chức, nên đồng nghiệp phải theo dõi được tiến trình của chính lô ấy.
    """
    job = card_batch.get_job(job_id)
    if job is not None and job.workspace_id != workspace.id:
        job = None
    if job is None:
        # Nói thẳng job sống trong bộ nhớ: 404 trơ ở đây đọc như "ID sai", trong khi nguyên nhân
        # thật thường là container vừa restart (`--reload` nạp lại khi sửa code) — hai việc phải
        # làm tiếp hoàn toàn khác nhau.
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=(
                "Không còn theo dõi được lượt quét này. Job nằm trong bộ nhớ tiến trình nên mất "
                "khi `api` khởi động lại hoặc sau 20 lượt gần nhất. Ảnh vẫn an toàn — mở /cards "
                "để xem, thẻ nào chưa quét được sẽ ở trạng thái “Chưa quét được” kèm lý do."
            ),
        )
    return _job_out(job)


@router.get("/api/cards", response_model=CardListOut, tags=["cards"])
async def list_cards(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: CurrentUser,
    workspace: CurrentWorkspace,
    q: Annotated[str | None, Query(description="Tìm trong họ tên / tên công ty / email")] = None,
    card_status: Annotated[
        str | None, Query(alias="status", description="pending | needs_review | confirmed")
    ] = None,
    company_id: Annotated[uuid.UUID | None, Query()] = None,
    language: Annotated[
        str | None, Query(description="Mã ISO 639-1: en | vi | ko | ja | zh")
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=card_repo.MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> CardListOut:
    """Danh sách danh thiếp, có phân trang / tìm kiếm / lọc (task 4.1).

    Trạng thái lạ trả **400** chứ không trả danh sách rỗng: rỗng đọc như "chưa có danh thiếp
    nào", người dùng sẽ đi tìm lỗi ở chỗ upload trong khi thực ra chỉ gõ sai bộ lọc.
    """
    if card_status is not None and card_status not in STATUS_CHOICES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"status phải là một trong {', '.join(STATUS_CHOICES)}.",
        )

    rows, total = await card_repo.list_cards(
        db,
        workspace_id=workspace.id,
        q=q,
        status=card_status,
        company_id=company_id,
        language=language,
        page=page,
        size=size,
    )
    return CardListOut(
        items=[CardOut.model_validate(row) for row in rows],
        total=total,
        page=page,
        size=size,
        # Trang cuối tính từ tổng, không từ `len(items)`: trang rỗng ở cuối vẫn phải biết còn
        # bao nhiêu trang để nút "về trang trước" của UI không dẫn vào hư không.
        pages=max(1, -(-total // size)),
    )


@router.get("/api/cards/{card_id}", response_model=CardDetailOut, tags=["cards"])
async def get_card(
    card_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: CurrentUser,
    workspace: CurrentWorkspace,
) -> CardDetailOut:
    """Chi tiết một danh thiếp, kèm `ocr_raw_json` để đối chiếu khi nghi OCR sai (task 4.2)."""
    card = await _get_or_404(db, card_id, workspace_id=workspace.id)
    return CardDetailOut.model_validate(card)


@router.get("/api/cards/{card_id}/image", tags=["cards"])
async def get_card_image(
    card_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: CurrentUser,
    workspace: CurrentWorkspace,
) -> FileResponse:
    """Ảnh đã tiền xử lý của một danh thiếp — UI dùng làm thumbnail (4.4) và ảnh gốc (5.1).

    Ảnh nằm trong volume `uploads`, **không** nằm dưới `/static`: mount cả thư mục upload ra
    static là công khai toàn bộ danh thiếp cho bất kỳ ai đoán được tên file (tên file là hash,
    nhưng hash nằm sẵn trong mọi response `CardDetail`). Đi qua endpoint này thì về sau thêm
    kiểm tra quyền chỉ phải sửa một chỗ.
    """
    card = await _get_or_404(db, card_id, workspace_id=workspace.id)
    # `image_path` cho phép `NULL` từ revision `0013`, và `0014` giữ nguyên dù `NEXT-08` đã
    # bị cắt — xem lý do ở `models/card.py`. Nói thẳng ra thay vì để `_resolve_image(None)`
    # nổ kiểu.
    if card.image_path is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="Bản ghi này không có ảnh gốc.",
        )
    path = _resolve_image(card.image_path)
    if path is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="Bản ghi còn nhưng file ảnh không còn trong volume uploads.",
        )
    return FileResponse(path, media_type="image/jpeg")


@router.patch("/api/cards/{card_id}", response_model=CardDetailOut, tags=["cards"])
async def update_card(
    card_id: uuid.UUID,
    payload: CardUpdateIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: CurrentUser,
    workspace: WriterWorkspace,
) -> CardDetailOut:
    """Sửa tay các trường sau khi review (task 4.2).

    Giá trị người dùng gõ vẫn đi qua `services/normalize.py` — gõ `0912 345 678` thì lưu
    `+84912345678`, đúng như lúc quét. Không chuẩn hoá ở đây thì DB có hai kiểu số khác nhau tuỳ
    theo trường đó do model đọc hay do người sửa, và mọi thứ so khớp về sau đều vấp.

    **Cố ý không đụng `status`.** Sửa nội dung không phải là xác nhận — chuyển sang `confirmed`
    là việc của `POST /{id}/confirm` (task 4.3), nơi mới có bước gắn công ty.
    """
    card = await _get_or_404(db, card_id, workspace_id=workspace.id)

    changes = payload.changes()
    if not changes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Không có trường nào để sửa.")

    edited = sorted(changes)
    language = changes.get("language_detected", card.language_detected)

    # `notes` không nằm trong `OCR_COLUMNS` (danh sách trắng của repository) nên phải gán tay.
    # Gán TRƯỚC khi gọi `update_fields` để cả hai thay đổi đi chung một commit — tách ra thì một
    # lần PATCH có thể ghi được nửa này mà hỏng nửa kia.
    if "notes" in changes:
        notes = changes.pop("notes")
        card.notes = normalize.squash_spaces(notes) if notes is not None else None

    fields = _normalize_edits(changes, language=language)
    fields.update(_translation_meta_after_edit(card, changes))
    card = await card_repo.update_fields(db, card, fields)

    # Thẻ **đã xác nhận** thì nó đang nằm trong KB, và KB vừa lệch với DB. Index lại ngay ở đây
    # thay vì chờ ai đó bấm `POST /api/kb/reindex`: sửa sai một số điện thoại rồi vẫn nghe trợ
    # lý đọc số cũ là lỗi không ai nghĩ tới việc đi tìm. Thẻ chưa xác nhận thì bỏ qua — nó chưa
    # bao giờ vào KB (`INDEXABLE_CARD_STATUSES`), và luồng review bấm Lưu liên tục.
    if card.status == CardStatus.CONFIRMED:
        await _sync_kb(db, card)

    logger.info("Card %s: sửa tay %s", card.id, ", ".join(edited))
    return CardDetailOut.model_validate(card)


@router.post("/api/cards/{card_id}/translate", response_model=CardDetailOut, tags=["cards"])
async def translate_card(
    card_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: CurrentUser,
    workspace: WriterWorkspace,
) -> CardDetailOut:
    """Việt hoá lại danh thiếp — nút *Dịch lại* ở màn hình review (task EX-04).

    Chạy trên **giá trị đang nằm trong DB**, tức là đã gồm cả những chỗ người dùng vừa sửa tay.
    Đó là toàn bộ lý do có endpoint này: sửa `full_name` từ `田中 太朗` thành `田中 太郎` mà bản
    phiên âm vẫn là bản dịch của chữ cũ thì tệ hơn là không có bản phiên âm nào.

    Khác với lượt Việt hoá tự động sau khi quét, ở đây lỗi **được báo ra** (503/502): người dùng
    vừa bấm nút và đang đợi, im lặng nuốt lỗi thì nút trông như hỏng.
    """
    card = await _get_or_404(db, card_id, workspace_id=workspace.id)
    source = {name: getattr(card, name, None) for name in translate.prompts.TRANSLATABLE_FIELDS}

    try:
        translation = await translate.translate_card(
            source,
            language=card.language_detected,
            model=await user_credentials.model_for(db, user, "ocr"),
            raise_on_error=True,
        )
    except llm.LLMNotConnectedError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except (llm.LLMError, translate.TranslationError) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    card = await card_repo.update_fields(db, card, translation.columns())

    # Thẻ đã xác nhận thì đang nằm trong KB, mà `services/kb.py` có in bản Việt hoá vào chunk
    # (EX-06) — cùng lý lẽ với nhánh tương tự ở `update_card()`.
    if card.status == CardStatus.CONFIRMED:
        await _sync_kb(db, card)

    logger.info("Card %s: Việt hoá lại (%s)", card.id, translation.meta.get("source"))
    return CardDetailOut.model_validate(card)


@router.delete("/api/cards/{card_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["cards"])
async def delete_card(
    card_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: CurrentUser,
    workspace: WriterWorkspace,
) -> Response:
    """Xoá một danh thiếp và file ảnh của nó (task 4.2).

    Xoá hàng trước, xoá file sau. Ngược lại thì transaction hỏng sẽ để lại một bản ghi trỏ vào
    file đã mất — hỏng theo kiểu im lặng, chỉ lộ ra khi có người mở đúng bản ghi đó. Xoá file
    hỏng thì chỉ ghi log: file thừa nằm lại trong volume không làm hỏng gì.
    """
    card = await _get_or_404(db, card_id, workspace_id=workspace.id)
    image_path = card.image_path

    # Gỡ khỏi KB **trong cùng transaction** với việc xoá hàng (`delete_for_source` không commit,
    # `delete_card` commit cả hai). Bỏ bước này thì chunk mồ côi ở lại và trợ lý vẫn trích dẫn
    # một danh thiếp đã xoá — dữ liệu người dùng tưởng đã xoá mà vẫn trả lời ra được.
    await kb_repo.delete_for_source(
        db, workspace_id=workspace.id, source_type=KBSourceType.CARD, source_id=card.id
    )

    await card_repo.delete_card(db, card)

    path = _resolve_image(image_path) if image_path is not None else None
    if path is not None:
        try:
            path.unlink()
        except OSError as exc:
            logger.warning("Xoá được bản ghi %s nhưng không xoá được %s: %s", card_id, path, exc)

    logger.info("Đã xoá card %s", card_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/cards/{card_id}/confirm", response_model=CardConfirmOut, tags=["cards"])
async def confirm_card(
    card_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: CurrentUser,
    workspace: WriterWorkspace,
) -> CardConfirmOut:
    """Người dùng duyệt xong → `confirmed` + gắn `company_id` (task 4.3).

    **Không kích hoạt enrich.** Hồ sơ doanh nghiệp chỉ sinh khi người dùng tích chọn công ty rồi
    bấm nút ở màn hình Doanh nghiệp (Plan.md mục 2.2, Luồng 2). Xác nhận danh thiếp chỉ tạo ra
    một bản ghi *tên công ty*, chưa phải hồ sơ.

    Gắn công ty **không chặn xác nhận**: danh thiếp không đọc được tên công ty, hoặc
    `company_matching.upsert_company()` (task 3.8 của T) chưa có, thì bản ghi vẫn về `confirmed`
    và `detail` nói rõ vì sao chưa gắn. Chặn lại sẽ khiến toàn bộ luồng F1 đứng chờ một task của
    người khác.
    """
    card = await _get_or_404(db, card_id, workspace_id=workspace.id)

    company_id = card.company_id
    detail: str | None = None

    if company_id is not None:
        detail = "Danh thiếp đã được gắn công ty từ trước."
    elif not (raw_name := (card.company_name_raw or "").strip()):
        detail = "Danh thiếp không có tên công ty nên chưa gắn được vào bảng companies."
    else:
        company_id, detail = await _upsert_company(
            db,
            raw_name,
            workspace_id=workspace.id,
            user_id=user.id,
            # `I-36`: bản Việt hoá đã có sẵn trên thẻ từ `EX-02`, chép thẳng sang công ty thay
            # vì để danh sách `/companies` hiện chữ Hàn/Trung/Ả Rập.
            display_name_vi=card.company_name_vi,
            email=card.email,
            website=card.website,
        )

    card.company_id = company_id
    card.status = CardStatus.CONFIRMED
    await db.commit()
    await db.refresh(card)

    # Task 7.3 — xác nhận xong là vào Knowledge Base ngay (Luồng 1, Plan.md mục 2.2 bước 7–8).
    indexed, kb_detail = await _sync_kb(db, card)
    detail = " ".join(part for part in (detail, kb_detail) if part) or None

    logger.info("Card %s → confirmed (company_id=%s, kb=%s)", card.id, company_id, indexed)
    return CardConfirmOut(
        id=card.id,
        status=card.status,
        company_id=company_id,
        company_matched=company_id is not None,
        kb_indexed=indexed,
        detail=detail,
    )


# --------------------------------------------------------------------------- nội bộ


async def _stage(
    db: AsyncSession, upload: UploadFile, *, workspace_id: uuid.UUID, user_id: uuid.UUID
) -> card_batch.BatchItem:
    """Một file trong lượt batch: đọc → băm → chống trùng → nén → ghi volume → bản ghi `pending`.

    Trả về `BatchItem` **ở mọi nhánh**, kể cả nhánh hỏng: hàng đợi cần một dòng cho mỗi file
    người dùng đã chọn, nếu không thì file bị loại sẽ biến mất khỏi giao diện và người dùng ngồi
    đếm "mình chọn 12 ảnh sao chỉ thấy 11".

    Đây là phần **đồng bộ** của batch nên nó chặn event loop trong lúc chạy: Pillow nén một ảnh
    mất khoảng 100ms, 50 ảnh là ~5 giây. Chấp nhận được vì trong 5 giây đó client duy nhất đang
    chờ chính là request này — trang `/cards/upload` chỉ bắt đầu poll sau khi nhận được 202.
    """
    filename = (upload.filename or "").strip() or "(không có tên file)"

    try:
        raw = await _read_limited(upload)
    except HTTPException as exc:
        # Bắt lại chính lỗi mình vừa ném ra thay vì chép lại luật giới hạn dung lượng lần thứ
        # hai: upload 1 ảnh trả 4xx là đúng, còn ở đây cùng một luật phải thành một dòng lỗi.
        return card_batch.BatchItem(
            filename=filename, status=card_batch.ItemStatus.ERROR, error=str(exc.detail)
        )

    image_hash = hashlib.sha256(raw).hexdigest()
    if (
        existing := await card_repo.get_by_hash(db, image_hash, workspace_id=workspace_id)
    ) is not None:
        return card_batch.BatchItem(
            filename=filename,
            workspace_id=workspace_id,
            card_id=existing.id,
            status=card_batch.ItemStatus.DONE,
            duplicate=True,
        )

    try:
        processed = image_service.preprocess(raw)
    except image_service.ImageError as exc:
        return card_batch.BatchItem(
            filename=filename, status=card_batch.ItemStatus.ERROR, error=str(exc)
        )

    relative_path = _store(processed.data, image_hash)

    try:
        card = await card_repo.create_card(
            db,
            workspace_id=workspace_id,
            user_id=user_id,
            image_path=relative_path,
            image_hash=image_hash,
            status=CardStatus.PENDING,
            notes="Đang xếp hàng chờ quét (upload hàng loạt).",
        )
    except card_repo.DuplicateImageError:
        # Cùng một ảnh nằm hai lần trong chính lượt này: lượt trước đã commit xong.
        raced = await card_repo.get_by_hash(db, image_hash, workspace_id=workspace_id)
        if raced is None:  # không xảy ra trên PostgreSQL
            raise
        return card_batch.BatchItem(
            filename=filename,
            workspace_id=workspace_id,
            card_id=raced.id,
            status=card_batch.ItemStatus.DONE,
            duplicate=True,
        )

    return card_batch.BatchItem(
        filename=filename,
        workspace_id=workspace_id,
        card_id=card.id,
        image_path=settings.upload_dir / relative_path,
        status=card_batch.ItemStatus.PENDING,
    )


def _job_out(job: card_batch.BatchJob) -> BatchJobOut:
    """`BatchJob` (dataclass trong bộ nhớ) → payload JSON.

    Ánh xạ tay chứ không `model_validate(from_attributes=True)`: `BatchItem.image_path` là đường
    dẫn nội bộ của container, tự động hoá bước này là tự mở đường cho nó rò ra API vào một ngày
    nào đó mà không ai để ý.
    """
    return BatchJobOut(
        job_id=job.id,
        total=job.total,
        done=job.done,
        failed=job.failed,
        running=job.running,
        finished=job.finished,
        aborted_reason=job.aborted_reason,
        items=[
            BatchItemOut(
                filename=item.filename,
                status=item.status.value,
                card_id=item.card_id,
                duplicate=item.duplicate,
                error=item.error,
                attempts=item.attempts,
                ocr_ms=item.ocr_ms,
            )
            for item in job.items
        ],
    )


async def _get_or_404(
    db: AsyncSession, card_id: uuid.UUID, *, workspace_id: uuid.UUID
) -> BusinessCard:
    """Danh thiếp của **người đang đăng nhập**, hoặc 404.

    Thẻ của người khác và thẻ không tồn tại trả **cùng một** 404 với **cùng một câu**
    (task 12.5, Plan.md mục 4): khác nhau ở đâu — mã, câu chữ, hay thời gian phản hồi —
    là còn một kênh để dò xem id nào có thật.
    """
    card = await card_repo.get(db, card_id, workspace_id=workspace_id)
    if card is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Không có danh thiếp này.")
    return card


def _normalize_edits(changes: dict[str, Any], *, language: str | None) -> dict[str, Any]:
    """Chuẩn hoá đúng những trường người dùng vừa sửa, **từng trường một**.

    Cố ý không gọi `normalize_card_fields()` như lúc quét: hàm đó xử lý `phone` và `phone_alt`
    như một cặp và luôn ghi lại cả hai, nên PATCH chỉ gửi `phone` sẽ xoá mất `phone_alt` đang có.
    Ở màn hình review, người dùng sửa ô nào thì chỉ ô đó được đổi.
    """
    region = normalize.region_for_language(language)
    out: dict[str, Any] = {}

    for name, value in changes.items():
        if value is None:
            out[name] = None
        elif name == "email":
            out[name] = normalize.normalize_email(value)
        elif name == "website":
            out[name] = normalize.normalize_website(value)
        elif name in ("phone", "phone_alt"):
            out[name] = normalize.normalize_phone(value, region=region)
        elif name == "language_detected":
            out[name] = (normalize.squash_spaces(value) or "").lower() or None
        else:
            out[name] = normalize.squash_spaces(value)

    return out


def _translation_meta_after_edit(card: BusinessCard, changes: dict[str, Any]) -> dict[str, Any]:
    """`translation_meta` mới sau một lượt sửa tay. Rỗng nghĩa là không đụng tới cột đó.

    Hai ca, và chúng loại trừ nhau:

    * người dùng **sửa thẳng một ô Việt hoá** → `source="manual"`, hết lỗi thời. Bản của người
      cầm tấm thẻ trong tay luôn thắng bản của model;
    * người dùng **sửa trường gốc** (tên, chức vụ, công ty, địa chỉ) → bản dịch cũ nay nói về
      chữ khác, đánh dấu `stale=True` để giao diện mời bấm *Dịch lại*.

    Cố ý **không tự gọi model ở đây**: màn hình review bấm Lưu liên tục, mỗi lần Lưu kéo theo
    một lời gọi LLM là biến thao tác sửa một chữ thành ba giây chờ. Dựng dict mới chứ không sửa
    tại chỗ — JSONB không được SQLAlchemy theo dõi thay đổi bên trong, sửa tại chỗ là ghi hụt.
    """
    edited_vi = [name for name in changes if name.endswith("_vi")]
    edited_source = [name for name in changes if name in translate.prompts.TRANSLATABLE_FIELDS]
    if not edited_vi and not edited_source:
        return {}

    meta = dict(card.translation_meta or {})
    if edited_vi:
        meta["source"] = "manual"
        meta["stale"] = False
        meta["edited_fields"] = sorted(set(meta.get("edited_fields", [])) | set(edited_vi))
    else:
        meta["stale"] = True
    return {"translation_meta": meta}


async def _sync_kb(db: AsyncSession, card: BusinessCard) -> tuple[bool, str | None]:
    """Đưa một danh thiếp vào Knowledge Base (task 7.3). Trả `(đã index?, lý do nếu không)`.

    **Không bao giờ làm hỏng thao tác gọi nó**, cùng lý lẽ với `_upsert_company()`: xác nhận danh
    thiếp là việc của F1 và phải xong được kể cả khi F3 đang hỏng. Service `embedder` chưa lên
    (máy vừa `docker compose up`, model còn đang nạp) là chuyện thường ngày; để nó chặn nút Xác
    nhận thì cả luồng nhập liệu đứng vì một thứ chỉ phục vụ trợ lý AI. Bù lại luôn có
    `POST /api/kb/reindex` để vá sau, nên mất một lượt index không mất dữ liệu.

    **Chạy đồng bộ, không đẩy sang `BackgroundTasks`.** Một danh thiếp là 1–2 chunk, nhúng mất
    vài chục mili giây trên CPU — rẻ hơn hẳn lời gọi OCR mà chính người dùng này vừa chờ. Đổi lại
    thì `kb_indexed` trong response là sự thật đã xảy ra, không phải lời hứa.

    ⚠️ **`rollback()` rồi phải `refresh()` ngay.** `Session.rollback()` làm **mọi** object ORM
    hết hạn, không phụ thuộc `expire_on_commit` — chỗ gọi đọc `card.…` sau đó là một lượt nạp
    lại đồng bộ giữa hàm async, tức `MissingGreenlet` và HTTP 500. Đúng cái mà hàm này sinh ra
    để tránh: embedder chết mà vẫn làm hỏng nút Xác nhận. Bắt được khi viết test 7.3; cùng họ
    với lỗi đã ghi ở `routers/kb.py::_reindex_cards`.
    """
    card_id = card.id  # đọc trước: từ đây trở đi `card` có thể hết hạn bất cứ lúc nào
    try:
        written = await kb.ingest_card(db, card)
    except EmbeddingError as exc:
        await _rollback_and_refresh(db, card)
        logger.warning("Card %s: không index được vào KB: %s", card_id, exc)
        return False, (
            "Chưa đưa được vào cơ sở tri thức của trợ lý AI (service embedder không sẵn sàng). "
            "Bấm Index lại ở trang Trợ lý AI sau là xong."
        )
    except Exception:  # noqa: BLE001 — xem docstring: F3 hỏng không được chặn F1
        await _rollback_and_refresh(db, card)
        logger.exception("Card %s: lỗi ngoài dự kiến khi index vào KB", card_id)
        return False, "Chưa đưa được vào cơ sở tri thức của trợ lý AI (lỗi nội bộ, đã ghi log)."

    if written == 0:
        # `services/kb.py::_to_chunks()` trả rỗng khi thẻ không còn trường nào có nội dung.
        logger.info("Card %s: không có nội dung nào để index", card_id)
        return False, None
    return True, None


async def _rollback_and_refresh(db: AsyncSession, card: BusinessCard) -> None:
    """Huỷ phần ghi dở của KB rồi nạp lại `card` để chỗ gọi dùng tiếp được.

    Bản thân bản ghi danh thiếp đã commit từ trước, nên `refresh()` chỉ là một câu `SELECT` trên
    đường lỗi. Rẻ hơn nhiều so với việc để chỗ gọi cầm một object hết hạn.
    """
    await db.rollback()
    await db.refresh(card)


async def _upsert_company(
    db: AsyncSession,
    raw_name: str,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    display_name_vi: str | None = None,
    email: str | None = None,
    website: str | None = None,
) -> tuple[uuid.UUID | None, str | None]:
    """Gọi `company_matching.upsert_company()` của T — chữ ký chốt ở họp D2 (`docs/api.md` mục 8).

    **`email` và `website` là bắt buộc phải truyền, không phải tuỳ chọn cho đẹp** (I-18). Quy tắc
    gộp công ty của task 3.8 đọc tên miền từ hai trường này và nó cắt theo **cả hai chiều**:
    chung tên miền thì nới ngưỡng so mờ xuống 80, còn **khác tên miền thì không bao giờ gộp**.
    Gọi thiếu hai tham số này — như bản đầu của 4.3 — thì `extract_domains(None, None)` trả rỗng,
    và toàn bộ luồng xác nhận trên UI chỉ còn so tên: "Công ty TNHH Phú Cơ" gặp "Công ty TNHH
    Phú" là gộp (I-21), trong khi hai công ty khác hẳn nhau nhưng trùng tên miền lại không gộp
    được. Nói cách khác lưới an toàn của T có tồn tại mà chưa bao giờ được bật từ giao diện.

    Import **trong hàm** chứ không ở đầu file: `services/company_matching.py` còn là stub cho tới
    khi T làm xong task 3.8, import ở module level sẽ không sao (module tồn tại), nhưng gọi hàm
    chưa có thì `AttributeError` ném ra giữa request. Kiểm bằng `getattr` để phân biệt rõ "T
    chưa làm" với "gọi được nhưng hỏng", và để câu thông báo nói đúng việc cần làm.
    """
    from app.services import company_matching

    upsert = getattr(company_matching, "upsert_company", None)
    if upsert is None:
        logger.info("company_matching.upsert_company() chưa có (task 3.8 của T) — bỏ qua bước gắn")
        return None, (
            "Đã xác nhận. Chưa gắn được công ty vì `company_matching.upsert_company()` "
            "(task 3.8, chủ sở hữu T) chưa triển khai — gắn lại được sau khi task đó xong."
        )

    try:
        return await upsert(
            db,
            raw_name,
            workspace_id=workspace_id,
            user_id=user_id,
            display_name_vi=display_name_vi,
            email=email,
            website=website,
        ), None
    except Exception as exc:  # T sở hữu hàm này; lỗi của nó không được làm hỏng việc xác nhận
        await db.rollback()
        logger.warning("upsert_company(%r) lỗi: %s", raw_name, exc)
        return None, f"Đã xác nhận, nhưng gắn công ty thất bại: {exc}"


def _resolve_image(image_path: str) -> Path | None:
    """Đường dẫn tuyệt đối của ảnh trong volume, hoặc `None` nếu không còn.

    DB lưu đường dẫn **tương đối** (xem `_store`). Ghép xong phải kiểm lại nó thật sự nằm trong
    `UPLOAD_DIR`: giá trị hiện tại do chính `_store` sinh ra nên an toàn, nhưng một bản ghi cũ
    hoặc dữ liệu seed chứa `../` sẽ biến endpoint ảnh thành đường đọc trộm file của container.
    """
    root = settings.upload_dir.resolve()
    try:
        target = (root / image_path).resolve()
        target.relative_to(root)
    except (OSError, ValueError):
        logger.warning("image_path %r nằm ngoài UPLOAD_DIR, từ chối phục vụ", image_path)
        return None
    return target if target.is_file() else None


async def _read_limited(file: UploadFile) -> bytes:
    """Đọc file, dừng và trả 413 ngay khi vượt `MAX_UPLOAD_MB`."""
    limit = settings.max_upload_mb * 1024 * 1024
    chunks: list[bytes] = []
    size = 0

    while chunk := await file.read(CHUNK_SIZE):
        size += len(chunk)
        if size > limit:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Ảnh vượt quá giới hạn {settings.max_upload_mb}MB.",
            )
        chunks.append(chunk)

    if not chunks:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="File rỗng.")
    return b"".join(chunks)


def _store(data: bytes, image_hash: str) -> str:
    """Ghi ảnh đã xử lý vào volume `uploads`, trả **đường dẫn tương đối** để lưu DB.

    Tương đối chứ không tuyệt đối: `UPLOAD_DIR` là biến môi trường (`/data/uploads` trong
    Docker, thư mục khác khi chạy ngoài container) — lưu đường dẫn tuyệt đối thì đổi mount là
    toàn bộ bản ghi cũ trỏ vào hư không.

    Tên file lấy theo hash nên ghi lại đúng ảnh đó là ghi đè chính nó, không sinh file mồ côi.
    Chia thư mục con 2 ký tự đầu để một thư mục không chứa hàng chục nghìn file.
    """
    relative = Path(image_hash[:2]) / f"{image_hash}.jpg"
    target = settings.upload_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return relative.as_posix()


def _ms_since(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
