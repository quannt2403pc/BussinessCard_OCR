"""Trợ lý hỏi–đáp (F3): truy hồi → dựng ngữ cảnh → gọi Gemini → rút trích dẫn.

Chủ sở hữu: Q | Task: 8.2 (lõi), 8.3 (lịch sử hội thoại) | xem Task.md

Nằm giữa `services/retriever.py` (D7, tìm chunk) và `routers/chat.py` (HTTP). Tách ra khỏi
router theo đúng lối `cards.py` → `services/ocr.py`: toàn bộ phần đáng test của D8 nằm ở đây và
test được mà không cần dựng request HTTP nào.

Ba quyết định định hình chất lượng câu trả lời:

1. **Không tìm được chunk nào thì KHÔNG gọi model.** Trả thẳng câu "không có thông tin". Gọi
   model với ngữ cảnh rỗng là trả tiền để nghe nó nói bằng kiến thức nội tại của chính nó —
   đúng kịch bản bịa của rủi ro R4.

2. **Trích dẫn rút từ dấu `[n]` model tự đặt, không phải từ danh sách chunk đã truy hồi.** Tầng
   truy hồi luôn trả về 5 chunk kể cả với câu hỏi lạc đề (đo ở 7.4 — xem `prompts/assistant.py`),
   nên "đã truy hồi" không đồng nghĩa "đã dùng". Liệt kê cả 5 chunk thành trích dẫn là gắn nguồn
   cho một câu trả lời không đọc chúng.

3. **Câu hỏi gửi cho tầng truy hồi ≠ câu hỏi gửi cho model.** Lượt sau của một hội thoại thường
   không tự đứng được ("Số điện thoại của *chị ấy* là gì?"): đem nguyên văn đi tìm thì không có
   gì để bám, cả nhánh vector lẫn nhánh từ khoá đều trượt. Xem `retrieval_query()`.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatRole
from app.models.kb import KBSourceType
from app.prompts import assistant as prompt
from app.repositories import kb as kb_repo
from app.services import llm, retriever, user_credentials
from app.services.retriever import Hit

logger = logging.getLogger(__name__)

#: Nhiệt độ khi sinh câu trả lời. Không đặt 0.0 như OCR: câu trả lời là văn xuôi, ở 0.0 model
#: hay kẹt vào một khuôn câu và lặp lại. 0.1 vẫn là "trích xuất có diễn đạt", không phải sáng tác.
TEMPERATURE = 0.1

#: Trần độ dài câu trả lời. Đủ cho một danh sách vài công ty kèm trích dẫn; chặn ở đây để một
#: lượt hỏng không ngốn hết hạn mức của cả buổi demo.
MAX_OUTPUT_TOKENS = 1024

#: Số lượt (user + assistant) đưa vào prompt ở phần lịch sử. 6 = 3 cặp hỏi–đáp: đủ để bắt được
#: đại từ trỏ ngược ("chị ấy", "công ty đó") mà không đẩy ngữ cảnh thật xuống cuối prompt.
HISTORY_TURNS = 6

#: Trần ký tự mỗi lượt lịch sử khi rút gọn. Câu trả lời cũ có thể dài cả đoạn; giữ nguyên thì
#: ba cặp hỏi–đáp đã dài hơn toàn bộ ngữ cảnh thật.
HISTORY_CHAR_LIMIT = 300

#: Độ dài đoạn trích hiện trên thẻ trích dẫn ở UI (task 8.4).
SNIPPET_CHARS = 240

#: Dấu trích dẫn: `[2]`, `[1][3]`, `[1, 3]`, `[1;3]`. Bắt rộng rồi lọc số hợp lệ sau — model
#: viết kiểu nào cũng phải đọc được, vì bỏ sót một dấu nghĩa là mất một trích dẫn có thật.
_MARKER_RE = re.compile(r"\[\s*(\d+(?:\s*[,;]\s*\d+)*)\s*\]")


@dataclass(frozen=True, slots=True)
class Turn:
    """Một lượt đã có trong hội thoại, đưa vào prompt ở phần lịch sử (task 8.3)."""

    role: str
    content: str


@dataclass(frozen=True, slots=True)
class Citation:
    """Một nguồn mà câu trả lời thật sự trích dẫn."""

    source_type: str
    source_id: uuid.UUID
    title: str
    snippet: str
    url: str
    #: Tương đồng cosine của chunk được trích. `None` khi chunk chỉ khớp ở nhánh full-text —
    #: lúc đó **không có** điểm cosine nào để báo, và bịa ra một con số cho đủ trường thì người
    #: đọc không phân biệt được nó với điểm thật. Cố ý không trả điểm RRF: nó phụ thuộc số nhánh
    #: khớp nên không so sánh được giữa hai lượt hỏi, in ra chỉ gây hiểu nhầm.
    score: float | None = None
    #: URL nguồn ngoài của hồ sơ DN (`metadata.sources`), rỗng với danh thiếp.
    source_urls: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Answer:
    """Kết quả một lượt hỏi."""

    text: str
    citations: list[Citation]
    #: Số chunk đưa vào ngữ cảnh. 0 nghĩa là không gọi model (xem quyết định 1 ở đầu file).
    context_chunks: int = 0
    model: str = ""
    elapsed_ms: int = 0


async def answer(
    db: AsyncSession,
    question: str,
    *,
    user_id: uuid.UUID,
    history: Sequence[Turn] = (),
    top_k: int = retriever.TOP_K,
    source_type: KBSourceType | str | None = None,
    company_id: uuid.UUID | None = None,
    client: httpx.AsyncClient | None = None,
) -> Answer:
    """Trả lời một câu hỏi dựa trên Knowledge Base.

    Ném `llm.LLMError` / `embeddings.EmbeddingError` ra ngoài cho router dịch sang mã HTTP —
    người dùng cần biết "chưa kết nối OAuth" khác "embedder chết", mà chỉ router mới biết trả
    503 hay 500.

    `user_id` (task 12.5) giới hạn **toàn bộ** ngữ cảnh trong dữ liệu của chính người hỏi. Nó đi
    xuống cả hai chỗ đọc DB dưới đây, kể cả câu đếm "KB rỗng?" — đếm toàn cục thì người dùng mới
    đăng ký, chưa có dữ liệu nào, sẽ nhận câu "không tìm thấy thông tin liên quan" (hàm ý *có* dữ
    liệu nhưng không khớp) thay vì lời mời quét danh thiếp đầu tiên.
    """
    started = time.perf_counter()
    text = (question or "").strip()
    if not text:
        raise ValueError("Câu hỏi rỗng")

    hits = await retriever.search(
        db,
        retrieval_query(text, history),
        user_id=user_id,
        top_k=top_k,
        source_type=source_type,
        company_id=company_id,
        client=client,
    )

    if not hits:
        # Phân biệt "KB rỗng" với "KB có dữ liệu nhưng không liên quan": hai tình huống này đòi
        # người dùng làm hai việc khác hẳn nhau. Chỉ đếm khi đã chắc là không có kết quả nên
        # không thêm một câu SQL vào đường đi thường.
        empty_kb = await kb_repo.count_chunks(db, user_id=user_id) == 0
        return Answer(
            text=prompt.EMPTY_KB_TEXT if empty_kb else prompt.NO_ANSWER_TEXT,
            citations=[],
            elapsed_ms=_ms_since(started),
        )

    context = prompt.build_context([context_block(hit) for hit in hits])
    # Giữ lại tên model để **khai đúng thứ đã gọi** ở `Answer.model` bên dưới: trước EX-14 chỗ đó
    # đọc thẳng `settings.llm_model`, mà từ EX-14 thì người dùng chọn model riêng cho trợ lý (I-34).
    model = await user_credentials.model_for_user_id(db, user_id, "chat")
    raw = await llm.generate_text(
        prompt.build_prompt(text, context, history=history_text(history)),
        model=model,
        system=prompt.SYSTEM_PROMPT,
        temperature=TEMPERATURE,
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )

    answer_text, citations = extract_citations(raw, hits)
    if not citations:
        # Không phải lỗi — câu từ chối đúng quy tắc 2 cũng không có trích dẫn nào. Nhưng nếu câu
        # trả lời dài mà vẫn trống trích dẫn thì đó là model quên quy tắc 4, và
        # `docs/qa-testset.md` chấm trượt câu đó. Ghi log để lúc đo A6 biết đường tìm.
        logger.info(
            "Câu trả lời không có dấu trích dẫn nào (%d chunk ngữ cảnh): %r", len(hits), text
        )

    return Answer(
        text=answer_text,
        citations=citations,
        context_chunks=len(hits),
        model=llm.base_model(model),
        elapsed_ms=_ms_since(started),
    )


def retrieval_query(question: str, history: Sequence[Turn] = ()) -> str:
    """Câu đem đi tìm trong KB — ghép **lượt hỏi trước** vào câu hỏi hiện tại nếu có (task 8.3).

    Lý do rất cụ thể, lấy từ cặp câu kiểm thử ở `docs/qa-testset.md` mục 6:

        lượt 1: "Ai là trưởng phòng marketing của Sữa Mộc Châu?"
        lượt 2: "Số điện thoại của chị ấy là gì?"

    Đem nguyên văn lượt 2 đi tìm thì nhánh vector chỉ thấy một câu hỏi chung chung về số điện
    thoại, còn `query_terms()` không rút được định danh nào — cả hai nhánh cùng trượt, và model
    nhận về ngữ cảnh của người khác. Ghép lượt hỏi trước vào là đủ để "Sữa Mộc Châu" quay lại
    trong câu truy vấn.

    **Đánh đổi đã biết:** khi người dùng đổi hẳn chủ đề, câu hỏi cũ thành nhiễu kéo truy hồi về
    phía sai. Chấp nhận được vì nhánh vector vẫn xếp hạng theo cả câu, và top-k lấy 5 chunk nên
    chủ đề mới vẫn có chỗ. Cách chặt chẽ hơn là để model tự viết lại câu hỏi thành câu đứng độc
    lập — thêm một lời gọi LLM cho mỗi lượt hỏi, không đáng với bản demo. Đo lại ở A6 nếu thấy
    lượt sau hay trả lời lạc.

    Chỉ lấy lượt **hỏi** gần nhất, không lấy câu trả lời: câu trả lời dài hơn nhiều và thường
    chứa cả những dữ kiện lạc, đủ để lấn át câu hỏi thật trong vector.
    """
    text = (question or "").strip()
    previous = [turn.content.strip() for turn in history if turn.role == ChatRole.USER.value]
    if not previous or not previous[-1]:
        return text
    return f"{previous[-1]} {text}"


def history_text(history: Sequence[Turn]) -> str:
    """Rút gọn các lượt trước thành văn bản nhét vào prompt (task 8.3)."""
    recent = list(history)[-HISTORY_TURNS:]
    lines = []
    for turn in recent:
        content = " ".join(turn.content.split())
        if not content:
            continue
        label = "Người dùng" if turn.role == ChatRole.USER.value else "Trợ lý"
        lines.append(f"{label}: {_cut(content, HISTORY_CHAR_LIMIT)}")
    return "\n".join(lines)


def context_block(hit: Hit) -> str:
    """Nội dung một khối ngữ cảnh.

    Dùng thẳng `hit.content`: `services/kb.py` đã lặp dòng tiêu đề vào đầu **mọi** chunk đúng để
    khối đứng một mình vẫn biết nó nói về ai. Thêm nhãn loại nguồn ở đầu vì model cần phân biệt
    "danh thiếp của một người" với "hồ sơ của cả công ty" — hai thứ trả lời hai kiểu câu hỏi
    khác nhau, mà tiêu đề tiếng Việt thì đã nói rõ điều đó rồi nên không lặp lại nữa.
    """
    return hit.content.strip()


def extract_citations(raw: str, hits: Sequence[Hit]) -> tuple[str, list[Citation]]:
    """Đọc dấu `[n]` trong câu trả lời → danh sách trích dẫn, và **đánh số lại** các dấu đó.

    Vì sao phải đánh số lại chứ không giữ nguyên số model viết: `n` model dùng là số hiệu **khối
    ngữ cảnh**, mà nhiều khối có thể cùng một nguồn (một hồ sơ dài bị cắt thành 2-3 chunk). Thẻ
    trích dẫn ở UI hiện theo **nguồn**, nên sau khi gộp thì `[3]` trong câu chữ trỏ tới thẻ
    không tồn tại. Đánh số lại làm dấu trong câu và thẻ bên dưới luôn khớp nhau.

    Dấu trỏ ra ngoài phạm vi (`[9]` khi chỉ có 5 khối — model đôi khi tự chế) bị **bỏ hẳn khỏi
    câu chữ** thay vì để lại: một dấu trích dẫn không bấm được trông y hệt một dấu bấm được, và
    người đọc sẽ tin vào nó.

    Trả `(câu trả lời đã đánh số lại, danh sách trích dẫn theo đúng thứ tự xuất hiện)`.
    """
    order: list[tuple[str, uuid.UUID]] = []
    chosen: dict[tuple[str, uuid.UUID], Hit] = {}

    def _renumber(match: re.Match[str]) -> str:
        numbers = []
        for piece in re.split(r"[,;]", match.group(1)):
            index = int(piece.strip())
            if not 1 <= index <= len(hits):
                continue
            hit = hits[index - 1]
            key = (hit.source_type, hit.source_id)
            if key not in chosen:
                # Giữ chunk **đầu tiên** được trích của mỗi nguồn: nó là chunk model thật sự đọc
                # để viết ra câu đó, nên đoạn trích trên thẻ khớp với chữ người dùng vừa đọc.
                chosen[key] = hit
                order.append(key)
            numbers.append(order.index(key) + 1)
        return "".join(f"[{number}]" for number in dict.fromkeys(numbers))

    text = _MARKER_RE.sub(_renumber, raw).strip()
    # Dọn khoảng trắng thừa để lại khi một dấu bị gỡ hẳn ("... Đại Việt  ." → "... Đại Việt.").
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)

    return text, [_citation(chosen[key]) for key in order]


# --------------------------------------------------------------------------- nội bộ


def _citation(hit: Hit) -> Citation:
    return Citation(
        source_type=hit.source_type,
        source_id=hit.source_id,
        title=hit.title or prompt.source_label(hit.source_type),
        snippet=_snippet(hit),
        url=source_url(hit.source_type, hit.source_id),
        score=round(hit.similarity, 3) if hit.similarity is not None else None,
        source_urls=hit.source_urls,
    )


def source_url(source_type: str, source_id: uuid.UUID) -> str:
    """Đường dẫn trang mở khi bấm vào thẻ trích dẫn.

    ⚠️ `source_id` mang hai thứ khác nhau tuỳ loại nguồn: id **danh thiếp** với chunk danh thiếp,
    nhưng id **công ty** với chunk hồ sơ DN (`services/kb.py::build_profile_document` neo theo
    công ty để bấm "Tạo lại hồ sơ" không để lại hồ sơ cũ trong KB). Nhầm chỗ này thì link trích
    dẫn trả 404 cho đúng một nửa số nguồn — `docs/qa-testset.md` chấm trượt vì bấm không mở được.
    """
    if source_type == KBSourceType.COMPANY_PROFILE.value:
        return f"/companies/{source_id}"
    return f"/cards/{source_id}"


def _snippet(hit: Hit) -> str:
    """Đoạn trích hiện trên thẻ: bỏ dòng tiêu đề (đã hiện riêng), cắt còn `SNIPPET_CHARS`."""
    lines = hit.content.strip().splitlines()
    body = "\n".join(lines[1:]).strip() if len(lines) > 1 else hit.content.strip()
    return _cut(
        " · ".join(line.strip() for line in body.splitlines() if line.strip()), SNIPPET_CHARS
    )


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1].rstrip()}…"


def _ms_since(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
