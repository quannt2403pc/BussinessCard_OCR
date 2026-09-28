"""Repository `kb_chunks` — đọc/ghi Knowledge Base của F3.

Chủ sở hữu: Q | Task: 6.3

Ba việc: ghi đè chunk theo nguồn (`replace_chunks`), duyệt dữ liệu cần index theo khoá chứ không
`OFFSET` (`card_batch`, `profile_batch`), và hai cách tìm mỗi cách một câu SQL (`search_similar`,
`search_fulltext`). Cả hai chỉ trả *ứng viên đã xếp hạng* — ngưỡng điểm và việc trộn là của
`services/retriever.py`.

**`workspace_id` là tham số bắt buộc của mọi hàm đọc/ghi ở đây.** Rò một chunk không hiện ra ở
danh sách nào cả, nó đi thẳng vào ngữ cảnh của trợ lý rồi ra thành một câu trả lời tự tin về dữ
liệu của người khác. Hai nhánh tìm kiếm đều nhận điều kiện qua **cùng một** `scope_filters()`.

**Không hàm nào trong file này commit** — chỗ gọi gom cả batch vào một transaction.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from sqlalchemy import ColumnElement, delete, func, literal, select, text
from sqlalchemy import cast as sa_cast
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.card import BusinessCard, CardStatus
from app.models.company import Company, CompanyProfile
from app.models.kb import IVFFLAT_LISTS, KBChunk, KBSourceType

#: Chỉ danh thiếp **đã xác nhận** mới vào KB. Bản `pending`/`needs_review` là dữ liệu model đoán,
#: chưa ai duyệt — đưa vào KB là để trợ lý trả lời bằng thông tin chưa được kiểm (R3/R4).
INDEXABLE_CARD_STATUSES = (CardStatus.CONFIRMED,)

#: Hồ sơ `draft` là hồ sơ đang chạy dở — chưa có nội dung thật để index.
INDEXABLE_PROFILE_STATUSES = ("generated", "verified")

#: Tên index vector — trùng `models/kb.py` và revision `0003`.
VECTOR_INDEX_NAME = "ix_kb_chunks_embedding"

#: Số cụm `ivfflat` dò mỗi lượt tìm. **Đặt bằng đúng `lists` là cố ý**: mặc định `probes = 1` chỉ
#: dò một cụm, nên chunk đúng nằm ở cụm khác là không bao giờ trả về — mất recall im lặng. Ở quy
#: mô KB vài trăm chunk thì quét hết mọi cụm mất vài mili giây. KB lớn hơn nhiều bậc thì hạ số
#: này xuống và đo lại recall.
VECTOR_PROBES = IVFFLAT_LISTS

#: Từ điển full-text cho nhánh tìm theo từ khoá. `simple` = chỉ tách token và hạ hoa thường,
#: **không** stemming, **không** bỏ stopword — đúng thứ cần để bắt tên riêng, email, số điện
#: thoại, mã số thuế. Dùng `english` thì mọi từ tiếng Việt trùng hình thức stopword tiếng Anh
#: ("a", "so", "the") bị ném đi.
#:
#: ⚠️ `simple` **không bỏ dấu**: gõ "cong ty" sẽ không khớp "công ty". Đó là việc của nhánh vector.
FTS_CONFIG = "simple"

#: Số nguồn đọc mỗi lượt khi reindex. Nhỏ để không giữ transaction lâu, đủ lớn để một lời gọi
#: embedder gánh được nhiều đoạn (trần một request là 32).
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
    workspace_id: uuid.UUID,
    source_type: KBSourceType | str,
    source_id: uuid.UUID,
    chunks: Sequence[ChunkRow],
) -> int:
    """Xoá sạch chunk cũ của một nguồn rồi ghi bộ mới. Trả về số chunk đã ghi.

    Xoá-rồi-ghi chứ không `UPDATE` từng dòng: số chunk đổi theo độ dài mô tả model trả về nên
    không có khoá nào ghép cặp dòng cũ với dòng mới. Bỏ bước xoá thì mỗi lần "Tạo lại hồ sơ" lại
    nhân đôi dữ liệu trong KB.
    """
    await delete_for_source(
        db, workspace_id=workspace_id, source_type=source_type, source_id=source_id
    )
    db.add_all(
        [
            KBChunk(
                workspace_id=workspace_id,
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
    workspace_id: uuid.UUID,
    source_type: KBSourceType | str,
    source_id: uuid.UUID,
) -> int:
    """Gỡ một nguồn khỏi KB. Trả số dòng đã xoá.

    `workspace_id` trong `WHERE` không phải để chống rò mà để **chặn xoá chéo**: một lỗi lập
    trình truyền sang id của người khác thì câu này không xoá gì cả.
    """
    # `Session.execute()` khai kiểu trả về là `Result`; chỉ câu DML mới có `rowcount`.
    result = cast(
        "CursorResult[Any]",
        await db.execute(
            delete(KBChunk).where(
                KBChunk.workspace_id == workspace_id,
                KBChunk.source_type == str(source_type),
                KBChunk.source_id == source_id,
            )
        ),
    )
    return int(result.rowcount or 0)


async def count_chunks(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    source_type: KBSourceType | str | None = None,
) -> int:
    """Đếm chunk **của một người** trong KB — dùng cho `/api/kb/reindex` và dashboard (7.7)."""
    query = select(func.count()).select_from(KBChunk).where(KBChunk.workspace_id == workspace_id)
    if source_type is not None:
        query = query.where(KBChunk.source_type == str(source_type))
    return int(await db.scalar(query) or 0)


async def rebuild_vector_index(db: AsyncSession) -> None:
    """`REINDEX` index `ivfflat` — **bắt buộc chạy sau khi ghi xong một lượt reindex đầy đủ**.

    Không phải tối ưu hoá mà là điều kiện để tìm kiếm ra kết quả: `ivfflat` học phân cụm từ dữ
    liệu **có sẵn lúc index được tạo**, mà migration chạy khi `kb_chunks` còn rỗng. Đo thật: index
    tạo lúc bảng rỗng, chèn 3 dòng rồi tìm chỉ trả về **1**; `REINDEX` xong trả đủ **3**.

    `REINDEX INDEX` (không `CONCURRENTLY`) khoá bảng vài chục mili giây — chấp nhận được ở quy mô
    này. **Cố ý không có `workspace_id`**: index là cấu trúc của cả bảng, không chia theo người
    dùng được, và nó không đọc/trả về dữ liệu của ai nên không phải đường rò.
    """
    await db.execute(text(f"REINDEX INDEX {VECTOR_INDEX_NAME}"))


async def card_batch(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    after: uuid.UUID | None = None,
    limit: int = BATCH_SIZE,
) -> Sequence[tuple[BusinessCard, str | None]]:
    """Một lô danh thiếp cần index, kèm **tên công ty đã chuẩn hoá** (có thể `None`).

    Phân trang theo khoá (`id > after`) chứ không `OFFSET`: reindex vừa đọc vừa ghi, mà `OFFSET`
    trên tập đang thay đổi thì bản ghi bị nhảy cóc hoặc lặp lại.

    Lấy kèm `display_name` bằng `LEFT JOIN` để `services/kb.py` khỏi truy vấn từng thẻ.
    """
    query = (
        select(BusinessCard, Company.display_name)
        .outerjoin(Company, BusinessCard.company_id == Company.id)
        .where(
            BusinessCard.workspace_id == workspace_id,
            BusinessCard.status.in_([str(status) for status in INDEXABLE_CARD_STATUSES]),
        )
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
    workspace_id: uuid.UUID,
    after: uuid.UUID | None = None,
    limit: int = BATCH_SIZE,
) -> Sequence[tuple[CompanyProfile, Company]]:
    """Một lô hồ sơ doanh nghiệp cần index, kèm công ty tương ứng.

    `INNER JOIN`: hồ sơ không có công ty là dữ liệu hỏng, và thiếu tên công ty thì đoạn văn index
    được cũng không trả lời được câu nào.
    """
    query = (
        select(CompanyProfile, Company)
        .join(Company, CompanyProfile.company_id == Company.id)
        .where(
            CompanyProfile.workspace_id == workspace_id,
            CompanyProfile.status.in_(INDEXABLE_PROFILE_STATUSES),
        )
        .order_by(CompanyProfile.id)
        .limit(limit)
    )
    if after is not None:
        query = query.where(CompanyProfile.id > after)

    rows = await db.execute(query)
    return [(profile, company) for profile, company in rows.all()]


def scope_filters(
    *,
    workspace_id: uuid.UUID,
    source_type: KBSourceType | str | None = None,
    company_id: uuid.UUID | None = None,
) -> list[ColumnElement[bool]]:
    """Điều kiện thu hẹp phạm vi tìm kiếm, dùng chung cho **cả hai** nhánh.

    `workspace_id` là điều kiện đầu tiên và **không tắt được bằng tham số nào**, khác hai bộ lọc
    dưới vốn do người dùng chọn. Một hàm chứ không chép hai lần: hai nhánh lọc lệch nhau thì kết
    quả trộn ra một tập nửa trong phạm vi nửa ngoài, và không có lỗi nào báo.

    `company_id` đọc từ **metadata** chứ không từ cột: `source_id` là id danh thiếp ở chunk danh
    thiếp nhưng là id **công ty** ở chunk hồ sơ, nên lọc theo cột sẽ bỏ sót đúng một nửa.
    """
    filters: list[ColumnElement[bool]] = [KBChunk.workspace_id == workspace_id]
    if source_type is not None:
        filters.append(KBChunk.source_type == str(source_type))
    if company_id is not None:
        filters.append(KBChunk.meta["company_id"].astext == str(company_id))
    return filters


async def search_similar(
    db: AsyncSession,
    embedding: Sequence[float],
    *,
    workspace_id: uuid.UUID,
    top_k: int = 5,
    source_type: KBSourceType | str | None = None,
    company_id: uuid.UUID | None = None,
) -> Sequence[tuple[KBChunk, float]]:
    """Top-k chunk gần nhất theo **khoảng cách cosine** (0 = trùng khớp, 2 = ngược hướng).

    Trả thẳng khoảng cách chứ không đổi sang "điểm tương đồng" — một quy ước, một chỗ đổi.

    Toán tử `<=>` là thứ index `ivfflat … vector_cosine_ops` phục vụ; đổi sang khoảng cách khác
    thì câu truy vấn vẫn chạy nhưng **bỏ qua index** và quét toàn bảng.

    ⚠️ **`workspace_id` phải nằm trong `WHERE` của chính câu này**, không được sàng lại danh sách
    trả về: sàng sau thì top-5 của A bị chunk của B chiếm chỗ rồi bị bỏ đi — vừa rò vừa sai.
    """
    # `SET LOCAL` chứ không `SET`: chỉ có hiệu lực tới hết transaction nên không rò sang request
    # khác dùng chung connection. Nội suy vì `SET` không nhận tham số bind — giá trị là hằng int
    # của chính module này.
    await db.execute(text(f"SET LOCAL ivfflat.probes = {VECTOR_PROBES:d}"))

    distance = KBChunk.embedding.cosine_distance(list(embedding)).label("distance")
    query = select(KBChunk, distance).order_by(distance).limit(top_k)
    for condition in scope_filters(
        workspace_id=workspace_id, source_type=source_type, company_id=company_id
    ):
        query = query.where(condition)

    rows = await db.execute(query)
    return [(chunk, float(value)) for chunk, value in rows.all()]


def content_tsvector() -> ColumnElement[Any]:
    """`to_tsvector('simple', content)` — biểu thức dùng chung cho cả mệnh đề lọc lẫn xếp hạng.

    Một hàm thay vì chép hai lần: lọc và tính điểm lệch cấu hình từ điển thì câu truy vấn vẫn
    chạy, chỉ là điểm tính trên một cách tách token khác — sai âm thầm.

    `sa_cast(literal(...), REGCONFIG)` vì dạng hai tham số của `to_tsvector` nhận `regconfig`.
    """
    return func.to_tsvector(sa_cast(literal(FTS_CONFIG), REGCONFIG), KBChunk.content)


async def search_fulltext(
    db: AsyncSession,
    terms: Sequence[str],
    *,
    workspace_id: uuid.UUID,
    top_k: int = 5,
    source_type: KBSourceType | str | None = None,
    company_id: uuid.UUID | None = None,
) -> Sequence[tuple[KBChunk, float]]:
    """Top-k chunk chứa **ít nhất một** trong `terms`, kèm điểm `ts_rank_cd` (càng lớn càng khớp).

    Nhận **danh sách từ khoá đã chọn lọc**, không nhận cả câu hỏi. `websearch_to_tsquery` nối mọi
    từ bằng `AND`, nên đưa nguyên câu hỏi vào là đòi chunk phải chứa cả "ai" lẫn "dùng" — hỏng im
    lặng với mọi câu hỏi thật. Chuyển sang `OR` cả câu cũng sai theo hướng ngược lại: `ts_rank_cd`
    không có IDF nên nhánh này trả về cả KB theo thứ tự gần như ngẫu nhiên. Lọc từ khoá **trước**
    khi xuống đây — `services/retriever.py::query_terms()`.

    Ghép các từ bằng `plainto_tsquery(cfg, :term) || …` chứ không nối chuỗi tsquery: mỗi từ đi
    xuống dưới dạng tham số bind nên câu này **không bao giờ ném lỗi cú pháp**.

    Không có index nào phục vụ câu này — KB cỡ vài trăm chunk nên Postgres quét bảng trong khoảng
    một mili giây. Index biểu thức chỉ đáng khi KB lớn hơn hẳn.
    """
    if not terms:
        return []

    tsvector = content_tsvector()
    tsquery = _tsquery_or(terms)
    rank = func.ts_rank_cd(tsvector, tsquery).label("rank")

    query = (
        select(KBChunk, rank).where(tsvector.op("@@")(tsquery)).order_by(rank.desc()).limit(top_k)
    )
    for condition in scope_filters(
        workspace_id=workspace_id, source_type=source_type, company_id=company_id
    ):
        query = query.where(condition)

    rows = await db.execute(query)
    return [(chunk, float(value)) for chunk, value in rows.all()]


def _tsquery_or(terms: Sequence[str]) -> ColumnElement[Any]:
    """`plainto_tsquery(term₁) || plainto_tsquery(term₂) || …` — khớp bất kỳ từ khoá nào.

    Một từ khoá nhiều tiếng ("Hòa Phát") vẫn được nối bằng `AND` bên trong nó: cả cụm phải xuất
    hiện mới tính là khớp.
    """
    config = sa_cast(literal(FTS_CONFIG), REGCONFIG)
    query: ColumnElement[Any] = func.plainto_tsquery(config, terms[0])
    for term in terms[1:]:
        query = query.op("||")(func.plainto_tsquery(config, term))
    return query
