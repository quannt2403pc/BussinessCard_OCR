"""Truy hồi cho RAG: tìm theo vector, tìm theo từ khoá, trộn hai kết quả.

Chủ sở hữu: Q | Task: 7.1 (vector + ngưỡng điểm), 7.2 (hybrid full-text), 12.5 (tách theo người
dùng) | xem Task.md

Đây là lớp giữa `repositories/kb.py` (câu SQL) và `routers/chat.py` (D8): nhận **câu hỏi bằng
chữ**, trả về các chunk đáng đưa vào ngữ cảnh của model, đã xếp hạng. Không gọi LLM, không dựng
prompt — D8 làm việc đó.

Vì sao phải có **hai** nhánh tìm, chứ không chỉ vector:

| Câu hỏi | Nhánh bắt được | Vì sao nhánh kia trượt |
|---------|----------------|------------------------|
| "công ty nào làm về logistics?" | vector | không có từ "logistics" nào trùng hình thức trong hồ sơ viết bằng tiếng Việt |
| "ai dùng email a.nguyen@abc.vn?" | full-text | một địa chỉ email là chuỗi ngẫu nhiên, model nhúng không có ngữ nghĩa nào để bám |
| "số 0912 345 678 là của ai?" | full-text (sau khi chuẩn hoá) | như trên |
| "mã số thuế 0301234567" | full-text | vector coi mọi dãy 10 chữ số gần như nhau |
| "cong ty san xuat sua" (không dấu) | **cả hai đều trượt** | `to_tsvector('simple', …)` không bỏ dấu; model nhúng cũng không kéo nổi tiếng Việt mất dấu về đúng chỗ — đo ở 7.4, xem ghi chú dưới |

Hai nhánh hỏng theo hai kiểu khác hẳn nhau, nên trộn lại thì chỗ này che được chỗ kia — đó là
toàn bộ lý do của task 7.2, và cũng là lý do **không** thay full-text bằng cách hạ ngưỡng vector.
Số đo 7.4 xác nhận: recall@3 **7/10 khi chỉ dùng vector → 9/10 khi trộn**.

⚠️ **Một ca đo được là cả hai nhánh cùng trượt: câu hỏi tiếng Việt gõ không dấu.** "cong ty nao
san xuat sua" không lọt top-5 dù hồ sơ Mộc Châu nằm sẵn trong KB. Nhánh full-text trượt vì
`simple` không bỏ dấu, nhánh vector trượt vì `e5-small` không coi "san xuat sua" gần "sản xuất
sữa". Gỡ được bằng extension `unaccent` của Postgres (một cột/biểu thức đã bỏ dấu cho nhánh
full-text) — cần một Alembic revision, nên để lại cho D9/D10 quyết theo kết quả đo A6 của D8,
chứ không lặng lẽ nhét vào 7.2.

Trộn bằng **Reciprocal Rank Fusion**, không cộng điểm thô: điểm cosine nằm trong `[0, 1]` còn
`ts_rank_cd` không có trần, cộng thẳng là để nhánh full-text nuốt trọn thứ tự. RRF chỉ nhìn
*thứ hạng* nên không cần chuẩn hoá thang điểm giữa hai thứ vốn không so sánh được với nhau.
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

#: Số chunk trả về mặc định. 5 chunk × ~700 ký tự ≈ 3 500 ký tự ngữ cảnh — đủ để trả lời một
#: câu hỏi về vài công ty mà chưa chạm giới hạn nào của Gemini Flash.
TOP_K = 5

#: Mỗi nhánh lấy `top_k × hệ số này` ứng viên trước khi trộn. Lấy đúng `top_k` mỗi nhánh thì
#: RRF không còn gì để xếp lại: một chunk đứng hạng 6 ở cả hai nhánh (dấu hiệu rất đáng tin)
#: sẽ không bao giờ lọt vào danh sách để được cộng điểm.
CANDIDATE_MULTIPLIER = 4

#: Ngưỡng điểm tương đồng cosine của **nhánh vector** (task 7.1 yêu cầu có ngưỡng).
#:
#: **Đây là sàn an toàn, KHÔNG phải bộ phân loại "có trong KB hay không".** Số đo 7.4 ngày
#: 2026-09-17 (`scripts/eval_retrieval.py`, `multilingual-e5-small`, 7 nguồn, 13 truy vấn):
#:
#:     chunk ĐÚNG   : thấp nhất 0.773 · cao nhất 0.910
#:     câu NGOÀI KB : cao nhất  0.819  ("hướng dẫn nấu phở bò")
#:
#: Hai phân bố **chồng lên nhau**: mọi ngưỡng chặn được câu hỏi ngoài phạm vi cũng chặn luôn
#: câu trả lời đúng cho "mã số thuế 0100233468" (0.773). Họ `e5` cho điểm nền rất cao — hai
#: đoạn văn chẳng liên quan gì vẫn quanh 0.75–0.82 — nên không có con số nào tách được.
#:
#: Hệ quả cho **D8**: việc quyết định "không có thông tin trong dữ liệu đã nhập" phải nằm ở
#: prompt của trợ lý (model tự thấy ngữ cảnh không chứa câu trả lời), **không** trông vào ngưỡng
#: này. Đừng nâng nó lên cho "chắc ăn": nâng là mất recall thật, còn câu hỏi lạc đề vẫn lọt.
#:
#: 0.70 đặt dưới điểm đúng thấp nhất đo được một quãng an toàn, nên với KB thật nó gần như
#: không bao giờ kích hoạt; chỗ nó thật sự có tác dụng là khi KB rỗng hoặc quá nhỏ.
MIN_SIMILARITY = 0.70

#: Hằng số làm mượt của RRF. 60 là giá trị trong bài báo gốc và là mặc định của mọi thư viện;
#: giữ nguyên để khỏi tự chế một thang điểm không ai đối chiếu được. Càng lớn thì chênh lệch
#: giữa các thứ hạng đầu càng phẳng.
RRF_K = 60

#: Vùng mặc định khi đọc số điện thoại **trong câu hỏi**. Câu hỏi không có trường
#: `language_detected` như danh thiếp nên không suy ra được vùng; KB của bản demo là danh thiếp
#: thu ở hội thảo trong nước nên `VN` là phỏng đoán đúng gần như mọi lúc. Đoán sai thì chỉ mất
#: dạng chuẩn hoá, bản người dùng gõ vẫn được tìm.
QUERY_PHONE_REGION = "VN"

#: Trần số từ khoá gửi xuống nhánh full-text. Câu hỏi thật không bao giờ chứa nhiều hơn chừng
#: này *định danh*; chạm trần nghĩa là bộ lọc đang bắt nhầm chữ thường thành tên riêng, và một
#: tsquery `OR` dài thì khớp gần hết KB.
MAX_QUERY_TERMS = 8

#: Từ **không** được coi là từ khoá dù viết hoa: đây là nhãn trường mà `services/kb.py` in vào
#: **mọi** chunk ("Email:", "Điện thoại:", "Công ty:"…), cộng vài từ để hỏi.
#:
#: Đây là lập luận IDF phiên bản rẻ tiền: token có mặt trong mọi tài liệu thì không phân biệt
#: được tài liệu nào, mà `ts_rank_cd` lại **không** tự hạ trọng số những token như vậy. Bỏ qua
#: bước này thì câu "Ai dùng Email X" kéo về đúng mọi danh thiếp trong KB.
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

#: Dãy trông như số điện thoại: mở đầu bằng chữ số hoặc `+`, dài tối thiểu 7 ký tự kể cả dấu
#: cách/gạch. Cố ý bắt rộng rồi để `normalize.normalize_phone()` phán quyết — nó có
#: `phonenumbers` để kiểm tra thật, còn regex ở đây chỉ khoanh vùng.
_PHONE_LIKE_RE = re.compile(r"(?<![\w+])\+?\d[\d\s().\-]{5,}\d(?!\w)")

#: Email — bắt trước mọi thứ khác, vì nó chứa cả dấu chấm lẫn ký tự chữ nên hai regex dưới đều
#: sẽ xé nó ra thành mảnh vụn nếu đi sau.
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

    #: Điểm trộn RRF — **thứ tự cuối cùng theo điểm này**. Không so sánh được giữa hai lượt tìm
    #: khác nhau (nó phụ thuộc số nhánh khớp), chỉ dùng để xếp hạng trong cùng một lượt.
    score: float
    #: Tương đồng cosine `1 - khoảng cách`, `None` khi chunk chỉ khớp ở nhánh full-text.
    similarity: float | None = None
    #: `ts_rank_cd`, `None` khi chunk chỉ khớp ở nhánh vector.
    text_rank: float | None = None
    #: `"vector"` | `"text"` | `"both"` — nhánh nào tìm ra. Hiện trong log và trong báo cáo đo
    #: của 7.4; khớp ở **cả hai** là tín hiệu tin cậy nhất.
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

    Trả về **danh sách rỗng** khi không có gì đủ liên quan — đó là một câu trả lời hợp lệ, không
    phải lỗi. D8 nhận rỗng thì nói "không có thông tin trong dữ liệu đã nhập" thay vì để model
    tự bịa từ mấy chunk gần nhất.

    `source_type` / `company_id` là bộ lọc metadata của task 8.5 — thu hẹp KB **trước** khi tìm
    (chỉ danh thiếp, chỉ hồ sơ DN, hoặc chỉ một công ty). Lọc ở tầng SQL chứ không sàng lại kết
    quả trong Python: sàng sau thì hỏi top-5 trong phạm vi một công ty sẽ nhận về 0–1 dòng vì
    bốn chỗ đã bị các công ty khác chiếm mất.

    `hybrid=False` tắt nhánh full-text; chỉ dùng để **đo riêng từng nhánh** ở task 7.4.

    `workspace_id` **không phải bộ lọc** như hai tham số trên, mà là ranh giới dữ liệu (task 12.5): nó
    không đến từ lựa chọn nào trên giao diện, không tắt được, và đi xuống tận mệnh đề `WHERE` của
    cả hai nhánh. Tiêu chí **A9** đứng trên đúng một dòng này — A hỏi về công ty mà chỉ B có thì
    truy hồi phải trả **rỗng**, để prompt của 8.1 nói "không có thông tin".
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
    """Nhánh vector (task 7.1): nhúng câu hỏi → top-k cosine → cắt theo ngưỡng.

    Câu hỏi đi qua `embeddings.embed_query()` chứ không `embed_passages()`: service `embedder`
    tự gắn tiền tố `query:` / `passage:` theo `kind`, mà họ model `e5` được huấn luyện với hai
    tiền tố đó cho hai vai trò khác nhau. Nhầm vai không sinh ra lỗi nào, chỉ làm chất lượng
    truy hồi tụt đi một cách không truy ra được (Plan.md mục 2.6).
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
            # Danh sách đã sắp theo khoảng cách tăng dần: cái đầu tiên rớt ngưỡng thì mọi cái
            # sau cũng rớt.
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
    """Nhánh full-text (task 7.2): khớp từ khoá trên `kb_chunks.content`.

    **Không có ngưỡng điểm ở nhánh này, và đó là chủ ý.** `ts_rank_cd` không có thang tuyệt đối
    (nó phụ thuộc độ dài văn bản và số từ trong truy vấn) nên mọi ngưỡng đặt ra đều là số bịa.
    Bù lại, nhánh này chỉ nhận các từ khoá **chọn lọc** của câu hỏi (`query_terms`) và chỉ trả
    về chunk thật sự chứa chúng — bản thân việc khớp đã là bộ lọc, khác hẳn nhánh vector vốn
    luôn trả đủ k dòng.

    Câu hỏi không có định danh nào ("công ty nào làm logistics?") thì nhánh này **không trả gì**
    và nhường hẳn cho nhánh vector. Đó là kết quả đúng chứ không phải thiếu sót: nó không có
    công cụ nào để trả lời một câu hỏi thuần ngữ nghĩa.
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

    Lọc chứ không đưa cả câu xuống, vì cả hai cách ghép tsquery đều hỏng với văn xuôi: `AND`
    mọi từ thì chunk phải chứa từng chữ một (kể cả "ai", "dùng"); `OR` mọi từ thì `ts_rank_cd` —
    vốn **không có IDF** — tính "ai" ngang với một địa chỉ email, và nhánh này trả về gần hết KB
    theo thứ tự gần như ngẫu nhiên. Đo cả hai ở task 7.4.

    Năm loại được giữ, đúng những thứ task 7.2 ghi là nhánh vector hay trượt:

    1. **Email** — bắt trước tiên, vì hai regex sau sẽ xé nó thành mảnh nếu chạy trước.
    2. **Số điện thoại**, giữ **cả hai dạng**: bản người dùng gõ và bản E.164 do
       `normalize.normalize_phone()` sinh. KB lưu bản đã chuẩn hoá ở task 3.6 (`+84912345678`)
       còn người hỏi gõ như in trên thẻ (`0912 345 678`) — thiếu bước này thì nhánh full-text
       tách thành ba token `0912`/`345`/`678` và không khớp gì. Giữ cả bản gốc vì danh thiếp
       nước ngoài mà `phonenumbers` không đọc được thì KB đang lưu đúng chữ người dùng gõ.
    3. **Dãy ≥ 6 chữ số** — mã số thuế, mã doanh nghiệp.
    4. **Tên miền** — `abc.vn`, `fptsoftware.com`.
    5. **Từ viết hoa không đứng đầu câu** — phỏng đoán tên riêng ("Vinamilk", "FPT", "Hòa Phát").
       Bỏ từ đầu câu vì ai cũng viết hoa nó, và bỏ nhãn trường (`NON_SELECTIVE_WORDS`).

    Giới hạn đã biết của (5): gõ toàn chữ thường thì tên riêng không được nhận ra. Chấp nhận
    được — lúc đó nhánh vector vẫn chạy, chỉ mất phần bổ trợ. Đoán rộng hơn thì nhánh full-text
    kéo về cả KB, kiểu hỏng tệ hơn hẳn.
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

    `score = Σ 1 / (RRF_K + thứ hạng)` cộng dồn trên mọi nhánh tìm ra chunk đó. Hệ quả có chủ
    đích: một chunk đứng hạng 3 ở **cả hai** nhánh xếp trên một chunh đứng hạng 1 ở đúng một
    nhánh — hai cách tìm độc lập cùng chỉ vào một chỗ là bằng chứng mạnh hơn.

    Giữ nguyên thứ tự của nhánh vector khi nhánh kia rỗng (RRF đơn điệu theo thứ hạng), nên tắt
    hybrid không làm đảo lộn gì.
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
