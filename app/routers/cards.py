"""F1 — upload, danh sách, chi tiết, sửa, xoá, confirm danh thiếp.

Chủ sở hữu: Q | Task: 3.1, 4.1–4.5, 5.1–5.3, 12.5

Router khai **đường dẫn đầy đủ** thay vì `prefix=`: file này phục vụ cả API (`/api/cards/*`) lẫn
ba trang HTML. Gom vào một router để không phải đụng `app/main.py`.

⚠️ **Thứ tự khai báo route HTML là một phần của thiết kế**: `/cards/upload` và `/cards/batch`
phải đứng trước `/cards/{card_id}`, nếu không chúng rơi vào route chi tiết và chết ở bước parse UUID.

Một lượt upload: đọc có giới hạn (cắt ngay lúc đọc, không đọc hết rồi mới đo) → chống trùng bằng
SHA-256 của **file gốc** (nén JPEG không tất định nên băm bản đã nén là sai) → tiền xử lý + lưu
volume → OCR + lưu DB.

**OCR hỏng không làm hỏng lượt upload**: ảnh vẫn được lưu, bản ghi vẫn tạo ở trạng thái `pending`
kèm lý do trong `ocr_error`, quét lại sau.
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

#: Trần số ảnh một lượt batch — giới hạn về sự kiên nhẫn, không phải giới hạn kỹ thuật.
MAX_BATCH_FILES = 50

#: Điểm `confidence` dưới mức này thì màn hình review tô vàng. 0.75 đo từ kết quả thật: chữ rõ
#: model chấm 0.9–1.0, chỗ mờ tụt xuống 0.5–0.7.
LOW_CONFIDENCE = 0.75


# --------------------------------------------------------------------------- trang HTML


@router.get("/cards", response_class=HTMLResponse, tags=["ui"])
async def cards_page(request: Request) -> HTMLResponse:
    """Màn hình danh sách danh thiếp.

    Trang render rỗng rồi để JavaScript gọi `GET /api/cards` đổ dữ liệu vào: tìm kiếm và lọc phải
    chạy được mà không tải lại trang.
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
    """Màn hình quét danh thiếp — **một ảnh hay ba mươi ảnh đều vào đây**.

    Gộp từ hai màn hình cũ: chúng chỉ khác nhau ở số ảnh, mà đó là thứ người dùng chưa biết cho
    tới khi mở hộp chọn file ra.
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
    """`/cards/batch` gộp vào `/cards/upload` — chuyển hướng **301**.

    **Phải khai trước `/cards/{card_id}`**, nếu không `batch` rơi vào route chi tiết và không
    parse được thành UUID. 301 chứ không xoá thẳng vì đường dẫn này nằm trong tài liệu và trong
    trang đánh dấu của người đã dùng nó.
    """
    return RedirectResponse("/cards/upload", status_code=status.HTTP_301_MOVED_PERMANENTLY)


@router.get("/cards/{card_id}", response_class=HTMLResponse, tags=["ui"])
async def card_detail_page(request: Request, card_id: uuid.UUID) -> HTMLResponse:
    """Màn hình review một danh thiếp: ảnh trái, form phải.

    Route **không** đọc DB, chỉ truyền `card_id` xuống template rồi để JavaScript gọi
    `GET /api/cards/{id}`. Sau mỗi lần Lưu, server trả bản đã chuẩn hoá và trang vẽ lại từ đúng
    payload đó; render sẵn từ Jinja thì trang có hai nguồn sự thật.
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

    Trả **201** cho ảnh mới, **200** khi ảnh đã được quét trước đó (`duplicate=true`, trả lại bản
    ghi cũ và không gọi lại model).
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
        # Hai lượt upload cùng một ảnh chạy song song; lượt kia đã ghi xong.
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
    """Nhận nhiều ảnh → lưu hết ngay → trả `job_id`, việc quét chạy nền.

    Ranh giới "làm ngay" / "làm sau" đặt ở chỗ **mất thì có lấy lại được không**: file người dùng
    vừa chọn chỉ tồn tại trong request này. Gọi vision mới đẩy ra nền — xem `services/card_batch.py`.

    **Một ảnh hỏng không làm hỏng cả lượt**: lý do nằm ngay trong `items[]`.
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
    """Tiến trình một lượt batch — `templates/cards/upload.html` poll endpoint này.

    Job của **không gian khác** trả 404 y như job không tồn tại. Đối chiếu theo không gian chứ
    không theo người bấm nút: đồng nghiệp phải theo dõi được tiến trình của chính lô ấy.
    """
    job = card_batch.get_job(job_id)
    if job is not None and job.workspace_id != workspace.id:
        job = None
    if job is None:
        # Nói thẳng job sống trong bộ nhớ: 404 trơ đọc như "ID sai", trong khi nguyên nhân thật
        # thường là container vừa restart.
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
    """Danh sách danh thiếp, có phân trang / tìm kiếm / lọc.

    Trạng thái lạ trả **400** chứ không trả danh sách rỗng: rỗng đọc như "chưa có danh thiếp nào".
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
        # Trang cuối tính từ tổng, không từ `len(items)`: trang rỗng ở cuối vẫn phải biết còn bao
        # nhiêu trang.
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
    """Ảnh đã tiền xử lý của một danh thiếp — UI dùng làm thumbnail và ảnh gốc.

    Ảnh nằm trong volume `uploads`, **không** dưới `/static`: mount cả thư mục upload ra static là
    công khai toàn bộ danh thiếp cho bất kỳ ai đoán được tên file.
    """
    card = await _get_or_404(db, card_id, workspace_id=workspace.id)
    # `image_path` cho phép `NULL` — nói thẳng ra thay vì để `_resolve_image(None)` nổ kiểu.
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
    """Sửa tay các trường sau khi review.

    Giá trị người dùng gõ vẫn đi qua `services/normalize.py`, đúng như lúc quét — không chuẩn hoá
    thì DB có hai kiểu số tuỳ theo trường đó do model đọc hay do người sửa.

    **Cố ý không đụng `status`**: chuyển sang `confirmed` là việc của `POST /{id}/confirm`.
    """
    card = await _get_or_404(db, card_id, workspace_id=workspace.id)

    changes = payload.changes()
    if not changes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Không có trường nào để sửa.")

    edited = sorted(changes)
    language = changes.get("language_detected", card.language_detected)

    # `notes` không nằm trong danh sách trắng của repository nên phải gán tay. Gán TRƯỚC khi gọi
    # `update_fields` để cả hai thay đổi đi chung một commit.
    if "notes" in changes:
        notes = changes.pop("notes")
        card.notes = normalize.squash_spaces(notes) if notes is not None else None

    fields = _normalize_edits(changes, language=language)
    fields.update(_translation_meta_after_edit(card, changes))
    card = await card_repo.update_fields(db, card, fields)

    # Thẻ **đã xác nhận** thì nó đang nằm trong KB, và KB vừa lệch với DB — index lại ngay thay
    # vì chờ ai bấm `POST /api/kb/reindex`. Thẻ chưa xác nhận chưa bao giờ vào KB.
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
    """Việt hoá lại danh thiếp — nút *Dịch lại* ở màn hình review.

    Chạy trên **giá trị đang nằm trong DB**, tức gồm cả những chỗ người dùng vừa sửa tay.

    Khác lượt Việt hoá tự động sau khi quét, ở đây lỗi **được báo ra** (503/502): người dùng vừa
    bấm nút và đang đợi.
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

    # Thẻ đã xác nhận thì đang nằm trong KB, mà chunk có in cả bản Việt hoá.
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
    """Xoá một danh thiếp và file ảnh của nó.

    Xoá hàng trước, xoá file sau: ngược lại thì transaction hỏng sẽ để lại bản ghi trỏ vào file
    đã mất. Xoá file hỏng thì chỉ ghi log — file thừa trong volume không làm hỏng gì.
    """
    card = await _get_or_404(db, card_id, workspace_id=workspace.id)
    image_path = card.image_path

    # Gỡ khỏi KB **trong cùng transaction** với việc xoá hàng. Bỏ bước này thì chunk mồ côi ở lại
    # và trợ lý vẫn trích dẫn một danh thiếp đã xoá.
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
    """Người dùng duyệt xong → `confirmed` + gắn `company_id`.

    **Không kích hoạt enrich.** Hồ sơ doanh nghiệp chỉ sinh khi người dùng tích chọn công ty rồi
    bấm nút ở màn hình Doanh nghiệp (Luồng 2). Đây mới chỉ tạo bản ghi *tên công ty*.

    Gắn công ty **không chặn xác nhận**: không đọc được tên công ty thì bản ghi vẫn về
    `confirmed` và `detail` nói rõ vì sao chưa gắn.
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
            # Bản Việt hoá đã có sẵn trên thẻ, chép thẳng sang công ty thay vì để danh sách
            # `/companies` hiện chữ Hàn/Trung/Ả Rập.
            display_name_vi=card.company_name_vi,
            email=card.email,
            website=card.website,
        )

    card.company_id = company_id
    card.status = CardStatus.CONFIRMED
    await db.commit()
    await db.refresh(card)

    # Xác nhận xong là vào Knowledge Base ngay (Luồng 1).
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
    người dùng đã chọn, nếu không thì file bị loại biến mất khỏi giao diện.

    Phần **đồng bộ** của batch nên nó chặn event loop (~100ms/ảnh). Chấp nhận được vì client duy
    nhất đang chờ chính là request này.
    """
    filename = (upload.filename or "").strip() or "(không có tên file)"

    try:
        raw = await _read_limited(upload)
    except HTTPException as exc:
        # Bắt lại chính lỗi mình vừa ném thay vì chép lại luật giới hạn dung lượng lần thứ hai.
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
            user_id=user_id,
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
            user_id=user_id,
            card_id=raced.id,
            status=card_batch.ItemStatus.DONE,
            duplicate=True,
        )

    # `user_id` phải đi kèm, không chỉ `workspace_id`: lượt quét ở nền dùng credential OAuth và
    # model của **chính người bấm nút**, mà request đã trả 202 nên không còn cookie để hỏi lại.
    return card_batch.BatchItem(
        filename=filename,
        workspace_id=workspace_id,
        user_id=user_id,
        card_id=card.id,
        image_path=settings.upload_dir / relative_path,
        status=card_batch.ItemStatus.PENDING,
    )


def _job_out(job: card_batch.BatchJob) -> BatchJobOut:
    """`BatchJob` (dataclass trong bộ nhớ) → payload JSON.

    Ánh xạ tay chứ không `model_validate(from_attributes=True)`: `BatchItem.image_path` là đường
    dẫn nội bộ của container, tự động hoá là mở đường cho nó rò ra API.
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
    """Danh thiếp của không gian đang mở, hoặc 404.

    Thẻ của người khác và thẻ không tồn tại trả **cùng một** 404 với **cùng một câu** — khác nhau
    ở mã, câu chữ hay thời gian phản hồi đều là một kênh để dò xem id nào có thật.
    """
    card = await card_repo.get(db, card_id, workspace_id=workspace_id)
    if card is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Không có danh thiếp này.")
    return card


def _normalize_edits(changes: dict[str, Any], *, language: str | None) -> dict[str, Any]:
    """Chuẩn hoá đúng những trường người dùng vừa sửa, **từng trường một**.

    Không gọi `normalize_card_fields()`: hàm đó xử lý `phone`/`phone_alt` như một cặp và luôn ghi
    lại cả hai, nên PATCH chỉ gửi `phone` sẽ xoá mất `phone_alt` đang có.
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

    Hai ca loại trừ nhau: sửa thẳng ô Việt hoá → `source="manual"`; sửa trường gốc → `stale=True`
    để giao diện mời bấm *Dịch lại*.

    Cố ý **không tự gọi model ở đây** — màn hình review bấm Lưu liên tục. Dựng dict mới chứ không
    sửa tại chỗ: JSONB không được SQLAlchemy theo dõi thay đổi bên trong.
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
    """Đưa một danh thiếp vào Knowledge Base. Trả `(đã index?, lý do nếu không)`.

    **Không bao giờ làm hỏng thao tác gọi nó**: xác nhận thẻ là việc của F1 và phải xong được kể
    cả khi `embedder` chưa lên. Vá sau bằng `POST /api/kb/reindex`.

    Chạy đồng bộ (1–2 chunk, vài chục ms) nên `kb_indexed` trong response là sự thật đã xảy ra.

    ⚠️ **`rollback()` rồi phải `refresh()` ngay.** `rollback()` làm mọi object ORM hết hạn, nên
    đọc `card.…` sau đó là một lượt nạp lại đồng bộ giữa hàm async → `MissingGreenlet` + HTTP 500.
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
    """Huỷ phần ghi dở của KB rồi nạp lại `card` để chỗ gọi dùng tiếp được."""
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
    """Gọi `company_matching.upsert_company()` của T.

    **`email` và `website` bắt buộc phải truyền** (I-18): quy tắc gộp công ty đọc tên miền từ hai
    trường này theo cả hai chiều — chung tên miền thì nới ngưỡng so mờ, khác tên miền thì không
    bao giờ gộp. Gọi thiếu thì luồng xác nhận chỉ còn so tên và gộp nhầm (I-21).

    Import **trong hàm** và kiểm bằng `getattr` để phân biệt "hàm chưa có" với "gọi được nhưng
    hỏng".
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

    DB lưu đường dẫn **tương đối**. Ghép xong phải kiểm lại nó nằm trong `UPLOAD_DIR`: một bản
    ghi cũ chứa `../` sẽ biến endpoint ảnh thành đường đọc trộm file của container.
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

    Tương đối chứ không tuyệt đối: `UPLOAD_DIR` là biến môi trường, lưu tuyệt đối thì đổi mount
    là mọi bản ghi cũ trỏ vào hư không. Tên file lấy theo hash; chia thư mục con 2 ký tự đầu.
    """
    relative = Path(image_hash[:2]) / f"{image_hash}.jpg"
    target = settings.upload_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return relative.as_posix()


def _ms_since(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
