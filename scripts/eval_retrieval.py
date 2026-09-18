"""Đo chất lượng truy hồi RAG trên bộ 10 truy vấn mẫu (task 7.4).

Chủ sở hữu: Q | Task: 7.4 | xem Task.md

Chạy trong Docker, nơi `db` và `embedder` phân giải được:

    docker compose run --rm -e PYTHONPATH=/app api python scripts/eval_retrieval.py

(`PYTHONPATH` là bắt buộc: `WORKDIR` của image là `/app` nhưng khi chạy một file trong
`scripts/` thì Python đặt `sys.path[0]` thành `/app/scripts`, và gói `app` không nằm ở đó.
Trên Git Bash phải thêm `MSYS_NO_PATHCONV=1`, nếu không `/app` bị dịch thành đường dẫn Windows.)

Ba thứ được đo, vì cả ba đều là con số mà `services/retriever.py` đang phải giả định:

1. **Recall@k của từng nhánh và của bản trộn** — hybrid có thật sự hơn vector đơn không, hay
   chỉ là phức tạp thêm. Đo riêng `vector`, `text`, `hybrid` trên cùng một bộ truy vấn.
2. **Phân bố điểm tương đồng**: câu hỏi *có* câu trả lời trong KB cho điểm bao nhiêu, câu hỏi
   *ngoài phạm vi* cho điểm bao nhiêu. Khoảng trống giữa hai phân bố chính là chỗ đặt
   `MIN_SIMILARITY`. Đặt ngưỡng mà không có hai phân bố này thì chỉ là bịa một con số.
3. **Kích thước chunk** — bao nhiêu nguồn thật sự bị cắt làm nhiều mảnh ở các mức trần khác
   nhau. Task 7.4 đòi "tinh chỉnh chunk size", nhưng chỉnh mà không biết nó có chạm tới dữ liệu
   thật hay không thì là chỉnh mù.

**Không để lại gì trong DB**: toàn bộ chạy trong một transaction rồi rollback, đúng cách
`tests/conftest.py` làm. Nhờ vậy chạy thẳng trên database đang dùng cũng an toàn, và mỗi lượt
chạy đo trên đúng một bộ dữ liệu cố định thay vì "những gì tình cờ có trong DB".
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import settings
from app.models.card import BusinessCard, CardStatus
from app.models.company import Company, CompanyProfile
from app.services import embeddings, kb, retriever
from app.services.normalize_company import normalize_company_name

NOW = datetime(2026, 9, 17, 9, 0, tzinfo=UTC)

#: Các mức `top_k` được báo cáo. 1 = "trả lời đúng ngay câu đầu", 5 = mặc định của `retriever`.
TOP_KS = (1, 3, 5)

#: Các mức trần chunk đem so, quanh `kb.CHUNK_MAX_CHARS` hiện tại.
CHUNK_SIZES = (400, 700, 1000)

#: Định danh của bộ dữ liệu cố định, khai ở đây để **`scripts/seed.py` (9.2) dùng chung**.
#:
#: Script này rollback nên không cần biết đã seed lần nào chưa; `seed.py` thì commit, nên phải
#: nhận ra được dữ liệu mẫu của lần chạy trước để không nạp chồng thành hai bản. Chép danh sách
#: sang file kia là mở đường cho hai bên lệch nhau trong im lặng — `seed.py` có bước đối chiếu
#: lại với dữ liệu thật vừa tạo và báo lỗi nếu lệch.
SEED_COMPANY_NAMES: tuple[str, ...] = (
    "Công ty TNHH Logistics Đại Việt",
    "Công ty CP Sữa Mộc Châu",
    "Hanwha Precision Vietnam",
)
SEED_CARD_EMAILS: tuple[str, ...] = (
    "an.nguyen@daiviet-logistics.vn",
    "binh.tran@mocchaumilk.vn",
    "minjun.kim@hanwha.co.kr",
    "tanaka@tokyotech.co.jp",
)


@dataclass(frozen=True, slots=True)
class Query:
    """Một truy vấn mẫu kèm **đáp án đúng**: nhãn của nguồn đáng lẽ phải ra."""

    text: str
    expect: str | None
    #: Nhánh nào *đáng lẽ* bắt được — để đọc báo cáo biết nhánh nào đang gánh việc gì.
    expect_leg: str
    note: str = ""


# Mười truy vấn của task 7.4 + ba câu ngoài phạm vi KB. Ba câu cuối không phải để tính recall mà
# để đo **cận trên của điểm không liên quan** — nửa còn thiếu khi chọn ngưỡng.
QUERIES: tuple[Query, ...] = (
    Query("công ty nào làm về logistics?", "hồ sơ Đại Việt", "vector", "tiêu chí D7"),
    Query("công ty nào sản xuất sữa chua?", "hồ sơ Mộc Châu", "vector"),
    Query("mã số thuế của Logistics Đại Việt là bao nhiêu?", "hồ sơ Đại Việt", "cả hai"),
    Query("ai dùng email binh.tran@mocchaumilk.vn?", "thẻ Bình", "text"),
    Query("số 0912 345 678 là của ai?", "thẻ An", "text", "cần chuẩn hoá E.164"),
    Query("ai là giám đốc kinh doanh?", "thẻ An", "vector"),
    Query("cong ty nao san xuat sua", "hồ sơ Mộc Châu", "vector", "gõ không dấu"),
    Query("Hanwha có làm cơ khí chính xác không?", "hồ sơ Hanwha", "cả hai"),
    Query("có ai làm ở công ty Nhật không?", "thẻ Tanaka", "vector", "câu hỏi Việt, dữ liệu Nhật"),
    Query("mã số thuế 0100233468 là của ai?", "hồ sơ Mộc Châu", "text"),
    Query("giá vàng hôm nay bao nhiêu?", None, "—", "ngoài phạm vi KB"),
    Query("hướng dẫn nấu phở bò", None, "—", "ngoài phạm vi KB"),
    Query("thời tiết Hà Nội ngày mai thế nào?", None, "—", "ngoài phạm vi KB"),
)


@dataclass
class Corpus:
    """Dữ liệu mẫu đã ghi vào DB, kèm ánh xạ `source_id → nhãn` để chấm điểm."""

    labels: dict[uuid.UUID, str] = field(default_factory=dict)
    documents: list[kb.Document] = field(default_factory=list)


async def seed(db: AsyncSession) -> Corpus:
    """Dựng 4 danh thiếp + 3 hồ sơ doanh nghiệp, đủ 3 ngôn ngữ (Việt, Hàn, Nhật)."""
    corpus = Corpus()

    dai_viet = _company("Công ty TNHH Logistics Đại Việt", ["Đại Việt Logistics"])
    moc_chau = _company("Công ty CP Sữa Mộc Châu", ["Mocchau Milk"])
    hanwha = _company("Hanwha Precision Vietnam", ["한화정밀기계"])
    db.add_all([dai_viet, moc_chau, hanwha])
    await db.flush()

    profiles = [
        (
            dai_viet,
            "hồ sơ Đại Việt",
            _profile(
                dai_viet,
                tax_code="0301234567",
                industry=["Logistics", "Vận tải và kho bãi"],
                products=["Vận tải đường bộ Bắc - Nam", "Kho ngoại quan", "Giao nhận hàng hoá"],
                description=(
                    "Đại Việt Logistics thành lập năm 2005, chuyên vận tải đường bộ và dịch vụ "
                    "kho bãi cho khách hàng công nghiệp tại miền Bắc."
                ),
            ),
        ),
        (
            moc_chau,
            "hồ sơ Mộc Châu",
            _profile(
                moc_chau,
                tax_code="0100233468",
                industry=["Chế biến thực phẩm", "Sản xuất sữa"],
                products=["Sữa tươi thanh trùng", "Sữa chua ăn", "Bơ và phô mai"],
                description=(
                    "Doanh nghiệp chế biến sữa từ đàn bò nuôi tại cao nguyên Mộc Châu, sản phẩm "
                    "chính là sữa tươi và sữa chua."
                ),
            ),
        ),
        (
            hanwha,
            "hồ sơ Hanwha",
            _profile(
                hanwha,
                tax_code="0312345678",
                industry=["Cơ khí chính xác", "Sản xuất linh kiện"],
                products=["Linh kiện cơ khí chính xác", "Máy gắn linh kiện SMT"],
                description=(
                    "Chi nhánh Việt Nam của tập đoàn Hanwha, gia công cơ khí chính xác cho ngành "
                    "điện tử."
                ),
            ),
        ),
    ]
    cards = [
        (
            "thẻ An",
            _card(
                dai_viet,
                full_name="Nguyễn Văn An",
                job_title="Giám đốc kinh doanh",
                email="an.nguyen@daiviet-logistics.vn",
                phone="+84912345678",
                language="vi",
            ),
        ),
        (
            "thẻ Bình",
            _card(
                moc_chau,
                full_name="Trần Thị Bình",
                job_title="Trưởng phòng Marketing",
                email="binh.tran@mocchaumilk.vn",
                phone="+84987654321",
                language="vi",
            ),
        ),
        (
            "thẻ Kim",
            _card(
                hanwha,
                full_name="Kim Min-jun",
                job_title="Sales Manager / 영업 과장",
                email="minjun.kim@hanwha.co.kr",
                phone="+82212345678",
                language="ko",
            ),
        ),
        (
            "thẻ Tanaka",
            _card(
                None,
                full_name="田中 太郎",
                job_title="営業部長",
                company_raw="東京テック株式会社",
                email="tanaka@tokyotech.co.jp",
                phone="+81312345678",
                language="ja",
            ),
        ),
    ]

    db.add_all([profile for _, _, profile in profiles] + [card for _, card in cards])
    await db.flush()

    for company, label, profile in profiles:
        corpus.labels[company.id] = label
        corpus.documents.append(kb.build_profile_document(company, profile))
    for label, card in cards:
        corpus.labels[card.id] = label
        name = card.company_name_raw
        if card.company_id is not None:
            name = next(c.display_name for c, _, _ in profiles if c.id == card.company_id)
        corpus.documents.append(kb.build_card_document(card, company_name=name))

    await kb.index_documents(db, corpus.documents, commit=False)
    return corpus


async def evaluate(db: AsyncSession, corpus: Corpus) -> None:
    """Chạy cả ba cấu hình trên cùng bộ truy vấn rồi in báo cáo."""
    legs = {"vector": False, "hybrid": True}
    # `top_k` lớn nhất là đủ: recall@1 và @3 cắt từ chính danh sách này ra.
    largest = max(TOP_KS)

    results: dict[str, list[tuple[Query, list[retriever.Hit]]]] = {}
    for leg, hybrid in legs.items():
        rows = []
        for query in QUERIES:
            # `min_similarity=-1.0`: **cố ý tắt ngưỡng ở đây**. Đây là lượt chạy dùng để *chọn*
            # ngưỡng, áp ngưỡng hiện tại vào thì số đo chỉ xác nhận lại chính nó.
            hits = await retriever.search(
                db, query.text, top_k=largest, min_similarity=-1.0, hybrid=hybrid
            )
            rows.append((query, hits))
        results[leg] = rows

    _print_per_query(results["hybrid"], corpus)
    _print_recall(results, corpus)
    _print_threshold(results["hybrid"], corpus)
    await _print_text_leg(db)
    _print_chunks(corpus)


# --------------------------------------------------------------------------- báo cáo


def _print_per_query(rows: Sequence[tuple[Query, list[retriever.Hit]]], corpus: Corpus) -> None:
    print("\n=== Từng truy vấn (hybrid, chưa áp ngưỡng) ===")
    print(f"{'truy vấn':<46} {'mong đợi':<16} {'hạng':>5} {'sim':>7} {'khớp bởi':<8}")
    for query, hits in rows:
        rank = _rank_of(hits, corpus, query.expect)
        top = hits[0] if hits else None
        similarity = f"{top.similarity:.3f}" if top and top.similarity is not None else "—"
        print(
            f"{_cut(query.text, 45):<46} {(query.expect or '(ngoài KB)'):<16} "
            f"{(str(rank) if rank else '—'):>5} {similarity:>7} "
            f"{(top.matched_by if top else '—'):<8}"
        )


def _print_recall(
    results: dict[str, list[tuple[Query, list[retriever.Hit]]]], corpus: Corpus
) -> None:
    print("\n=== Recall@k (chỉ tính 10 truy vấn có đáp án) ===")
    header = "  ".join(f"@{k}".rjust(6) for k in TOP_KS)
    print(f"{'nhánh':<10} {header}")
    for leg, rows in results.items():
        answerable = [(query, hits) for query, hits in rows if query.expect]
        cells = []
        for k in TOP_KS:
            hit_count = sum(
                1
                for query, hits in answerable
                if (rank := _rank_of(hits, corpus, query.expect)) and rank <= k
            )
            cells.append(f"{hit_count}/{len(answerable)}".rjust(6))
        print(f"{leg:<10} {'  '.join(cells)}")


def _print_threshold(rows: Sequence[tuple[Query, list[retriever.Hit]]], corpus: Corpus) -> None:
    """Hai phân bố điểm — chỗ trống giữa chúng là chỗ đặt ngưỡng."""
    trung: list[float] = []
    ngoai: list[float] = []
    for query, hits in rows:
        if query.expect is None:
            ngoai += [hit.similarity for hit in hits[:1] if hit.similarity is not None]
            continue
        rank = _rank_of(hits, corpus, query.expect)
        if rank:
            similarity = hits[rank - 1].similarity
            if similarity is not None:
                trung.append(similarity)

    print("\n=== Phân bố điểm tương đồng ===")
    if trung:
        print(f"chunk ĐÚNG    : thấp nhất {min(trung):.3f} · cao nhất {max(trung):.3f}")
    if ngoai:
        print(f"câu NGOÀI KB  : cao nhất  {max(ngoai):.3f} (điểm của chunk đứng đầu)")
    if trung and ngoai:
        gap = min(trung) - max(ngoai)
        print(f"khoảng trống  : {gap:+.3f}")
        if gap > 0:
            middle = (min(trung) + max(ngoai)) / 2
            print(f"  -> tách được: đặt MIN_SIMILARITY ~ {middle:.2f}")
        else:
            print("  (!)  hai phân bố CHỒNG NHAU — không ngưỡng nào vừa giữ được câu")
            print("      đúng vừa chặn được câu lạc đề. Ngưỡng chỉ còn là *sàn an toàn*:")
            print(f"      đặt dưới {min(trung):.3f} (điểm đúng thấp nhất) và để D8 lo việc")
            print("      nói 'không có thông tin' bằng prompt, đừng nâng ngưỡng để bù.")
        print(f"ngưỡng đang dùng: {retriever.MIN_SIMILARITY:.2f}")


async def _print_text_leg(db: AsyncSession) -> None:
    """Nhánh full-text rút ra từ khoá gì, và có khớp được không."""
    print("\n=== Nhánh full-text: từ khoá rút được ===")
    for query in QUERIES:
        terms = retriever.query_terms(query.text)
        hits = await retriever.text_search(db, query.text, top_k=5)
        print(f"{_cut(query.text, 45):<46} {str(terms):<52} → {len(hits)} chunk")


def _print_chunks(corpus: Corpus) -> None:
    print("\n=== Kích thước chunk ===")
    bodies = [chunk.content for document in corpus.documents for chunk in document.chunks]
    print(
        f"nguồn: {len(corpus.documents)} · chunk hiện tại: {len(bodies)} "
        f"(trần {kb.CHUNK_MAX_CHARS}) · dài nhất {max(len(b) for b in bodies)} ký tự"
    )
    for size in CHUNK_SIZES:
        pieces = sum(len(kb.chunk_text(body, max_chars=size)) for body in bodies)
        print(f"  trần {size:>4} ký tự → {pieces} mảnh")


# --------------------------------------------------------------------------- tiện ích


def _rank_of(hits: Sequence[retriever.Hit], corpus: Corpus, expect: str | None) -> int | None:
    """Thứ hạng (1-based) của chunk thuộc nguồn đúng, `None` nếu không có trong danh sách."""
    if expect is None:
        return None
    for index, hit in enumerate(hits, start=1):
        if corpus.labels.get(hit.source_id) == expect:
            return index
    return None


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _company(display_name: str, aliases: list[str]) -> Company:
    # `name_normalized` dùng **đúng hàm chuẩn hoá của T**, không phải một chuỗi hex ngẫu nhiên
    # như bản đầu: `scripts/seed.py` (9.2) commit bộ dữ liệu này vào DB thật, mà khoá ngẫu nhiên
    # thì lần sau người dùng xác nhận một danh thiếp của đúng công ty đó, `upsert_company()`
    # không khớp được và tạo ra bản ghi công ty thứ hai. Khoá này không nằm trong nội dung chunk
    # nên số đo truy hồi của 7.4 không đổi.
    return Company(
        id=uuid.uuid4(),
        display_name=display_name,
        name_normalized=normalize_company_name(display_name),
        aliases=aliases,
    )


def _profile(company: Company, **overrides) -> CompanyProfile:
    values = {
        "id": uuid.uuid4(),
        "company_id": company.id,
        "legal_name": company.display_name,
        "founded_year": 2005,
        "size_label": "Vừa",
        "employee_range": "200-500",
        "address": "Hà Nội, Việt Nam",
        "website": "https://example.vn",
        "status": "generated",
        "generated_at": NOW.replace(tzinfo=None),
        "sources": {"tax_code": [{"url": "https://masothue.com/", "title": "masothue.com"}]},
    }
    values.update(overrides)
    return CompanyProfile(**values)


def _card(
    company: Company | None,
    *,
    full_name: str,
    job_title: str,
    email: str,
    phone: str,
    language: str,
    company_raw: str | None = None,
) -> BusinessCard:
    return BusinessCard(
        id=uuid.uuid4(),
        image_path=f"ev/{uuid.uuid4().hex}.jpg",
        image_hash=uuid.uuid4().hex,
        uploaded_at=NOW,
        status=CardStatus.CONFIRMED.value,
        company_id=company.id if company else None,
        full_name=full_name,
        job_title=job_title,
        company_name_raw=company_raw or (company.display_name if company else None),
        email=email,
        phone=phone,
        address="Hà Nội, Việt Nam",
        language_detected=language,
    )


async def run() -> int:
    info = await embeddings.health()
    print(f"embedder: {info.get('model')} · {info.get('dim')} chiều")

    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            db = AsyncSession(
                bind=connection,
                join_transaction_mode="create_savepoint",
                expire_on_commit=False,
            )
            try:
                corpus = await seed(db)
                await evaluate(db, corpus)
            finally:
                await db.close()
                # Rollback chứ không commit: script này không được để lại dữ liệu mẫu nào
                # trong DB đang dùng thật.
                await transaction.rollback()
    finally:
        await engine.dispose()
    return 0


def main() -> int:
    argparse.ArgumentParser(description="Đo chất lượng truy hồi RAG (task 7.4)").parse_args()
    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
