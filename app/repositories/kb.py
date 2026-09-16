"""Repository `kb_chunks` — đọc/ghi Knowledge Base của F3.

Chủ sở hữu: Q | Task: 6.3 | xem Task.md

Ba việc, đúng ranh giới của một repository (router/service lo nghiệp vụ, file này lo câu SQL):

1. **Ghi đè chunk theo nguồn** (`replace_chunks`) — index lại một danh thiếp/hồ sơ nhiều lần
   phải ra cùng một kết quả, không cộng dồn bản cũ.
2. **Duyệt dữ liệu cần index** (`card_batch`, `profile_batch`) — phân trang theo khoá chứ không
   `OFFSET`, để `POST /api/kb/reindex` (6.4) không phải nạp cả bảng vào RAM.
3. **Tìm theo vector** (`search_similar`) — nguyên thuỷ cho `services/retriever.py` (7.1); mọi
   thứ khác (ngưỡng điểm, trộn full-text, xếp hạng) là việc của 7.1, cố ý không nằm ở đây.

Index `ivfflat` (cosine) do revision `0003` tạo và `models/kb.py` khai — xem ghi chú ở đó về
lý do tạo muộn.

**Không hàm nào trong file này commit.** Một lượt reindex ghi hàng chục nguồn; commit từng
nguồn thì nửa chừng hỏng là KB ở trạng thái nửa cũ nửa mới, còn để chỗ gọi quyết định thì nó
gom được đúng một batch vào một transaction (xem `services/kb.py::index_documents`).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from sqlalchemy import delete, func, select, text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.card import BusinessCard, CardStatus
from app.models.company import Company, CompanyProfile
from app.models.kb import KBChunk, KBSourceType

#: Chỉ danh thiếp **đã xác nhận** mới vào KB. Bản `pending`/`needs_review` là dữ liệu model
#: đoán, chưa ai duyệt — đưa vào KB là để trợ lý trả lời bằng thông tin chưa được kiểm
#: (rủi ro R3/R4). Luồng 1 trong Plan.md mục 2.2 cũng chỉ sinh embedding sau bước xác nhận.
INDEXABLE_CARD_STATUSES = (CardStatus.CONFIRMED,)

#: Hồ sơ `draft` là hồ sơ đang chạy dở (task 5.4) — chưa có nội dung thật để index.
#: Cùng cách hiểu với bộ lọc `has_profile` của T ở `repositories/company.py`.
INDEXABLE_PROFILE_STATUSES = ("generated", "verified")

#: Tên index vector — trùng `models/kb.py` và revision `0003`.
VECTOR_INDEX_NAME = "ix_kb_chunks_embedding"

#: Số nguồn đọc mỗi lượt khi reindex. Nhỏ để không giữ transaction lâu, đủ lớn để một lời gọi
#: embedder gánh được nhiều đoạn (trần một request là 32 — `services/embeddings.MAX_BATCH`).
BATCH_SIZE = 20


@dataclass(frozen=True, slots=True)
class ChunkRow:
    """Một dòng sắp ghi vào `kb_chunks`: văn bản + metadata + vector đã sinh."""

    content: str
    embedding: list[float]
    meta: dict[str, Any] = field(default_factory=dict)


async def replace_chunks(
    db: AsyncSession,
    *,
    source_type: KBSourceType | str,
    source_id: uuid.UUID,
    chunks: Sequence[ChunkRow],
) -> int:
    """Xoá sạch chunk cũ của một nguồn rồi ghi bộ mới. Trả về số chunk đã ghi.

    Xoá-rồi-ghi chứ không `UPDATE` từng dòng: số chunk của một hồ sơ thay đổi theo độ dài mô tả
    model trả về, nên không có khoá nào để ghép cặp dòng cũ với dòng mới. Bỏ bước xoá thì mỗi
    lần "Tạo lại hồ sơ" (task 6.7) lại nhân đôi dữ liệu trong KB và trợ lý trích dẫn bản cũ.
    """
    await delete_for_source(db, source_type=source_type, source_id=source_id)
    db.add_all(
        [
            KBChunk(
                source_type=str(source_type),
                source_id=source_id,
                content=chunk.content,
                meta=chunk.meta or None,
                embedding=chunk.embedding,
            )
            for chunk in chunks
        ]
    )
    await db.flush()
    return len(chunks)


async def delete_for_source(
    db: AsyncSession,
    *,
    source_type: KBSourceType | str,
    source_id: uuid.UUID,
) -> int:
    """Gỡ một nguồn khỏi KB (xoá danh thiếp ở 4.2, hoặc trước khi ghi lại). Trả số dòng đã xoá."""
    # `Session.execute()` khai kiểu trả về là `Result`; chỉ câu DML mới có `rowcount`, nên
    # phải nói rõ với mypy thay vì gắn `# type: ignore` mù.
    result = cast(
        "CursorResult[Any]",
        await db.execute(
            delete(KBChunk).where(
                KBChunk.source_type == str(source_type),
                KBChunk.source_id == source_id,
            )
        ),
    )
    return int(result.rowcount or 0)


async def count_chunks(db: AsyncSession, *, source_type: KBSourceType | str | None = None) -> int:
    """Đếm chunk trong KB — dùng cho `/api/kb/reindex` và dashboard (7.7)."""
    query = select(func.count()).select_from(KBChunk)
    if source_type is not None:
        query = query.where(KBChunk.source_type == str(source_type))
    return int(await db.scalar(query) or 0)


async def rebuild_vector_index(db: AsyncSession) -> None:
    """`REINDEX` index `ivfflat` — **bắt buộc chạy sau khi ghi xong một lượt reindex đầy đủ**.

    Không phải tối ưu hoá, mà là điều kiện để tìm kiếm ra kết quả. `ivfflat` học phân cụm từ dữ
    liệu **có sẵn lúc index được tạo**; revision `0003` chạy khi `kb_chunks` còn rỗng nên các
    centroid vô nghĩa. Đo thật 2026-09-16 trên pgvector/pg16: index tạo lúc bảng rỗng, chèn 3
    dòng rồi `ORDER BY v <=> …` chỉ trả về **1** dòng; `REINDEX` xong trả đủ **3**.

    `REINDEX INDEX` (không `CONCURRENTLY`) khoá bảng trong lúc chạy. Chấp nhận được vì đây là
    thao tác quản trị trên KB cỡ vài trăm dòng, mất vài chục mili giây.
    """
    await db.execute(text(f"REINDEX INDEX {VECTOR_INDEX_NAME}"))


async def card_batch(
    db: AsyncSession,
    *,
    after: uuid.UUID | None = None,
    limit: int = BATCH_SIZE,
) -> Sequence[tuple[BusinessCard, str | None]]:
    """Một lô danh thiếp cần index, kèm **tên công ty đã chuẩn hoá** (có thể `None`).

    Phân trang theo khoá (`id > after`) chứ không `OFFSET`: reindex vừa đọc vừa ghi, mà `OFFSET`
    trên tập đang thay đổi thì bản ghi bị nhảy cóc hoặc lặp lại. `id` là UUID nên thứ tự không
    có ý nghĩa nghiệp vụ — ở đây chỉ cần nó **ổn định và duy nhất**, đủ để duyệt hết đúng một lần.

    Lấy kèm `display_name` bằng `LEFT JOIN` thay vì để `services/kb.py` tự truy vấn từng thẻ:
    30 danh thiếp là 30 lời gọi DB thừa, và tên công ty là thứ luôn cần khi serialize (6.2).
    """
    query = (
        select(BusinessCard, Company.display_name)
        .outerjoin(Company, BusinessCard.company_id == Company.id)
        .where(BusinessCard.status.in_([str(status) for status in INDEXABLE_CARD_STATUSES]))
        .order_by(BusinessCard.id)
        .limit(limit)
    )
    if after is not None:
        query = query.where(BusinessCard.id > after)

    rows = await db.execute(query)
    return [(card, name) for card, name in rows.all()]


async def profile_batch(
    db: AsyncSession,
    *,
    after: uuid.UUID | None = None,
    limit: int = BATCH_SIZE,
) -> Sequence[tuple[CompanyProfile, Company]]:
    """Một lô hồ sơ doanh nghiệp cần index, kèm công ty tương ứng.

    `INNER JOIN`: hồ sơ không có công ty là dữ liệu hỏng (khoá ngoại `NOT NULL` đã chặn), và
    thiếu tên công ty thì đoạn văn index được cũng không dùng để trả lời được câu nào.
    """
    query = (
        select(CompanyProfile, Company)
        .join(Company, CompanyProfile.company_id == Company.id)
        .where(CompanyProfile.status.in_(INDEXABLE_PROFILE_STATUSES))
        .order_by(CompanyProfile.id)
        .limit(limit)
    )
    if after is not None:
        query = query.where(CompanyProfile.id > after)

    rows = await db.execute(query)
    return [(profile, company) for profile, company in rows.all()]


async def search_similar(
    db: AsyncSession,
    embedding: Sequence[float],
    *,
    top_k: int = 5,
    source_type: KBSourceType | str | None = None,
) -> Sequence[tuple[KBChunk, float]]:
    """Top-k chunk gần nhất theo **khoảng cách cosine** (0 = trùng khớp, 2 = ngược hướng).

    Trả thẳng khoảng cách chứ không đổi sang "điểm tương đồng": đổi ở đây thì `retriever.py`
    (7.1) lại phải đoán xem con số đang là khoảng cách hay điểm. Một quy ước, một chỗ đổi.

    Toán tử `<=>` là thứ index `ivfflat … vector_cosine_ops` phục vụ; đổi sang khoảng cách khác
    (L2, tích vô hướng) thì câu truy vấn vẫn chạy nhưng **bỏ qua index** và quét toàn bảng.
    """
    distance = KBChunk.embedding.cosine_distance(list(embedding)).label("distance")
    query = select(KBChunk, distance).order_by(distance).limit(top_k)
    if source_type is not None:
        query = query.where(KBChunk.source_type == str(source_type))

    rows = await db.execute(query)
    return [(chunk, float(value)) for chunk, value in rows.all()]
