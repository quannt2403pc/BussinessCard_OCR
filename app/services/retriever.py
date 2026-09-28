"""Truy hồi cho RAG: tìm theo vector, tìm theo từ khoá, trộn hai kết quả.

Chủ sở hữu: Q | Task: 7.1, 7.2, 12.5

Lớp giữa `repositories/kb.py` (câu SQL) và `routers/chat.py`: nhận câu hỏi bằng chữ, trả về các
chunk đã xếp hạng. Không gọi LLM, không dựng prompt.

Hai nhánh tìm vì chúng hỏng theo hai kiểu khác nhau: vector bắt được câu hỏi ngữ nghĩa ("công ty
nào làm logistics"), full-text bắt được định danh (email, số điện thoại, mã số thuế). Trộn lại
nâng recall@3 từ 7/10 lên 9/10.

⚠️ Cả hai cùng trượt với câu tiếng Việt **gõ không dấu** — cần extension `unaccent`, chưa làm.

Trộn bằng **Reciprocal Rank Fusion**, không cộng điểm thô: cosine nằm trong `[0, 1]` còn
`ts_rank_cd` không có trần, cộng thẳng là để full-text nuốt trọn thứ tự.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.kb import KBChunk, KBSourceType
from app.repositories import kb as kb_repo
from app.services import embeddings, normalize

logger = logging.getLogger(__name__)

#: Số chunk trả về mặc định (~3 500 ký tự ngữ cảnh).
TOP_K = 5

#: Mỗi nhánh lấy `top_k × hệ số này` ứng viên trước khi trộn — lấy đúng `top_k` thì RRF không
#: còn gì để xếp lại.
CANDIDATE_MULTIPLIER = 4

#: Ngưỡng cosine của nhánh vector — **sàn an toàn, KHÔNG phải bộ phân loại "có trong KB hay
#: không"**. Đo thật: chunk đúng 0.773–0.910, câu ngoài KB cao nhất 0.819 — hai phân bố chồng
#: nhau nên không ngưỡng nào tách được. Việc từ chối câu ngoài phạm vi nằm ở prompt trợ lý.
#: Đừng nâng lên cho "chắc ăn": nâng là mất recall thật mà câu lạc đề vẫn lọt.
MIN_SIMILARITY = 0.70

#: Hằng số làm mượt của RRF — 60 là giá trị trong bài báo gốc và mặc định của mọi thư viện.
RRF_K = 60

#: Vùng mặc định khi đọc số điện thoại trong câu hỏi (câu hỏi không có `language_detected`).
QUERY_PHONE_REGION = "VN"

#: Trần số từ khoá gửi xuống nhánh full-text — một tsquery `OR` dài thì khớp gần hết KB.
MAX_QUERY_TERMS = 8

#: Từ **không** được coi là từ khoá dù viết hoa: nhãn trường mà `services/kb.py` in vào mọi
#: chunk, cộng vài từ để hỏi. `ts_rank_cd` không có IDF nên phải tự loại chúng ở đây.
NON_SELECTIVE_WORDS = frozenset(
    (
        # từ để hỏi
        "ai",
        "gì",
        "nào",
        "đâu",
        "bao",
        "nhiêu",
        "sao",
        "vì",
        "cho",
        "của",
        "là",
        "có",
        "không",
        "và",
        "hoặc",
        "với",
        "thì",
        "mà",
        # tên loại nguồn
        "công",
        "ty",
        "doanh",
        "nghiệp",
        "danh",
        "thiếp",
        "hồ",
        "sơ",
        # nhãn trường của `serialize_card()`
        "họ",
        "tên",
        "chức",
        "vụ",
        "email",
        "mail",
        "điện",
        "thoại",
        "số",
        "địa",
        "chỉ",
        "website",
        "ngôn",
        "ngữ",
        "ngày",
        "ghi",
        "chú",
        # nhãn trường của `serialize_company_profile()`
        "mã",
        "thuế",
        "năm",
        "thành",
        "lập",
        "quy",
        "mô",
        "nhân",
        "sự",
        "ngành",
        "nghề",
        "sản",
        "phẩm",
        "dịch",
        "vụ",
        "mô",
        "tả",
        # nhãn tiếng Anh hay gặp trong câu hỏi
        "company",
        "phone",
        "address",
        "name",
    )
)

#: Dãy trông như số điện thoại. Cố ý bắt rộng rồi để `normalize.normalize_phone()` phán quyết.
_PHONE_LIKE_RE = re.compile(r"(?<![\w+])\+?\d[\d\s().\-]{5,}\d(?!\w)")

#: Email — bắt trước mọi thứ khác, vì hai regex dưới sẽ xé nó thành mảnh vụn nếu đi sau.
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

#: Tên miền / website (`abc.vn`, `www.fpt.com.vn`). Bỏ `https://` vì `to_tsvector` cũng bỏ.
_DOMAIN_RE = re.compile(r"(?:https?://)?(?:[\w-]+\.)+[a-z]{2,}(?:/\S*)?", re.IGNORECASE)

#: Dãy chữ số dài — mã số thuế, mã doanh nghiệp. Ngắn hơn 6 chữ số thì là năm, số nhà, số lượng.
_LONG_DIGITS_RE = re.compile(r"\d{6,}")

#: Một "từ" theo nghĩa chữ cái (không lấy chữ số, không lấy gạch dưới).
_WORD_RE = re.compile(r"[^\W\d_]+(?:[-'][^\W\d_]+)*", re.UNICODE)


@dataclass(frozen=True, slots=True)
class Hit:
    """Một chunk được truy hồi, kèm đủ thông tin để D8 trích dẫn nguồn."""

    chunk_id: uuid.UUID
    source_type: str
    source_id: uuid.UUID
    content: str
    meta: dict[str, Any]

    #: Điểm trộn RRF. Chỉ so sánh được trong cùng một lượt tìm.
    score: float
    #: Tương đồng cosine `1 - khoảng cách`, `None` khi chunk chỉ khớp ở nhánh full-text.
    similarity: float | None = None
    #: `ts_rank_cd`, `None` khi chunk chỉ khớp ở nhánh vector.
    text_rank: float | None = None
    #: `"vector"` | `"text"` | `"both"` — khớp ở **cả hai** là tín hiệu tin cậy nhất.
    matched_by: str = "vector"

    @property
    def title(self) -> str:
        """Dòng tiêu đề của nguồn (`Danh thiếp — …` / `Hồ sơ doanh nghiệp — …`).

        `services/kb.py` lặp dòng này vào đầu mọi chunk *và* ghi vào metadata; đọc từ metadata
        thì không phụ thuộc vào cách chunk được cắt.
        """
        title = self.meta.get("title")
        return title if isinstance(title, str) else ""

    @property
    def source_urls(self) -> list[str]:
        """URL nguồn của hồ sơ DN (rỗng với danh thiếp) — khối trích dẫn của D8."""
        urls = self.meta.get("sources")
        return [url for url in urls if isinstance(url, str)] if isinstance(urls, list) else []


async def search(
    db: AsyncSession,
    query: str,
    *,
    workspace_id: uuid.UUID,
    top_k: int = TOP_K,
    min_similarity: float = MIN_SIMILARITY,
    source_type: KBSourceType | str | None = None,
    company_id: uuid.UUID | None = None,
    hybrid: bool = True,
    client: httpx.AsyncClient | None = None,
) -> list[Hit]:
    """Tìm trong Knowledge Base, trả tối đa `top_k` chunk đã xếp hạng.

    Danh sách rỗng là câu trả lời hợp lệ, không phải lỗi — trợ lý nói "không có thông tin".

    `source_type` / `company_id` lọc ở tầng SQL **trước** khi tìm; sàng lại trong Python thì
    top-5 của một công ty sẽ chỉ còn 0–1 dòng. `hybrid=False` chỉ dùng để đo riêng từng nhánh.

    `workspace_id` không phải bộ lọc mà là ranh giới dữ liệu: không tắt được, đi xuống tận
    `WHERE` của cả hai nhánh.
    """
    text_query = (query or "").strip()
    if not text_query:
        return []

    candidates = max(top_k * CANDIDATE_MULTIPLIER, top_k)
    vector_hits = await vector_search(
        db,
        text_query,
        workspace_id=workspace_id,
        top_k=candidates,
        min_similarity=min_similarity,
        source_type=source_type,
        company_id=company_id,
        client=client,
    )
    text_hits = (
        await text_search(
            db,
            text_query,
            workspace_id=workspace_id,
            top_k=candidates,
            source_type=source_type,
            company_id=company_id,
        )
        if hybrid
        else []
    )

    fused = _fuse(vector_hits, text_hits)[:top_k]
    logger.debug(
        "Truy hồi %r: %d vector + %d full-text → %d kết quả",
        text_query,
        len(vector_hits),
        len(text_hits),
        len(fused),
    )
    return fused


async def vector_search(
    db: AsyncSession,
    query: str,
    *,
    workspace_id: uuid.UUID,
    top_k: int = TOP_K,
    min_similarity: float = MIN_SIMILARITY,
    source_type: KBSourceType | str | None = None,
    company_id: uuid.UUID | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[Hit]:
    """Nhánh vector: nhúng câu hỏi → top-k cosine → cắt theo ngưỡng.

    Dùng `embed_query()` chứ không `embed_passages()`: họ `e5` cần đúng tiền tố `query:`.
    Nhầm vai không sinh lỗi, chỉ làm chất lượng truy hồi tụt không truy ra được.
    """
    vector = await embeddings.embed_query(query, client=client)
    rows = await kb_repo.search_similar(
        db,
        vector,
        workspace_id=workspace_id,
        top_k=top_k,
        source_type=source_type,
        company_id=company_id,
    )

    hits: list[Hit] = []
    for chunk, distance in rows:
        similarity = 1.0 - distance
        if similarity < min_similarity:
            # Đã sắp theo khoảng cách tăng dần: cái đầu rớt ngưỡng thì mọi cái sau cũng rớt.
            break
        hits.append(_hit(chunk, score=0.0, similarity=similarity, matched_by="vector"))

    if rows and not hits:
        logger.info(
            "Không chunk nào đạt ngưỡng %.2f cho %r (gần nhất %.3f)",
            min_similarity,
            query,
            1.0 - rows[0][1],
        )
    return hits


async def text_search(
    db: AsyncSession,
    query: str,
    *,
    workspace_id: uuid.UUID,
    top_k: int = TOP_K,
    source_type: KBSourceType | str | None = None,
    company_id: uuid.UUID | None = None,
) -> list[Hit]:
    """Nhánh full-text: khớp từ khoá trên `kb_chunks.content`.

    Không đặt ngưỡng điểm — `ts_rank_cd` không có thang tuyệt đối. Bù lại nhánh này chỉ nhận từ
    khoá chọn lọc (`query_terms`), nên câu hỏi thuần ngữ nghĩa sẽ **không trả gì** và nhường
    hẳn cho nhánh vector.
    """
    rows = await kb_repo.search_fulltext(
        db,
        query_terms(query),
        workspace_id=workspace_id,
        top_k=top_k,
        source_type=source_type,
        company_id=company_id,
    )
    return [_hit(chunk, score=0.0, text_rank=rank, matched_by="text") for chunk, rank in rows]


def query_terms(query: str) -> list[str]:
    """Rút các từ khoá **đáng tìm nguyên văn** từ câu hỏi, theo thứ tự chọn lọc giảm dần.

    Lọc chứ không đưa cả câu xuống: `AND` mọi từ thì chunk phải chứa từng chữ một, `OR` mọi từ
    thì `ts_rank_cd` (không có IDF) trả về gần hết KB theo thứ tự gần như ngẫu nhiên.

    Giữ 5 loại: email, số điện thoại (**cả bản gõ tay lẫn bản E.164**, vì KB lưu bản chuẩn hoá),
    dãy ≥ 6 chữ số, tên miền, và từ viết hoa không đứng đầu câu (phỏng đoán tên riêng).
    Gõ toàn chữ thường thì (5) trượt — chấp nhận được, nhánh vector vẫn chạy.
    """
    remaining = query or ""
    terms: list[str] = []

    for pattern in (_EMAIL_RE, _PHONE_LIKE_RE, _LONG_DIGITS_RE, _DOMAIN_RE):
        for raw in pattern.findall(remaining):
            _append(terms, raw.strip())
            if pattern is _PHONE_LIKE_RE:
                _append(terms, normalize.normalize_phone(raw, region=QUERY_PHONE_REGION))
        # Cắt phần đã nhận ra khỏi chuỗi để regex sau không xé lại nó thành mảnh vụn.
        remaining = pattern.sub(" ", remaining)

    for index, word in enumerate(_WORD_RE.findall(remaining)):
        if index > 0 and word[0].isupper() and word.casefold() not in NON_SELECTIVE_WORDS:
            _append(terms, word)

    return terms[:MAX_QUERY_TERMS]


def _append(terms: list[str], value: str | None) -> None:
    """Thêm một từ khoá nếu nó có nội dung và chưa có trong danh sách (không phân biệt hoa thường)."""
    term = (value or "").strip()
    if term and all(term.casefold() != existing.casefold() for existing in terms):
        terms.append(term)


# --------------------------------------------------------------------------- nội bộ


def _fuse(vector_hits: Sequence[Hit], text_hits: Sequence[Hit]) -> list[Hit]:
    """Reciprocal Rank Fusion hai danh sách đã xếp hạng → một danh sách.

    `score = Σ 1 / (RRF_K + thứ hạng)`: chunk hạng 3 ở **cả hai** nhánh xếp trên chunk hạng 1 ở
    một nhánh. Nhánh kia rỗng thì thứ tự nhánh vector giữ nguyên.
    """
    merged: dict[uuid.UUID, Hit] = {}
    scores: dict[uuid.UUID, float] = {}

    for hits in (vector_hits, text_hits):
        for rank, hit in enumerate(hits, start=1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (RRF_K + rank)
            previous = merged.get(hit.chunk_id)
            merged[hit.chunk_id] = hit if previous is None else _combine(previous, hit)

    return sorted(
        (
            Hit(
                chunk_id=hit.chunk_id,
                source_type=hit.source_type,
                source_id=hit.source_id,
                content=hit.content,
                meta=hit.meta,
                score=scores[chunk_id],
                similarity=hit.similarity,
                text_rank=hit.text_rank,
                matched_by=hit.matched_by,
            )
            for chunk_id, hit in merged.items()
        ),
        key=lambda hit: hit.score,
        reverse=True,
    )


def _combine(first: Hit, second: Hit) -> Hit:
    """Gộp hai bản ghi của cùng một chunk do hai nhánh trả về."""
    return Hit(
        chunk_id=first.chunk_id,
        source_type=first.source_type,
        source_id=first.source_id,
        content=first.content,
        meta=first.meta,
        score=0.0,  # `_fuse` gán điểm thật sau khi cộng xong mọi nhánh
        similarity=first.similarity if first.similarity is not None else second.similarity,
        text_rank=first.text_rank if first.text_rank is not None else second.text_rank,
        matched_by="both",
    )


def _hit(
    chunk: KBChunk,
    *,
    score: float,
    similarity: float | None = None,
    text_rank: float | None = None,
    matched_by: str,
) -> Hit:
    return Hit(
        chunk_id=chunk.id,
        source_type=chunk.source_type,
        source_id=chunk.source_id,
        content=chunk.content,
        meta=chunk.meta or {},
        score=score,
        similarity=similarity,
        text_rank=text_rank,
        matched_by=matched_by,
    )
