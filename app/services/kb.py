"""Knowledge Base cho F3: biến danh thiếp / hồ sơ DN thành văn bản, chunk, nhúng vector.

Chủ sở hữu: Q | Task: 6.2, 12.5 | xem Task.md

Hai hàm ở cuối file là **hợp đồng với T** (bảng "Ba chỗ hai người gọi code của nhau", đầu
Task.md): `ingest_card()` và `ingest_company_profile()`. T gọi hàm thứ hai ở cuối luồng enrich
(task 7.5) và không cần biết gì về chunk, tiền tố hay pgvector.

⚠️ **Task 12.5 — chữ ký hai hàm hợp đồng đó KHÔNG đổi, cố ý.** Chunk cần `workspace_id`, nhưng thay vì
thêm một tham số (mà mọi chỗ gọi phải nhớ truyền đúng, và truyền sai thì ghi dữ liệu người này
vào KB người khác), chủ sở hữu **suy ra từ chính bản ghi nguồn**: `card.workspace_id`,
`profile.workspace_id`. Hệ quả đáng giá: `services/enrich_jobs.py` của T (task 7.5) gọi
`ingest_company_profile(db, profile)` y như cũ, không phải sửa dòng nào, và **không có cách nào
gọi sai người**.

Ba quyết định đáng nêu, vì chúng quyết định chất lượng trả lời của trợ lý về sau:

1. **Mỗi chunk tự đứng được một mình.** Dòng tiêu đề (`Danh thiếp — …` / `Hồ sơ doanh nghiệp —
   …`) được lặp vào đầu **mọi** chunk. Cắt một đoạn giữa phần mô tả mà không mang theo tên công
   ty thì lúc truy hồi nó là một đoạn văn vô danh: model đọc được nội dung nhưng không biết nó
   nói về ai, và trích dẫn ra câu trả lời sai công ty.
2. **URL nguồn không nằm trong văn bản đem nhúng**, mà nằm ở `metadata.sources`. Nhúng cả đống
   link vào vector chỉ làm loãng ngữ nghĩa, trong khi D8 cần URL ở dạng đọc được để trích dẫn.
3. **Serialize bằng nhãn tiếng Việt có dấu.** Model embedding là đa ngôn ngữ (`multilingual-e5-
   small`) và câu hỏi của người dùng sẽ là tiếng Việt — nhãn `Mã số thuế:` bắt được câu hỏi
   "mã số thuế của công ty X là gì" tốt hơn hẳn nhãn `tax_code:`.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.card import BusinessCard
from app.models.company import Company, CompanyProfile
from app.models.kb import KBSourceType
from app.repositories import kb as kb_repo
from app.repositories.kb import ChunkRow
from app.services import embeddings

logger = logging.getLogger(__name__)

#: Trần ký tự một chunk. `multilingual-e5-small` cắt đầu vào ở 512 token
#: (`embedder` báo `max_seq_length` trong `/health`) — phần vượt bị **bỏ im lặng**, không lỗi.
#: 700 ký tự là mức an toàn cho cả tiếng Việt lẫn CJK: tiếng Trung/Nhật tốn nhiều token hơn hẳn
#: trên mỗi ký tự, nên lấy trần theo ngôn ngữ "đắt" nhất thay vì theo tiếng Anh.
CHUNK_MAX_CHARS = 700

#: Phần lặp lại giữa hai chunk liên tiếp khi phải cắt **giữa một đoạn văn dài** (chỉ mô tả
#: doanh nghiệp mới đủ dài để gặp). Không có phần lặp thì một câu bị cắt đôi sẽ không khớp với
#: truy vấn nào cả. Các dòng trường dữ liệu không bao giờ bị lặp — xem `chunk_text`.
CHUNK_OVERLAP_CHARS = 80

#: Số URL tối đa giữ trong metadata của một chunk. Đủ cho khối "Nguồn tham khảo" ở D8 mà không
#: phình JSONB.
MAX_SOURCE_URLS = 10

#: Tên tiếng Việt của mã ISO 639-1 mà `prompts/ocr.py` sinh ra (Plan.md mục 1.3 chốt đúng 5
#: ngôn ngữ). Ghi cả tên lẫn mã vào chunk, không chỉ mã.
#:
#: Đo ở task 10.1 (2026-09-21), câu 8 của `docs/qa-testset.md`: hỏi *"Có ai làm ở công ty Nhật
#: không?"* thì truy hồi **đúng** — thẻ `田中 太郎 · 東京テック株式会社` xếp hạng 1 — nhưng trợ lý vẫn
#: trả "Không có thông tin này trong dữ liệu đã nhập". Nguyên nhân: chunk chỉ ghi `Ngôn ngữ: ja`,
#: không chỗ nào trong ngữ cảnh nói thẻ này là **tiếng Nhật**. Muốn trả lời được thì model phải
#: tự biết `東京テック株式会社` là công ty Nhật — mà đó đúng là thứ quy tắc 1 của
#: `prompts/assistant.py` cấm. Model làm đúng luật; chỗ sai là dữ liệu.
#:
#: Vì vậy sửa ở đây chứ **không** nới quy tắc 1: nới nó ra là mở lại đường cho X3 ("mã số thuế
#: của Vinamilk") — câu chặn mà cả lượt nghiệm thu phụ thuộc vào. Ghi thêm tên ngôn ngữ giúp cả
#: hai nhánh truy hồi: nhánh vector có chữ "Nhật" để bám, nhánh full-text có token để khớp.
LANGUAGE_LABELS: dict[str, str] = {
    "vi": "Tiếng Việt",
    "en": "Tiếng Anh",
    "ko": "Tiếng Hàn",
    "ja": "Tiếng Nhật",
    "zh": "Tiếng Trung",
    "zh-tw": "Tiếng Trung (phồn thể)",
    # EX-05 — phạm vi ngôn ngữ mở ra không giới hạn. Bảng này chỉ là **nhãn hiển thị**: mã lạ
    # vẫn vào KB nguyên dạng (`_language()` giữ nguyên mã), chỉ là chunk ghi `sw` thay vì
    # "Tiếng Swahili". Thiếu một dòng ở đây không làm mất dữ liệu nào.
    "th": "Tiếng Thái",
    "ru": "Tiếng Nga",
    "de": "Tiếng Đức",
    "fr": "Tiếng Pháp",
    "es": "Tiếng Tây Ban Nha",
    "pt": "Tiếng Bồ Đào Nha",
    "it": "Tiếng Ý",
    "nl": "Tiếng Hà Lan",
    "ar": "Tiếng Ả Rập",
    "hi": "Tiếng Hindi",
    "id": "Tiếng Indonesia",
    "ms": "Tiếng Mã Lai",
    "tl": "Tiếng Philippines",
    "km": "Tiếng Khmer",
    "lo": "Tiếng Lào",
    "my": "Tiếng Miến Điện",
    "tr": "Tiếng Thổ Nhĩ Kỳ",
    "pl": "Tiếng Ba Lan",
    "cs": "Tiếng Séc",
    "sv": "Tiếng Thuỵ Điển",
    "he": "Tiếng Do Thái",
    "el": "Tiếng Hy Lạp",
    "uk": "Tiếng Ukraina",
}


@dataclass(frozen=True, slots=True)
class Chunk:
    """Một đoạn văn bản sắp nhúng, kèm metadata sẽ ghi vào `kb_chunks.metadata`."""

    content: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Document:
    """Toàn bộ chunk của **một** nguồn (một danh thiếp, hoặc một hồ sơ DN).

    Là đơn vị ghi đè: `index_documents()` xoá hết chunk cũ của `source_id` rồi ghi bộ mới.

    `workspace_id` đi kèm ngay trong đơn vị ghi (task 12.5) chứ không truyền song song: một lượt
    reindex xử lý nhiều Document, và một tham số `workspace_id` chung cho cả lô là chỗ để hai thứ
    lệch nhau khi về sau có ai gom Document của nhiều nguồn vào một lời gọi.
    """

    workspace_id: uuid.UUID
    source_type: KBSourceType
    source_id: uuid.UUID
    chunks: list[Chunk]


# --------------------------------------------------------------------------- serialize


def serialize_card(card: BusinessCard, *, company_name: str | None = None) -> tuple[str, str]:
    """Danh thiếp → `(tiêu đề, thân bài)`. Trường rỗng bị bỏ hẳn dòng.

    `company_name` là tên công ty **đã chuẩn hoá** (bảng `companies`, do T gộp ở task 3.8) —
    khác `company_name_raw` in trên thẻ. Giữ cả hai khi chúng khác nhau: người hỏi có thể gõ
    theo kiểu nào cũng được ("Cty ABC" hay "Công ty TNHH ABC").

    **Bản Việt hoá (EX-06) đi vào chunk, ngay dưới bản gốc.** Đây là chỗ nó đáng giá nhất trong
    cả hệ thống: không có nó thì câu hỏi *"Tanaka Taro làm ở đâu?"* không bao giờ khớp nổi một
    chunk chỉ chứa `田中 太郎`, kể cả với model nhúng đa ngôn ngữ — hai chuỗi không có ký tự nào
    chung. Tên Việt hoá **cũng vào dòng tiêu đề**, vì tiêu đề được lặp vào đầu mọi chunk (xem
    `_to_chunks`), nên một thẻ dài mấy chunk thì chunk nào cũng tra được bằng tên Việt.
    """
    name = (card.full_name or "").strip() or "(không rõ tên)"
    company = (company_name or card.company_name_raw or "").strip()
    title = f"Danh thiếp — {_with_vi(name, card.full_name_vi)}" + (
        f" · {_with_vi(company, card.company_name_vi)}" if company else ""
    )

    lines = [
        ("Họ tên", card.full_name),
        ("Họ tên (Việt hoá)", _other(card.full_name_vi, card.full_name)),
        ("Chức vụ", card.job_title),
        ("Chức vụ (Việt hoá)", _other(card.job_title_vi, card.job_title)),
        ("Công ty", company or None),
        # Chỉ ghi thêm khi tên in trên thẻ khác tên đã gộp — lặp y hệt chỉ tổ loãng vector.
        ("Tên công ty trên danh thiếp", _other(card.company_name_raw, company)),
        ("Tên công ty (Việt hoá)", _other(card.company_name_vi, company)),
        ("Email", card.email),
        ("Điện thoại", card.phone),
        ("Điện thoại khác", card.phone_alt),
        ("Địa chỉ", card.address),
        ("Địa chỉ (Việt hoá)", _other(card.address_vi, card.address)),
        ("Website", card.website),
        ("Ngôn ngữ", _language(card.language_detected)),
        ("Ngày thu thập", _date(card.uploaded_at)),
        ("Ghi chú", card.notes),
    ]
    return title, _lines_to_text(lines)


def serialize_company_profile(company: Company, profile: CompanyProfile) -> tuple[str, str]:
    """Hồ sơ doanh nghiệp → `(tiêu đề, thân bài)`.

    `description` để **cuối cùng**: nó là trường dài duy nhất, nên khi hồ sơ phải cắt thành
    nhiều chunk thì các trường tra cứu (MST, ngành nghề, địa chỉ) nằm gọn trong chunk đầu thay
    vì bị đẩy rải rác — câu hỏi "mã số thuế của X" chỉ cần lấy đúng một chunk là trả lời được.
    """
    # `I-36`: tiêu đề đi theo tên **đang hiện trên giao diện**, còn bản gốc xuống dòng *Tên
    # gọi khác*. Người hỏi trợ lý gõ đúng cái tên họ nhìn thấy trong danh sách; tiêu đề chữ Hàn
    # thì câu hỏi tiếng Việt không khớp được vào chunk này. Giữ cả hai để hỏi kiểu nào cũng ra.
    title = f"Hồ sơ doanh nghiệp — {_with_vi(company.display_name, company.display_name_vi)}"
    lines = [
        ("Tên công ty", company.vi_name),
        ("Tên gọi khác", _join(_other_names(company))),
        ("Tên pháp lý", _other(profile.legal_name, company.vi_name)),
        ("Mã số thuế", profile.tax_code),
        ("Năm thành lập", profile.founded_year),
        ("Quy mô", profile.size_label),
        ("Số nhân sự", profile.employee_range),
        ("Ngành nghề", _join(profile.industry)),
        ("Sản phẩm và dịch vụ", _join(profile.products)),
        ("Địa chỉ", profile.address),
        ("Website", profile.website),
        ("Điện thoại", profile.phone),
        ("Email", profile.email),
        ("Mô tả", profile.description),
    ]
    return title, _lines_to_text(lines)


def chunk_text(
    text: str,
    *,
    max_chars: int = CHUNK_MAX_CHARS,
    overlap: int = CHUNK_OVERLAP_CHARS,
) -> list[str]:
    """Cắt văn bản thành các đoạn ≤ `max_chars`, ưu tiên ranh giới dòng.

    Gom trọn dòng chừng nào còn vừa: mỗi dòng ở đây là một trường dữ liệu hoàn chỉnh
    (`Mã số thuế: …`), cắt giữa dòng là làm hỏng đúng thứ mà câu hỏi nhắm tới. Chỉ khi **một
    dòng** dài quá trần (thực tế chỉ có `Mô tả`) mới cắt bên trong nó, và chỉ khi đó mới có
    phần lặp `overlap` để câu bị cắt đôi vẫn còn khớp được ở một trong hai chunk.
    """
    if max_chars <= 0:
        raise ValueError("max_chars phải dương")
    overlap = max(0, min(overlap, max_chars // 2))

    units: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if len(line) <= max_chars:
            units.append(line)
        else:
            units.extend(_split_long_line(line, max_chars, overlap))

    chunks: list[str] = []
    current = ""
    for unit in units:
        if not current:
            current = unit
        elif len(current) + 1 + len(unit) <= max_chars:
            current = f"{current}\n{unit}"
        else:
            chunks.append(current)
            current = unit
    if current:
        chunks.append(current)
    return chunks


# --------------------------------------------------------------------------- dựng Document


def build_card_document(card: BusinessCard, *, company_name: str | None = None) -> Document:
    """Danh thiếp → `Document` sẵn sàng nhúng."""
    title, body = serialize_card(card, company_name=company_name)
    meta = {
        "kind": KBSourceType.CARD.value,
        "title": title,
        "card_id": str(card.id),
        "company_id": str(card.company_id) if card.company_id else None,
        "company_name": company_name or card.company_name_raw,
        "full_name": card.full_name,
        "uploaded_at": _iso(card.uploaded_at),
    }
    return Document(
        workspace_id=card.workspace_id,
        source_type=KBSourceType.CARD,
        source_id=card.id,
        chunks=_to_chunks(title, body, meta),
    )


def build_profile_document(company: Company, profile: CompanyProfile) -> Document:
    """Hồ sơ DN → `Document` sẵn sàng nhúng. `source_id` là **id công ty**, không phải id hồ sơ.

    Mỗi công ty chỉ có đúng một hồ sơ (`uq_company_profiles_company_id`), nhưng bấm "Tạo lại
    hồ sơ" (6.7) có thể sinh ra hàng `company_profiles` mới với id khác. Neo theo `company_id`
    thì lần ghi sau tự xoá đè bản trước; neo theo id hồ sơ thì KB giữ lại cả hồ sơ đã bị thay.
    """
    # Hai bảng, cùng một chủ sở hữu — `0005` gắn `workspace_id` cho cả `companies` lẫn
    # `company_profiles`. Lệch nhau nghĩa là dữ liệu đã hỏng ở đâu đó phía trên (một hồ sơ nối
    # sang công ty của người khác); ghi vào KB lúc đó là biến một lỗi dữ liệu thành đường rò.
    if company.workspace_id != profile.workspace_id:
        raise ValueError(
            f"Hồ sơ {profile.id} (user {profile.workspace_id}) không cùng chủ với công ty "
            f"{company.id} (user {company.workspace_id}) — không index."
        )

    title, body = serialize_company_profile(company, profile)
    meta = {
        "kind": KBSourceType.COMPANY_PROFILE.value,
        "title": title,
        "company_id": str(company.id),
        "profile_id": str(profile.id),
        "company_name": company.display_name,
        "status": profile.status,
        "generated_at": _iso(profile.generated_at),
        "sources": _source_urls(profile.sources),
    }
    return Document(
        workspace_id=profile.workspace_id,
        source_type=KBSourceType.COMPANY_PROFILE,
        source_id=company.id,
        chunks=_to_chunks(title, body, meta),
    )


# --------------------------------------------------------------------------- ghi vào KB


async def index_documents(
    db: AsyncSession,
    documents: Iterable[Document],
    *,
    client: httpx.AsyncClient | None = None,
    commit: bool = True,
) -> int:
    """Nhúng rồi ghi đè toàn bộ chunk của các `Document`. Trả về **số chunk đã ghi**.

    Gọi embedder **một lần cho cả lô** rồi mới đụng tới DB. Hai lý do, cùng một gốc:
    lời gọi mạng không được nằm trong transaction (bài học 5.4 — giữ transaction mở suốt thời
    gian gọi model), và một request 32 đoạn rẻ hơn hẳn 32 request một đoạn.

    `commit=False` khi chỗ gọi đang tự quản transaction (T gọi trong luồng enrich ở 7.5).
    """
    docs = list(documents)
    texts = [chunk.content for doc in docs for chunk in doc.chunks]
    vectors = await embeddings.embed_passages(texts, client=client) if texts else []

    written = 0
    cursor = 0
    for doc in docs:
        rows = [
            ChunkRow(content=chunk.content, embedding=vectors[cursor + offset], meta=chunk.meta)
            for offset, chunk in enumerate(doc.chunks)
        ]
        cursor += len(doc.chunks)
        written += await kb_repo.replace_chunks(
            db,
            workspace_id=doc.workspace_id,
            source_type=doc.source_type,
            source_id=doc.source_id,
            chunks=rows,
        )

    if commit:
        await db.commit()
    return written


async def ingest_card(
    db: AsyncSession,
    card: BusinessCard,
    *,
    company: Company | None = None,
    client: httpx.AsyncClient | None = None,
    commit: bool = True,
) -> int:
    """Đưa **một** danh thiếp vào KB (gọi khi người dùng xác nhận — task 7.3).

    Chạy lại nhiều lần cũng ra cùng kết quả: chunk cũ của đúng thẻ này bị xoá trước khi ghi.
    """
    if company is None and card.company_id is not None:
        company = await db.get(Company, card.company_id)
    document = build_card_document(card, company_name=company.display_name if company else None)
    return await index_documents(db, [document], client=client, commit=commit)


async def ingest_company_profile(
    db: AsyncSession,
    profile: CompanyProfile,
    *,
    company: Company | None = None,
    client: httpx.AsyncClient | None = None,
    commit: bool = True,
) -> int:
    """Đưa **một** hồ sơ doanh nghiệp vào KB — hàm T gọi ở cuối luồng enrich (task 7.5).

    Truyền sẵn `company` nếu chỗ gọi đã có (luồng enrich luôn có) để khỏi thêm một lần truy DB.
    Hồ sơ `draft` vẫn index được nếu gọi thẳng vào đây — nhưng đừng: `draft` là hồ sơ đang chạy
    dở, nội dung còn rỗng. `POST /api/kb/reindex` (6.4) đã lọc sẵn theo `INDEXABLE_PROFILE_STATUSES`.
    """
    if company is None:
        company = await db.get(Company, profile.company_id)
    if company is None:
        raise ValueError(f"Hồ sơ {profile.id} trỏ tới công ty không tồn tại ({profile.company_id})")
    document = build_profile_document(company, profile)
    return await index_documents(db, [document], client=client, commit=commit)


# --------------------------------------------------------------------------- nội bộ


def _to_chunks(title: str, body: str, meta: dict[str, Any]) -> list[Chunk]:
    """Cắt thân bài rồi gắn tiêu đề + metadata vào từng chunk.

    Thân bài rỗng (danh thiếp quét hỏng, chưa có trường nào) trả về danh sách rỗng — nguồn đó
    bị gỡ khỏi KB thay vì để lại một chunk chỉ có mỗi dòng tiêu đề, thứ khớp với mọi câu hỏi
    về "danh thiếp" mà không trả lời được câu nào.
    """
    pieces = chunk_text(body)
    if not pieces:
        return []
    return [
        Chunk(
            content=f"{title}\n{piece}",
            meta={**meta, "chunk_index": index, "chunk_count": len(pieces)},
        )
        for index, piece in enumerate(pieces)
    ]


def _split_long_line(line: str, max_chars: int, overlap: int) -> list[str]:
    """Cắt một dòng dài quá trần, lùi về khoảng trắng gần nhất khi có thể.

    Không tách theo từ: tiếng Trung/Nhật không có khoảng trắng nên cách đó trả về nguyên dòng
    và chunk vẫn quá dài — đúng trường hợp danh thiếp đa ngôn ngữ mà A4 đòi hỏi. Cắt theo ký tự
    rồi *cố* lùi về khoảng trắng là cách chạy được cho mọi ngôn ngữ trong phạm vi dự án.
    """
    pieces: list[str] = []
    start = 0
    while start < len(line):
        end = min(start + max_chars, len(line))
        if end < len(line):
            space = line.rfind(" ", start + max_chars // 2, end)
            if space > start:
                end = space
        piece = line[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(line):
            break
        start = max(end - overlap, start + 1)
    return pieces


def _lines_to_text(lines: Sequence[tuple[str, Any]]) -> str:
    return "\n".join(f"{label}: {_text(value)}" for label, value in lines if _text(value))


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _other_names(company: Company) -> list[str]:
    """Mọi cách gọi khác với tên đang hiện, bỏ trùng và giữ nguyên thứ tự (`I-36`).

    `_insert_company()` vốn đã nhét `display_name` vào `aliases`, nên nối thẳng hai thứ lại
    là in tên gốc hai lần trong cùng một dòng.
    """
    shown = company.vi_name.casefold()
    names = dict.fromkeys(
        name.strip()
        for name in (*(company.aliases or []), company.display_name)
        if name and name.strip() and name.strip().casefold() != shown
    )
    return list(names)


def _join(values: Sequence[str] | None) -> str:
    return ", ".join(part.strip() for part in values if part and part.strip()) if values else ""


def _language(code: str | None) -> str | None:
    """`"ja"` → `"Tiếng Nhật (ja)"`. Mã lạ thì giữ nguyên mã, không bịa tên.

    Giữ lại cả mã vì nó là thứ `services/normalize.py` dùng để chọn mã vùng số điện thoại — mất
    mã khỏi chunk thì lúc đối chiếu KB với DB không còn lần ra được vì sao một số lại chuẩn hoá
    theo vùng đó. Xem `LANGUAGE_LABELS` để biết vì sao dòng này quan trọng với F3.
    """
    text = _text(code)
    if not text:
        return None
    label = LANGUAGE_LABELS.get(text.casefold())
    return f"{label} ({text})" if label else text


def _with_vi(original: str, vi: str | None) -> str:
    """`"田中 太郎"` + `"Tanaka Taro"` → `"田中 太郎 (Tanaka Taro)"`; trùng nhau thì chỉ một bản.

    Dùng cho **dòng tiêu đề**, nơi mỗi ký tự đều phải trả giá: tiêu đề được lặp vào đầu mọi chunk
    nên viết dài là ăn vào ngân sách 512 token của `e5-small` ở mọi chunk cùng lúc (task 6.2).
    """
    other = _other(vi, original)
    return f"{original} ({other})" if other else original


def _other(value: str | None, already_shown: str | None) -> str | None:
    """Giữ `value` chỉ khi nó khác thứ đã in ở dòng trên (so sánh bỏ hoa thường/khoảng trắng)."""
    text = _text(value)
    if not text:
        return None
    if already_shown and text.casefold() == _text(already_shown).casefold():
        return None
    return text


def _date(value: datetime | date | None) -> str:
    return value.date().isoformat() if isinstance(value, datetime) else _text(value)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _source_urls(sources: Any) -> list[str]:
    """Rút danh sách URL phẳng từ `company_profiles.sources` (`{trường: [{url,…}]}`).

    Đọc phòng thủ: `sources` là JSONB do luồng enrich của T ghi, cấu trúc có thể đổi mà không
    ai báo. Sai kiểu thì trả danh sách rỗng chứ không làm hỏng cả lượt index.
    """
    if not isinstance(sources, dict):
        return []
    urls: list[str] = []
    for refs in sources.values():
        if not isinstance(refs, list):
            continue
        for ref in refs:
            url = ref.get("url") if isinstance(ref, dict) else ref
            if isinstance(url, str) and url.strip() and url not in urls:
                urls.append(url.strip())
            if len(urls) >= MAX_SOURCE_URLS:
                return urls
    return urls
