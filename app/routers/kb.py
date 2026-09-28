"""F3 — `POST /api/kb/reindex`: index lại toàn bộ danh thiếp + hồ sơ DN vào Knowledge Base.

Chủ sở hữu: Q | Task: 6.4, 12.5

**Chạy đồng bộ, trả về số liệu — không phải job nền.** KB của bản demo cỡ 30 danh thiếp + 10 hồ
sơ, nhúng chừng đó mất vài giây trên CPU. Job nền tử tế phải có bảng trong DB để sống qua restart,
tức thêm một Alembic revision cho một endpoint quản trị chạy vài giây — không đáng. Đây cũng là
nút vá dữ liệu cho người vận hành, không nằm trên đường đi của người dùng cuối.

Một khoá trong tiến trình chặn hai lượt reindex chạy chồng nhau — không phải để bảo vệ dữ liệu
(ghi đè theo nguồn nên chạy chồng vẫn đúng) mà để khỏi đốt đôi thời gian CPU của embedder.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.workspace import WriterWorkspace
from app.repositories import kb as kb_repo
from app.schemas.kb import ReindexOut, ReindexScope
from app.services import embeddings, kb
from app.services.embeddings import EmbedderUnavailableError, EmbeddingError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/kb", tags=["kb"])

#: Trần số lô mỗi lượt chạy — chốt chặn cho vòng lặp, không phải giới hạn nghiệp vụ. Chạm trần
#: nghĩa là phân trang theo khoá có bug, và vòng lặp vô hạn thì giữ luôn cả khoá lẫn một kết nối DB.
MAX_BATCHES = 500

#: Chỉ một lượt reindex tại một thời điểm trong tiến trình này.
_reindex_lock = asyncio.Lock()


@router.post("/reindex", response_model=ReindexOut)
async def reindex(
    db: Annotated[AsyncSession, Depends(get_db)],
    workspace: WriterWorkspace,
    scope: Annotated[
        ReindexScope | None,
        Query(description="Chỉ index một loại nguồn. Bỏ trống = cả danh thiếp lẫn hồ sơ DN."),
    ] = None,
) -> ReindexOut:
    """Nhúng lại toàn bộ KB từ dữ liệu đang có trong DB.

    Không nhận `CurrentUser`: phạm vi index là **không gian làm việc**, mà `CurrentWorkspace` đã
    đòi đăng nhập rồi — giữ thêm một tham số không ai đọc chỉ mời gọi ai đó lại index theo
    `user.id`.

    Chỉ lấy danh thiếp **đã xác nhận** và hồ sơ **generated/verified**. Nguồn nào không còn trường
    nào có nội dung thì bị gỡ khỏi KB và tính vào `skipped`.
    """
    if _reindex_lock.locked():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Đang có một lượt index lại chạy dở. Đợi nó xong rồi bấm lại.",
        )

    async with _reindex_lock:
        started = time.perf_counter()
        try:
            # Hỏi embedder trước khi đọc DB: chưa sẵn sàng thì báo ngay thay vì để người dùng chờ
            # hết một vòng đọc dữ liệu. Đây cũng là chỗ bắt lệch số chiều.
            info = await embeddings.health()

            async with httpx.AsyncClient(
                base_url=embeddings.base_url(), timeout=embeddings.EMBED_TIMEOUT
            ) as http:
                cards = chunks = skipped = 0
                profiles = 0
                if scope in (None, ReindexScope.CARD):
                    cards, written, missing = await _reindex_cards(db, http, workspace.id)
                    chunks += written
                    skipped += missing
                if scope in (None, ReindexScope.COMPANY_PROFILE):
                    profiles, written, missing = await _reindex_profiles(db, http, workspace.id)
                    chunks += written
                    skipped += missing
        except EmbedderUnavailableError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        except EmbeddingError as exc:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

        # Ghi xong mới `REINDEX` được: index `ivfflat` học phân cụm từ dữ liệu đang có, mà
        # migration tạo nó lúc `kb_chunks` còn rỗng. Bỏ bước này thì KB đầy dữ liệu nhưng tìm
        # kiếm trả về gần như không có gì.
        if chunks:
            await kb_repo.rebuild_vector_index(db)
            await db.commit()

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        total_chunks = await kb_repo.count_chunks(db, workspace_id=workspace.id)
        logger.info(
            "Reindex KB: %d danh thiếp + %d hồ sơ → %d chunk (%d bỏ qua) trong %dms",
            cards,
            profiles,
            chunks,
            skipped,
            elapsed_ms,
        )
        return ReindexOut(
            cards=cards,
            profiles=profiles,
            chunks=chunks,
            skipped=skipped,
            total_chunks=total_chunks,
            elapsed_ms=elapsed_ms,
            model=str(info.get("model") or ""),
        )


async def _reindex_cards(
    db: AsyncSession, http: httpx.AsyncClient, workspace_id: uuid.UUID
) -> tuple[int, int, int]:
    """Duyệt hết danh thiếp đã xác nhận. Trả `(số nguồn, số chunk, số nguồn rỗng)`.

    Commit sau **mỗi lô**: hỏng ở lô sau không cuốn theo công sức nhúng của các lô trước.
    """
    after: uuid.UUID | None = None
    sources = written = empty = 0

    for _ in range(MAX_BATCHES):
        rows = await kb_repo.card_batch(db, workspace_id=workspace_id, after=after)
        if not rows:
            break
        documents = [
            kb.build_card_document(card, company_name=company_name) for card, company_name in rows
        ]
        # Đọc `id` TRƯỚC khi ghi: `index_documents()` commit, mà commit làm mọi object ORM hết
        # hạn — chạm vào thuộc tính sau đó là một lượt nạp lại đồng bộ giữa hàm async
        # (`MissingGreenlet`). `SessionLocal` có `expire_on_commit=False` nhưng dựa vào một tuỳ
        # chọn ở file khác thì lỗi chỉ chờ đúng một session khác là ra.
        after = rows[-1][0].id
        written += await kb.index_documents(db, documents, client=http, commit=True)
        empty += sum(1 for document in documents if not document.chunks)
        sources += len(rows)
    else:
        logger.warning("Reindex danh thiếp chạm trần %d lô — dừng sớm.", MAX_BATCHES)

    return sources, written, empty


async def _reindex_profiles(
    db: AsyncSession, http: httpx.AsyncClient, workspace_id: uuid.UUID
) -> tuple[int, int, int]:
    """Duyệt hết hồ sơ DN đã sinh xong. Trả `(số nguồn, số chunk, số nguồn rỗng)`."""
    after: uuid.UUID | None = None
    sources = written = empty = 0

    for _ in range(MAX_BATCHES):
        rows = await kb_repo.profile_batch(db, workspace_id=workspace_id, after=after)
        if not rows:
            break
        documents = [kb.build_profile_document(company, profile) for profile, company in rows]
        after = rows[-1][0].id  # phải đọc trước khi commit — xem ghi chú ở `_reindex_cards`
        written += await kb.index_documents(db, documents, client=http, commit=True)
        empty += sum(1 for document in documents if not document.chunks)
        sources += len(rows)
    else:
        logger.warning("Reindex hồ sơ DN chạm trần %d lô — dừng sớm.", MAX_BATCHES)

    return sources, written, empty
