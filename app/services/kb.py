"""Knowledge Base cho F3: biến danh thiếp / hồ sơ DN thành văn bản, chunk, nhúng vector.

Chủ sở hữu: Q | Task: 6.2, 12.5

Hai hàm ở cuối file là **hợp đồng với T**: `ingest_card()` và `ingest_company_profile()`. Chữ ký
không đổi khi lên đa người dùng — chủ sở hữu **suy ra từ chính bản ghi nguồn** (`card.workspace_id`)
thay vì thêm tham số mà mọi chỗ gọi phải nhớ truyền đúng, nên **không có cách nào gọi sai người**.

Ba quyết định quyết định chất lượng trả lời của trợ lý:

1. **Mỗi chunk tự đứng được một mình** — dòng tiêu đề lặp vào đầu **mọi** chunk, nếu không thì
   một đoạn cắt ở giữa là đoạn văn vô danh và model trích dẫn sai công ty.
2. **URL nguồn không nằm trong văn bản đem nhúng** mà ở `metadata.sources` — nhúng link chỉ làm
   loãng ngữ nghĩa.
3. **Serialize bằng nhãn tiếng Việt có dấu** — model nhúng là đa ngôn ngữ và câu hỏi sẽ là tiếng
   Việt, nên `Mã số thuế:` bắt tốt hơn hẳn `tax_code:`.
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

#: Trần ký tự một chunk. `multilingual-e5-small` cắt đầu vào ở 512 token và phần vượt bị **bỏ im
#: lặng**. 700 ký tự an toàn cho cả tiếng Việt lẫn CJK (CJK tốn nhiều token hơn trên mỗi ký tự).
CHUNK_MAX_CHARS = 700

#: Phần lặp giữa hai chunk liên tiếp khi phải cắt **giữa một đoạn văn dài**. Không có phần lặp
#: thì một câu bị cắt đôi không khớp với truy vấn nào. Dòng trường dữ liệu không bao giờ bị lặp.
CHUNK_OVERLAP_CHARS = 80

#: Số URL tối đa giữ trong metadata của một chunk.
MAX_SOURCE_URLS = 10

#: Tên tiếng Việt của mã ISO 639-1. Ghi **cả tên lẫn mã** vào chunk, không chỉ mã.
#:
#: Đo thật: chunk chỉ ghi `Ngôn ngữ: ja` thì câu hỏi "có ai làm ở công ty Nhật không?" truy hồi
#: đúng thẻ nhưng trợ lý vẫn trả "không có thông tin" — không chỗ nào trong ngữ cảnh nói thẻ này
#: là **tiếng Nhật**. Sửa ở dữ liệu chứ không nới quy tắc 1 của `prompts/assistant.py`.
LANGUAGE_LABELS: dict[str, str] = {
    "vi": "Tiếng Việt",
    "en": "Tiếng Anh",
    "ko": "Tiếng Hàn",
    "ja": "Tiếng Nhật",
    "zh": "Tiếng Trung",
    "zh-tw": "Tiếng Trung (phồn thể)",
    # Bảng này chỉ là **nhãn hiển thị**: mã lạ vẫn vào KB nguyên dạng, chỉ là chunk ghi `sw` thay
    # vì "Tiếng Swahili".
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
    `workspace_id` đi kèm ngay trong đơn vị ghi chứ không truyền song song cho cả lô.
    """

    workspace_id: uuid.UUID
    source_type: KBSourceType
    source_id: uuid.UUID
    chunks: list[Chunk]


# --------------------------------------------------------------------------- serialize


def serialize_card(card: BusinessCard, *, company_name: str | None = None) -> tuple[str, str]:
    """Danh thiếp → `(tiêu đề, thân bài)`. Trường rỗng bị bỏ hẳn dòng.

    `company_name` là tên **đã chuẩn hoá**, khác `company_name_raw` in trên thẻ. Giữ cả hai khi
    chúng khác nhau: người hỏi có thể gõ theo kiểu nào cũng được.

    **Bản Việt hoá đi vào chunk, ngay dưới bản gốc** — không có nó thì câu hỏi "Tanaka Taro làm ở
    đâu?" không bao giờ khớp nổi một chunk chỉ chứa `田中 太郎`. Tên Việt hoá cũng vào dòng tiêu đề.
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

    `description` để **cuối cùng**: nó là trường dài duy nhất, nên khi phải cắt nhiều chunk thì
    các trường tra cứu (MST, ngành nghề, địa chỉ) nằm gọn trong chunk đầu.
    """
    # Tiêu đề đi theo tên **đang hiện trên giao diện**, bản gốc xuống dòng *Tên gọi khác*: người
    # hỏi gõ đúng cái tên họ nhìn thấy trong danh sách.
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

    Mỗi dòng là một trường dữ liệu hoàn chỉnh (`Mã số thuế: …`), cắt giữa dòng là làm hỏng đúng
    thứ mà câu hỏi nhắm tới. Chỉ dòng dài quá trần mới cắt bên trong, và chỉ khi đó mới có
    `overlap`.
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

    Bấm "Tạo lại hồ sơ" sinh hàng `company_profiles` mới với id khác. Neo theo `company_id` thì
    lần ghi sau tự xoá đè bản trước; neo theo id hồ sơ thì KB giữ lại cả hồ sơ đã bị thay.
    """
    # Hai bảng phải cùng một không gian. Lệch nhau nghĩa là dữ liệu đã hỏng ở đâu đó phía trên;
    # ghi vào KB lúc đó là biến một lỗi dữ liệu thành đường rò.
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

    Gọi embedder **một lần cho cả lô** rồi mới đụng tới DB: lời gọi mạng không được nằm trong
    transaction, và một request 32 đoạn rẻ hơn hẳn 32 request một đoạn.

    `commit=False` khi chỗ gọi đang tự quản transaction.
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
    """Đưa **một** hồ sơ doanh nghiệp vào KB — hàm T gọi ở cuối luồng enrich.

    Truyền sẵn `company` nếu chỗ gọi đã có để khỏi thêm một lần truy DB. Đừng gọi thẳng với hồ sơ
    `draft`: nội dung còn rỗng.
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

    Thân bài rỗng trả về danh sách rỗng — nguồn đó bị gỡ khỏi KB thay vì để lại một chunk chỉ có
    dòng tiêu đề, thứ khớp với mọi câu hỏi mà không trả lời được câu nào.
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

    Không tách theo từ: tiếng Trung/Nhật không có khoảng trắng nên cách đó trả về nguyên dòng và
    chunk vẫn quá dài.
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
    """Mọi cách gọi khác với tên đang hiện, bỏ trùng và giữ nguyên thứ tự.

    `_insert_company()` vốn đã nhét `display_name` vào `aliases`, nên nối thẳng là in hai lần.
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

    Giữ cả mã vì `services/normalize.py` dùng nó để chọn mã vùng số điện thoại.
    """
    text = _text(code)
    if not text:
        return None
    label = LANGUAGE_LABELS.get(text.casefold())
    return f"{label} ({text})" if label else text


def _with_vi(original: str, vi: str | None) -> str:
    """`"田中 太郎"` + `"Tanaka Taro"` → `"田中 太郎 (Tanaka Taro)"`; trùng nhau thì chỉ một bản.

    Dùng cho **dòng tiêu đề**, nơi mỗi ký tự đều phải trả giá: tiêu đề lặp vào đầu mọi chunk nên
    viết dài là ăn vào ngân sách 512 token ở mọi chunk cùng lúc.
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

    Đọc phòng thủ: sai kiểu thì trả danh sách rỗng chứ không làm hỏng cả lượt index.
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
