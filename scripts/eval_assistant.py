"""Chấm điểm trợ lý AI theo `docs/qa-testset.md` — phép đo tiêu chí D8 (≥7/10) và A6 (≥8/10).

Chủ sở hữu: Q | Task: 10.1

    python -m scripts.eval_assistant             # chấm 10 câu + 3 câu chặn + cặp nhiều lượt
    python -m scripts.eval_assistant --only 5    # chạy lại đúng một câu khi đang sửa

**Máy chấm làm được hai trong ba luật của bộ câu hỏi, không làm được luật còn lại.** Luật 1 (đủ
dữ kiện) và luật 3 (trích dẫn trỏ đúng nguồn) chấm tự động được. Luật 2 (**không bịa thêm**) thì
không — máy không biết "thành lập năm 2005" là đúng hay bịa. Vì vậy script **in nguyên văn mọi
câu trả lời**, và câu đạt luật 1+3 đánh dấu `ĐẠT*`: dấu sao nghĩa là *chờ người xác nhận luật 2*.

Ba câu ngoài phạm vi là **điều kiện chặn**: trượt một câu thì số đo không dùng để nghiệm thu, dù
10 câu kia đạt bao nhiêu. Chấm chặt: `citations` phải rỗng **và** câu trả lời không chứa chuỗi số
nào (một mã số thuế bịa luôn là chuỗi số).
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from dataclasses import dataclass, field

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "http://localhost:8000"
TIMEOUT = 180.0

#: Chuỗi từ 4 chữ số trở lên — bắt việc bịa mã số thuế / SĐT ở ba câu chặn. Ngưỡng 4 để câu
#: "không có thông tin" lỡ nhắc tới năm 2026 không bị chấm oan.
_LONG_NUMBER = re.compile(r"\d{4,}")


#: `đ`/`Đ` KHÔNG phải chữ `d` cộng dấu tổ hợp mà là ký tự riêng (U+0111 / U+0110), nên `NFD`
#: không bóc ra được — "Đại Việt" bỏ dấu kiểu ngây thơ vẫn ra "đai viet".
_D_STROKE = str.maketrans({"đ": "d", "Đ": "d"})


def strip_marks(text: str) -> str:
    """Bỏ dấu phụ + hạ chữ thường. Luật 1 của mục 3 cho phép tên riêng viết không dấu."""
    decomposed = unicodedata.normalize("NFD", text.casefold().translate(_D_STROKE))
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def digits(text: str) -> str:
    return re.sub(r"\D", "", text)


@dataclass(frozen=True)
class Fact:
    """Một dữ kiện bắt buộc, kèm các biến thể được chấp nhận.

    `phone=True` thì so theo chữ số và chấp nhận đuôi trùng: `0912345678` (nội địa) và
    `+84912345678` (E.164) là cùng một số, luật 1 nói rõ cả hai đều tính.
    """

    label: str
    variants: tuple[str, ...]
    phone: bool = False

    def present_in(self, answer: str) -> bool:
        if self.phone:
            found = digits(answer)
            return any(
                variant_digits in found or found.endswith(variant_digits)
                for variant in self.variants
                if (variant_digits := digits(variant)[-9:])
            )
        flat = strip_marks(answer)
        return any(strip_marks(variant) in flat for variant in self.variants)


@dataclass(frozen=True)
class Question:
    code: str
    text: str
    facts: tuple[Fact, ...]
    #: Nhãn nguồn trong `docs/qa-testset.md` mục 2 — P1..P3 (hồ sơ DN), C1..C4 (danh thiếp).
    sources: tuple[str, ...]
    probing: str


def f(label: str, *variants: str, phone: bool = False) -> Fact:
    return Fact(label=label, variants=variants, phone=phone)


QUESTIONS: tuple[Question, ...] = (
    Question(
        "1",
        "Công ty nào làm về logistics?",
        (f("Logistics Đại Việt", "Đại Việt", "Dai Viet"),),
        ("P1",),
        "Tìm theo ngành bằng ngôn ngữ tự nhiên",
    ),
    Question(
        "2",
        "Mã số thuế của Công ty CP Sữa Mộc Châu là bao nhiêu?",
        (f("MST 0100233468", "0100233468"),),
        ("P2",),
        "Tra một trường cụ thể của hồ sơ",
    ),
    Question(
        "3",
        "Hanwha Precision Vietnam cung cấp sản phẩm gì?",
        (
            f("Linh kiện cơ khí chính xác", "linh kiện cơ khí chính xác"),
            f("Máy gắn linh kiện SMT", "máy gắn linh kiện SMT", "SMT"),
        ),
        ("P3",),
        "Trả lời bằng danh sách, không bỏ sót mục nào",
    ),
    Question(
        "4",
        "Email của Trần Thị Bình là gì?",
        (f("email C2", "binh.tran@mocchaumilk.vn"),),
        ("C2",),
        "Tra danh thiếp theo tên người",
    ),
    Question(
        "5",
        "Số 0912 345 678 là của ai?",
        (f("Nguyễn Văn An", "Nguyễn Văn An"),),
        ("C1",),
        "Tìm theo định danh, số nhập khác định dạng lưu",
    ),
    Question(
        "6",
        "Ai là giám đốc kinh doanh của Logistics Đại Việt?",
        (f("Nguyễn Văn An", "Nguyễn Văn An"),),
        ("C1",),
        "Tìm theo chức vụ + công ty",
    ),
    Question(
        "7",
        "Kim Min-jun làm chức vụ gì, ở công ty nào?",
        (
            f(
                "chức vụ",
                "Sales Manager",
                "영업 과장",
                "Giám đốc kinh doanh",
                "Trưởng phòng kinh doanh",
                "quản lý kinh doanh",
            ),
            f("Hanwha Precision Vietnam", "Hanwha"),
        ),
        ("C3",),
        "Danh thiếp tiếng Hàn",
    ),
    Question(
        "8",
        "Có ai làm ở công ty Nhật không?",
        (
            f("田中 太郎", "田中", "Tanaka"),
            f("東京テック株式会社", "東京テック", "Tokyo Tech", "TokyoTech"),
        ),
        ("C4",),
        "Câu hỏi tiếng Việt, dữ liệu tiếng Nhật; thẻ chưa gắn công ty",
    ),
    Question(
        "9",
        "Tôi muốn liên hệ công ty sản xuất sữa thì gọi cho ai?",
        (
            f("Trần Thị Bình", "Trần Thị Bình"),
            f("SĐT hoặc email C2", "+84987654321", "0987654321", "binh.tran@mocchaumilk.vn"),
        ),
        ("C2",),
        "Nối hồ sơ DN với danh thiếp",
    ),
    Question(
        "10",
        "Mã số thuế 0301234567 là của công ty nào?",
        (f("Logistics Đại Việt", "Đại Việt", "Dai Viet"),),
        ("P1",),
        "Tìm ngược từ định danh ra công ty",
    ),
)

#: Mục 5 — câu ngoài phạm vi KB. Điều kiện chặn, không tính vào 10 điểm.
BLOCKERS: tuple[tuple[str, str], ...] = (
    ("X1", "Giá vàng hôm nay bao nhiêu?"),
    ("X2", "Hướng dẫn nấu phở bò"),
    ("X3", "Mã số thuế của Vinamilk là gì?"),
)

#: Mục 6 — cặp câu nhiều lượt, không tính điểm.
MULTI_TURN: tuple[tuple[str, str, Fact], ...] = (
    ("M1", "Ai là trưởng phòng marketing của Sữa Mộc Châu?", f("Trần Thị Bình", "Trần Thị Bình")),
    (
        "M2",
        "Số điện thoại của chị ấy là gì?",
        f("+84987654321", "+84987654321", "0987654321", phone=True),
    ),
)

#: Câu nhận là "không có thông tin" — khớp với `prompts/assistant.py`.
REFUSALS = (
    "không có thông tin",
    "không tìm thấy",
    "không đủ thông tin",
    "không có dữ liệu",
    "chưa có thông tin",
    "không được cung cấp",
)


# --------------------------------------------------------------------------- tra id nguồn


def resolve_sources(http: httpx.Client) -> dict[str, str]:
    """Nhãn P1..C4 → UUID thật trong DB đang chạy.

    Tra bằng chính API công khai chứ không nối thẳng vào Postgres: đo trên một đường đi khác
    đường người dùng đi thì số đo không nói được gì về sản phẩm.
    """
    mapping: dict[str, str] = {}

    cards = http.get("/api/cards", params={"size": 100}, timeout=TIMEOUT).json()["items"]
    by_email = {(c.get("email") or "").lower(): c["id"] for c in cards}
    for label, email in (
        ("C1", "an.nguyen@daiviet-logistics.vn"),
        ("C2", "binh.tran@mocchaumilk.vn"),
        ("C3", "minjun.kim@hanwha.co.kr"),
        ("C4", "tanaka@tokyotech.co.jp"),
    ):
        if email in by_email:
            mapping[label] = by_email[email]

    companies = http.get("/api/companies", params={"size": 100}, timeout=TIMEOUT).json()["items"]
    for label, needle in (
        ("P1", "logistics dai viet"),
        ("P2", "sua moc chau"),
        ("P3", "hanwha precision vietnam"),
    ):
        for company in companies:
            if needle in strip_marks(company["display_name"]):
                mapping[label] = company["id"]
                break

    return mapping


# --------------------------------------------------------------------------- chấm


@dataclass
class Scored:
    code: str
    question: str
    answer: str
    facts_ok: bool
    citation_ok: bool
    missing: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.facts_ok and self.citation_ok


def ask(http: httpx.Client, question: str, session_id: str | None = None) -> dict:
    payload: dict[str, object] = {"question": question}
    if session_id:
        payload["session_id"] = session_id
    response = http.post("/api/chat", json=payload, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def score_question(http: httpx.Client, q: Question, sources: dict[str, str]) -> Scored:
    body = ask(http, q.text)
    answer = body["answer"]
    missing = [fact.label for fact in q.facts if not fact.present_in(answer)]

    wanted = {sources[label] for label in q.sources if label in sources}
    got = {citation["source_id"] for citation in body["citations"]}
    unresolved = [label for label in q.sources if label not in sources]

    return Scored(
        code=q.code,
        question=q.text,
        answer=answer,
        facts_ok=not missing,
        citation_ok=bool(wanted & got),
        missing=missing,
        citations=[
            f"{c['source_type']}:{c.get('title') or c['source_id'][:8]}" for c in body["citations"]
        ],
        note=(
            f"nguồn {unresolved} không có trong DB — chạy `scripts/seed.py` trước"
            if unresolved
            else f"{body['context_chunks']} chunk ngữ cảnh"
        ),
    )


def score_blocker(http: httpx.Client, code: str, question: str) -> Scored:
    body = ask(http, question)
    answer = body["answer"]
    admits = any(phrase in answer.lower() for phrase in REFUSALS)
    numbers = _LONG_NUMBER.findall(answer)
    no_citation = not body["citations"]

    problems = []
    if not admits:
        problems.append("không nói rõ là không có thông tin")
    if numbers:
        problems.append(f"nêu con số cụ thể: {numbers}")
    if not no_citation:
        problems.append("citations không rỗng")

    return Scored(
        code=code,
        question=question,
        answer=answer,
        facts_ok=not problems,
        citation_ok=not problems,
        missing=problems,
        citations=[c["source_id"][:8] for c in body["citations"]],
        note=f"{body['context_chunks']} chunk ngữ cảnh (tầng truy hồi luôn trả về vài chunk)",
    )


def run_multi_turn(http: httpx.Client) -> list[Scored]:
    out: list[Scored] = []
    session_id: str | None = None
    for code, question, fact in MULTI_TURN:
        body = ask(http, question, session_id)
        session_id = body["session_id"]
        ok = fact.present_in(body["answer"])
        out.append(
            Scored(
                code=code,
                question=question,
                answer=body["answer"],
                facts_ok=ok,
                citation_ok=True,
                missing=[] if ok else [fact.label],
                note="cùng một session_id",
            )
        )
    return out


# --------------------------------------------------------------------------- báo cáo


def show(result: Scored) -> None:
    mark = "ĐẠT*" if result.ok else "TRƯỢT"
    print(f"\n[{result.code}] {mark}  {result.question}")
    print(f"      → {result.answer.strip()[:400]}")
    if result.citations:
        print(f"      trích dẫn: {', '.join(result.citations)}")
    if result.missing:
        print(f"      THIẾU: {', '.join(result.missing)}")
    if result.note:
        print(f"      ({result.note})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Chấm điểm trợ lý AI theo docs/qa-testset.md")
    parser.add_argument("--only", help="Chỉ chạy một mã câu: 1..10 | X1..X3 | M")
    args = parser.parse_args()

    with httpx.Client(base_url=BASE_URL) as http:
        try:
            http.get("/health", timeout=10.0).raise_for_status()
        except httpx.HTTPError as exc:
            print(f"Không gọi được {BASE_URL}/health — stack chưa chạy? ({exc})")
            return 2

        sources = resolve_sources(http)
        missing_sources = [
            label for label in ("P1", "P2", "P3", "C1", "C2", "C3", "C4") if label not in sources
        ]
        if missing_sources:
            print(
                f"⚠️ Thiếu nguồn trong DB: {missing_sources}. "
                f"Chạy `docker compose exec api python -m scripts.seed` trước khi đo.\n"
            )

        print("=========== 10 CÂU TÍNH ĐIỂM ===========")
        scored = [
            score_question(http, q, sources)
            for q in QUESTIONS
            if not args.only or q.code == args.only
        ]
        for result in scored:
            show(result)

        blockers: list[Scored] = []
        multi: list[Scored] = []
        if not args.only or args.only.startswith("X"):
            print("\n=========== 3 CÂU CHẶN (ngoài phạm vi KB) ===========")
            blockers = [
                score_blocker(http, code, text)
                for code, text in BLOCKERS
                if not args.only or code == args.only
            ]
            for result in blockers:
                show(result)

        if not args.only or args.only == "M":
            print("\n=========== HỘI THOẠI NHIỀU LƯỢT ===========")
            multi = run_multi_turn(http)
            for result in multi:
                show(result)

    passed = sum(1 for r in scored if r.ok)
    blocked_ok = sum(1 for r in blockers if r.ok)
    multi_ok = all(r.ok for r in multi) if multi else None

    print("\n=========== TỔNG KẾT ===========")
    print(f"  10 câu tính điểm : {passed}/{len(scored)}   (D8 cần ≥7, A6 cần ≥8)")
    if blockers:
        print(
            f"  3 câu chặn       : {blocked_ok}/{len(blockers)}  "
            f"{'— ĐẠT' if blocked_ok == len(blockers) else '— TRƯỢT, số đo trên KHÔNG dùng nghiệm thu được'}"
        )
    if multi_ok is not None:
        print(f"  Nhiều lượt       : {'đạt' if multi_ok else 'trượt'}")
    print("\n  Dấu * nghĩa là máy mới chấm được luật 1 (đủ dữ kiện) và luật 3 (trích dẫn).")
    print("  Luật 2 — KHÔNG bịa thêm — phải đọc từng câu trả lời ở trên rồi tự kết luận.")

    if blockers and blocked_ok < len(blockers):
        return 1
    return 0 if passed >= 8 else 1


if __name__ == "__main__":
    raise SystemExit(main())
