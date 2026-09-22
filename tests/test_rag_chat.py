"""Test trợ lý hỏi–đáp: prompt (8.1), API chat (8.2), hội thoại nhiều lượt (8.3), lọc (8.5).

Chủ sở hữu: Q | Task: 8.1–8.5 | xem Task.md

Phần cần DB dùng `embedder` giả lập của `conftest.py`: vector suy từ hàm băm của chính đoạn văn,
nên hỏi bằng **đúng nội dung một chunk** cho tương đồng 1.0 còn mọi câu khác cho ~0. Hai đầu của
thang điểm — đủ để dựng chắc chắn hai tình huống mà D8 phải phân biệt: "tìm được ngữ cảnh" và
"không tìm được gì".

Đổi lại, các test ở đây **không nói gì về chất lượng câu trả lời thật**: model cũng là giả lập,
nó trả về đúng chuỗi test đưa cho. Chất lượng trả lời đo bằng model thật trên
`docs/qa-testset.md` (task 8.7 của T), kết quả ghi vào chính file đó.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models.card import BusinessCard, CardStatus
from app.models.chat import ChatMessage, ChatRole
from app.models.company import Company, CompanyProfile
from app.models.kb import KBSourceType
from app.prompts import assistant as prompt
from app.repositories import chat as chat_repo
from app.repositories import kb as kb_repo
from app.routers import kb as kb_router
from app.services import assistant, retriever
from app.services.assistant import Turn
from app.services.retriever import Hit
from tests.conftest import api_client

NOW = datetime(2026, 9, 18, 9, 0, tzinfo=UTC)


def make_hit(name: str, *, source_type: str = KBSourceType.CARD.value, **overrides) -> Hit:
    """Một `Hit` tối thiểu; `name` quyết định cả `chunk_id` lẫn `source_id` nên tất định."""
    values = {
        "chunk_id": uuid.uuid5(uuid.NAMESPACE_OID, name),
        "source_type": source_type,
        "source_id": uuid.uuid5(uuid.NAMESPACE_DNS, name),
        "content": f"Danh thiếp — {name}\nHọ tên: {name}",
        "meta": {"title": f"Danh thiếp — {name}"},
        "score": 0.5,
        "similarity": 0.8,
    }
    values.update(overrides)
    return Hit(**values)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- 8.1 prompt


def test_ngu_canh_danh_so_tu_1():
    """Đánh số từ 0 thì model tự sửa về 1 và mọi dấu `[n]` lệch đúng một khối — sai âm thầm."""
    assert prompt.build_context(["A", "B"]) == "[1] A\n\n[2] B"


def test_prompt_dat_cau_hoi_sau_cung():
    """Câu hỏi nằm ngay trước chỗ model sinh chữ, không bị vùi dưới mấy nghìn ký tự ngữ cảnh."""
    text = prompt.build_prompt("Ai làm logistics?", "[1] Danh thiếp — A")

    assert text.rstrip().endswith("CÂU HỎI: Ai làm logistics?")
    assert text.index("NGỮ CẢNH:") < text.index("CÂU HỎI:")


def test_prompt_khong_co_lich_su_thi_khong_co_khoi_lich_su():
    assert "CÁC LƯỢT TRƯỚC" not in prompt.build_prompt("Hỏi gì đó", "[1] X")


def test_prompt_noi_ro_lich_su_khong_phai_nguon_de_trich_dan():
    """Thiếu câu này thì lượt 2 trích dẫn lại câu trả lời của chính mình ở lượt 1."""
    text = prompt.build_prompt("Số của chị ấy?", "[1] X", history="Người dùng: ai là A?")

    assert "CÁC LƯỢT TRƯỚC" in text
    assert "KHÔNG phải nguồn dữ kiện" in text


def test_system_prompt_cam_dung_kien_thuc_san_co():
    """Quy tắc 1 và 3 là thứ duy nhất chặn được câu X3 của `docs/qa-testset.md` (Vinamilk)."""
    assert "KHÔNG được dùng" in prompt.SYSTEM_PROMPT
    assert prompt.NO_ANSWER_TEXT in prompt.SYSTEM_PROMPT


# --------------------------------------------------------------------------- 8.2 trích dẫn


def test_trich_dan_theo_dung_dau_model_dat():
    hits = [make_hit("A"), make_hit("B"), make_hit("C")]

    text, citations = assistant.extract_citations("Đáp án là B [2].", hits)

    assert text == "Đáp án là B [1]."
    assert [citation.source_id for citation in citations] == [hits[1].source_id]


def test_trich_dan_giu_thu_tu_xuat_hien_va_danh_so_lai():
    """Số trong câu chữ phải khớp thứ tự thẻ bên dưới, không phải số hiệu khối ngữ cảnh."""
    hits = [make_hit("A"), make_hit("B"), make_hit("C")]

    text, citations = assistant.extract_citations("X [3] và Y [1].", hits)

    assert text == "X [1] và Y [2]."
    assert [citation.source_id for citation in citations] == [hits[2].source_id, hits[0].source_id]


def test_trich_dan_gop_nhieu_chunk_cua_cung_mot_nguon():
    """Một hồ sơ dài bị cắt thành 2 chunk vẫn chỉ ra **một** thẻ trích dẫn.

    Không gộp thì UI hiện hai thẻ y hệt nhau trỏ cùng một trang, và số trong câu chữ trỏ tới
    thẻ thứ hai — thứ người đọc không phân biệt được với một nguồn thật khác.
    """
    source_id = uuid.uuid4()
    hits = [
        make_hit("phần 1", source_id=source_id),
        make_hit("phần 2", source_id=source_id),
    ]

    text, citations = assistant.extract_citations("A [1] B [2].", hits)

    assert len(citations) == 1
    assert text == "A [1] B [1]."


def test_trich_dan_bo_han_dau_tro_ra_ngoai_pham_vi():
    """Model tự chế `[9]` khi chỉ có 2 khối: bỏ hẳn khỏi câu chữ, không để lại dấu bấm hụt."""
    hits = [make_hit("A"), make_hit("B")]

    text, citations = assistant.extract_citations("Câu trả lời [9].", hits)

    assert text == "Câu trả lời."
    assert citations == []


def test_trich_dan_doc_duoc_ca_hai_cach_viet_nhieu_nguon():
    hits = [make_hit("A"), make_hit("B"), make_hit("C")]

    lien_nhau, a = assistant.extract_citations("X [1][3].", hits)
    dau_phay, b = assistant.extract_citations("X [1, 3].", hits)

    assert lien_nhau == dau_phay == "X [1][2]."
    assert len(a) == len(b) == 2


def test_khong_co_dau_nao_thi_khong_tu_bia_trich_dan():
    """Trích dẫn tự gán còn tệ hơn không có: nó trông y hệt trích dẫn thật."""
    text, citations = assistant.extract_citations("Tôi nghĩ là công ty ABC.", [make_hit("A")])

    assert citations == []
    assert text == "Tôi nghĩ là công ty ABC."


def test_link_trich_dan_ho_so_tro_ve_trang_cong_ty():
    """`source_id` của chunk hồ sơ là id **công ty**, không phải id hồ sơ — nhầm là 404."""
    company_id = uuid.uuid4()

    assert (
        assistant.source_url(KBSourceType.COMPANY_PROFILE.value, company_id)
        == f"/companies/{company_id}"
    )
    assert assistant.source_url(KBSourceType.CARD.value, company_id) == f"/cards/{company_id}"


def test_diem_de_none_khi_chunk_chi_khop_nhanh_full_text():
    """Không có điểm cosine thì trả `null`, không bịa một con số cho đủ trường."""
    hits = [make_hit("A", similarity=None, text_rank=0.3, matched_by="text")]

    _, citations = assistant.extract_citations("X [1].", hits)

    assert citations[0].score is None


# --------------------------------------------------------------------------- 8.3 nhiều lượt


def test_cau_truy_van_ghep_luot_hoi_truoc():
    """Cặp câu ở `docs/qa-testset.md` mục 6: lượt 2 không tự đứng được."""
    history = [
        Turn(role=ChatRole.USER.value, content="Ai là trưởng phòng marketing của Sữa Mộc Châu?"),
        Turn(role=ChatRole.ASSISTANT.value, content="Trần Thị Bình [1]."),
    ]

    query = assistant.retrieval_query("Số điện thoại của chị ấy là gì?", history)

    assert "Sữa Mộc Châu" in query
    assert query.endswith("Số điện thoại của chị ấy là gì?")


def test_cau_truy_van_khong_lay_cau_tra_loi_cu():
    """Câu trả lời dài hơn câu hỏi nhiều và đủ sức lấn át nó trong vector."""
    history = [Turn(role=ChatRole.ASSISTANT.value, content="Một câu trả lời rất dài về ABC")]

    assert assistant.retrieval_query("Hỏi tiếp", history) == "Hỏi tiếp"


def test_cau_truy_van_khong_co_lich_su_thi_giu_nguyen():
    assert assistant.retrieval_query("Ai làm logistics?") == "Ai làm logistics?"


def test_lich_su_chi_lay_nhung_luot_gan_nhat():
    history = [Turn(role=ChatRole.USER.value, content=f"câu {index}") for index in range(10)]

    lines = assistant.history_text(history).splitlines()

    assert len(lines) == assistant.HISTORY_TURNS
    assert lines[-1] == "Người dùng: câu 9"


def test_lich_su_cat_ngan_luot_qua_dai():
    history = [Turn(role=ChatRole.ASSISTANT.value, content="x" * 1000)]

    line = assistant.history_text(history)

    assert line.startswith("Trợ lý: ")
    assert len(line) < assistant.HISTORY_CHAR_LIMIT + 20


# --------------------------------------------------------------------------- cần DB


async def seed_kb(db_session, user) -> tuple[BusinessCard, Company]:
    """Một danh thiếp đã xác nhận + một hồ sơ DN, cả hai đã nằm trong KB."""
    company = Company(
        id=uuid.uuid4(),
        user_id=user.id,
        display_name="Công ty TNHH Logistics Đại Việt",
        name_normalized=f"dai-viet-{uuid.uuid4().hex[:8]}",
        aliases=["Đại Việt Logistics"],
    )
    db_session.add(company)
    await db_session.flush()

    db_session.add(
        CompanyProfile(
            id=uuid.uuid4(),
            user_id=user.id,
            company_id=company.id,
            legal_name="Công ty TNHH Logistics Đại Việt",
            tax_code="0301234567",
            industry=["Logistics"],
            products=["Vận tải đường bộ Bắc - Nam"],
            address="Hà Nội, Việt Nam",
            status="generated",
            generated_at=NOW,
            sources={"tax_code": [{"url": "https://masothue.example/0301234567"}]},
        )
    )
    card = BusinessCard(
        id=uuid.uuid4(),
        user_id=user.id,
        image_path="ab/abc.jpg",
        image_hash=uuid.uuid4().hex,
        uploaded_at=NOW,
        status=CardStatus.CONFIRMED.value,
        company_id=company.id,
        full_name="Nguyễn Văn An",
        job_title="Giám đốc kinh doanh",
        company_name_raw="Đại Việt Logistics",
        email="an.nguyen@daiviet-logistics.vn",
        phone="+84912345678",
        language_detected="vi",
    )
    db_session.add(card)
    await db_session.flush()
    await kb_router.reindex(db_session, user)
    return card, company


async def chunk_content(db_session, user, source_type: str) -> str:
    """Nội dung chunk đầu tiên của một loại nguồn — dùng làm câu hỏi cho tương đồng 1.0."""
    rows = await kb_repo.search_similar(
        db_session,
        [0.0] * settings.embedding_dim,
        user_id=user.id,
        top_k=1,
        source_type=source_type,
    )
    return rows[0][0].content


async def post_chat(db_session, payload: dict, user) -> httpx.Response:
    """Gọi `POST /api/chat` qua ASGI **với tư cách `user`**.

    Từ task 12.4 mọi đường dẫn ngoài `/auth/*` đều đòi phiên đăng nhập, nên phần dựng
    client (ghi đè `get_db`, ký cookie, trỏ session của middleware về đúng transaction của
    test) chuyển hẳn sang `conftest.api_client` — một chỗ cho cả bộ test.
    """
    async with api_client(db_session, user) as http:
        return await http.post("/api/chat", json=payload)


async def get_chat(db_session, session_id: uuid.UUID, user) -> httpx.Response:
    async with api_client(db_session, user) as http:
        return await http.get(f"/api/chat/{session_id}")


# --------------------------------------------------------------------------- 8.2 đầu–cuối


async def test_chat_tra_loi_kem_trich_dan_bam_duoc(db_session, user_a, embedder, cliproxy):
    """Đường đi đầy đủ: truy hồi → prompt → model → trích dẫn trỏ đúng `/cards/{id}`."""
    card, _ = await seed_kb(db_session, user_a)
    question = await chunk_content(db_session, user_a, KBSourceType.CARD.value)
    cliproxy.reply("Nguyễn Văn An, giám đốc kinh doanh [1].")

    response = await post_chat(db_session, {"question": question}, user_a)

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Nguyễn Văn An, giám đốc kinh doanh [1]."
    assert body["context_chunks"] >= 1
    assert body["model"] == settings.llm_model
    # Model chỉ trích `[1]`, nên đúng **một** thẻ dù ngữ cảnh có mấy khối — đó là khác biệt giữa
    # "đã truy hồi" và "đã dùng" (xem `services/assistant.py`, quyết định 2).
    assert len(body["citations"]) == 1
    assert body["citations"][0]["url"] == f"/cards/{card.id}"
    assert body["citations"][0]["title"].startswith("Danh thiếp — Nguyễn Văn An")


async def test_chat_ghi_lai_ca_cau_hoi_lan_cau_tra_loi(db_session, user_a, embedder, cliproxy):
    """Một lượt hỏi ghi đúng hai dòng, và trích dẫn được lưu để mở lại hội thoại còn thấy."""
    await seed_kb(db_session, user_a)
    question = await chunk_content(db_session, user_a, KBSourceType.CARD.value)
    cliproxy.reply("Trả lời [1].")

    body = (await post_chat(db_session, {"question": question}, user_a)).json()
    rows = await chat_repo.list_messages(
        db_session, uuid.UUID(body["session_id"]), user_id=user_a.id
    )

    assert [row.role for row in rows] == [ChatRole.USER.value, ChatRole.ASSISTANT.value]
    assert rows[0].citations is None, "lượt người dùng không áp dụng trích dẫn → NULL"
    assert len(rows[1].citations or []) == 1


async def test_chat_khong_tim_duoc_gi_thi_khong_goi_model(db_session, user_a, embedder, cliproxy):
    """Gọi model với ngữ cảnh rỗng là trả tiền để nghe nó nói bằng kiến thức nội tại (R4)."""
    await seed_kb(db_session, user_a)
    cliproxy.reply("KHÔNG ĐƯỢC GỌI")

    body = (await post_chat(db_session, {"question": "giá vàng hôm nay bao nhiêu?"}, user_a)).json()

    assert body["answer"] == prompt.NO_ANSWER_TEXT
    assert body["citations"] == []
    assert body["context_chunks"] == 0
    assert len(cliproxy.calls) == 0


async def test_chat_kb_rong_noi_ro_la_chua_co_du_lieu(db_session, user_a, embedder, cliproxy):
    """ "Chưa nhập gì" và "đã nhập nhưng không liên quan" đòi người dùng làm hai việc khác nhau."""
    body = (await post_chat(db_session, {"question": "công ty nào làm logistics?"}, user_a)).json()

    assert body["answer"] == prompt.EMPTY_KB_TEXT
    assert len(cliproxy.calls) == 0


async def test_chat_gui_dung_system_prompt_va_ngu_canh_cho_model(
    db_session, user_a, embedder, cliproxy
):
    """Kiểm ở tầng HTTP: ngữ cảnh thật sự đi vào payload, không phải chỉ dựng rồi bỏ quên."""
    await seed_kb(db_session, user_a)
    question = await chunk_content(db_session, user_a, KBSourceType.CARD.value)
    cliproxy.reply("Xong [1].")

    await post_chat(db_session, {"question": question}, user_a)

    payload = cliproxy.calls[-1].request.read().decode()
    assert "an.nguyen@daiviet-logistics.vn" in payload
    assert "[1]" in payload
    assert "systemInstruction" in payload


async def test_chat_model_chua_ket_noi_tra_503_va_khong_ghi_gi(
    db_session, user_a, embedder, cliproxy
):
    """Model hỏng thì không để lại phiên rỗng hay câu hỏi cụt trong lịch sử."""
    await seed_kb(db_session, user_a)
    question = await chunk_content(db_session, user_a, KBSourceType.CARD.value)
    cliproxy.fail(401, {"error": {"message": "authentication_error"}})

    response = await post_chat(db_session, {"question": question}, user_a)

    assert response.status_code == 503
    assert "/settings" in response.json()["detail"]
    assert await db_session.scalar(select(func.count()).select_from(ChatMessage)) == 0


async def test_chat_cau_hoi_rong_bi_chan_o_schema(db_session, user_a, embedder, cliproxy):
    response = await post_chat(db_session, {"question": "   "}, user_a)

    assert response.status_code == 422
    assert len(cliproxy.calls) == 0


async def test_chat_phien_khong_ton_tai_tra_404(db_session, user_a, embedder, cliproxy):
    response = await post_chat(
        db_session, {"question": "hỏi gì đó", "session_id": str(uuid.uuid4())}, user_a
    )

    assert response.status_code == 404


# --------------------------------------------------------------------------- 8.3 nhiều lượt


async def test_luot_sau_dua_lich_su_vao_prompt(db_session, user_a, embedder, cliproxy):
    """Lượt 2 phải thấy lượt 1 trong prompt, nếu không đại từ trỏ ngược không hiểu được."""
    await seed_kb(db_session, user_a)
    question = await chunk_content(db_session, user_a, KBSourceType.CARD.value)
    cliproxy.reply("Nguyễn Văn An [1].", "Số là +84912345678 [1].")

    first = (await post_chat(db_session, {"question": question}, user_a)).json()
    second = await post_chat(
        db_session,
        {"question": "Số điện thoại của anh ấy là gì?", "session_id": first["session_id"]},
        user_a,
    )

    assert second.status_code == 200
    assert second.json()["session_id"] == first["session_id"]
    payload = cliproxy.calls[-1].request.read().decode()
    assert "CÁC LƯỢT TRƯỚC" in payload
    assert "Nguyễn Văn An" in payload


async def test_luot_sau_van_truy_hoi_duoc_nho_ghep_cau_hoi_truoc(
    db_session, user_a, embedder, cliproxy
):
    """Câu hỏi lượt 2 không chứa định danh nào; ghép lượt trước vào mới tìm lại được chunk cũ.

    Đây là test bảo vệ `retrieval_query()`: bỏ bước ghép đi thì lượt 2 trả về
    `context_chunks = 0` và trợ lý nói không biết, dù dữ liệu nằm ngay đó.
    """
    await seed_kb(db_session, user_a)
    question = await chunk_content(db_session, user_a, KBSourceType.CARD.value)
    cliproxy.reply("Nguyễn Văn An [1].", "Số là +84912345678 [1].")

    first = (await post_chat(db_session, {"question": question}, user_a)).json()
    second = (
        await post_chat(
            db_session,
            {"question": "còn số điện thoại thì sao?", "session_id": first["session_id"]},
            user_a,
        )
    ).json()

    assert second["context_chunks"] >= 1


async def test_doc_lai_phien_tra_du_luot_va_trich_dan(db_session, user_a, embedder, cliproxy):
    await seed_kb(db_session, user_a)
    question = await chunk_content(db_session, user_a, KBSourceType.CARD.value)
    cliproxy.reply("Trả lời [1].")
    posted = (await post_chat(db_session, {"question": question}, user_a)).json()

    body = (await get_chat(db_session, uuid.UUID(posted["session_id"]), user_a)).json()

    assert len(body["messages"]) == 2
    assert body["messages"][0]["citations"] is None
    assert len(body["messages"][1]["citations"]) == 1
    assert body["title"]


async def test_doc_lai_phien_khong_ton_tai_tra_404(db_session, user_a, embedder, cliproxy):
    assert (await get_chat(db_session, uuid.uuid4(), user_a)).status_code == 404


async def test_hai_luot_ghi_cung_transaction_van_dung_thu_tu(db_session, user_a):
    """Bắt đúng lỗi phát hiện khi chạy test 8.3 lần đầu — xem `repositories/chat.py::_ROLE_ORDER`.

    `now()` của Postgres là thời điểm **bắt đầu transaction**, nên hai dòng ghi trong cùng một
    lượt hỏi có `created_at` bằng nhau tuyệt đối; `id` là UUIDv4 nên cũng không mang thông tin
    thời gian. Hậu quả đo được: `GET /api/chat/{id}` trả câu trả lời đứng **trước** câu hỏi.

    Cố ý ghi câu trả lời **trước** câu hỏi để chứng minh thứ tự đọc ra không phụ thuộc thứ tự
    ghi vào — nếu nó phụ thuộc thì test này xanh vì lý do sai.
    """
    session = await chat_repo.create_session(db_session, user_id=user_a.id, title="thử")
    await chat_repo.add_message(
        db_session, session_id=session.id, role=ChatRole.ASSISTANT, content="đáp", citations=[]
    )
    await chat_repo.add_message(
        db_session, session_id=session.id, role=ChatRole.USER, content="hỏi"
    )

    doc_het = await chat_repo.list_messages(db_session, session.id, user_id=user_a.id)
    gan_nhat = await chat_repo.recent_messages(db_session, session.id, user_id=user_a.id, limit=2)

    assert doc_het[0].created_at == doc_het[1].created_at, "tiền đề của test: cùng transaction"
    assert [row.content for row in doc_het] == ["hỏi", "đáp"]
    assert [row.content for row in gan_nhat] == ["hỏi", "đáp"]


# --------------------------------------------------------------------------- 8.5 lọc phạm vi


async def test_loc_theo_loai_nguon_bo_han_chunk_ngoai_pham_vi(db_session, user_a, embedder):
    """Hỏi bằng đúng nội dung chunk hồ sơ; lọc `card` thì chunk hồ sơ đó không được lọt vào.

    Ghi chú rút ra khi viết test này: câu hỏi đó **vẫn ra kết quả** sau khi lọc, vì chunk danh
    thiếp cũng chứa "Đại Việt Logistics" nên nhánh full-text bắt được nó. Đúng như thiết kế —
    lọc là *thu hẹp phạm vi*, không phải tắt tìm kiếm. Điều phải đúng là **không chunk nào ngoài
    phạm vi lọt ra**, nên khẳng định theo tập `source_type` chứ không theo số lượng.
    """
    await seed_kb(db_session, user_a)
    profile_chunk = await chunk_content(db_session, user_a, KBSourceType.COMPANY_PROFILE.value)

    khong_loc = await retriever.search(db_session, profile_chunk, user_id=user_a.id)
    chi_card = await retriever.search(
        db_session, profile_chunk, user_id=user_a.id, source_type=KBSourceType.CARD
    )

    assert KBSourceType.COMPANY_PROFILE.value in {hit.source_type for hit in khong_loc}
    assert {hit.source_type for hit in chi_card} == {KBSourceType.CARD.value}


async def test_bo_loc_di_tu_api_xuong_tang_truy_hoi(db_session, user_a, embedder, cliproxy):
    """Kiểm bộ lọc thật sự đi qua `ChatFilters` → `assistant.answer()` → SQL, không rơi dọc đường."""
    _, company = await seed_kb(db_session, user_a)
    profile_chunk = await chunk_content(db_session, user_a, KBSourceType.COMPANY_PROFILE.value)
    cliproxy.reply("Hồ sơ đây [1].")

    body = (
        await post_chat(
            db_session,
            {
                "question": profile_chunk,
                "filters": {"source_type": "company_profile", "company_id": str(company.id)},
            },
            user_a,
        )
    ).json()

    assert body["citations"][0]["source_type"] == KBSourceType.COMPANY_PROFILE.value
    assert body["citations"][0]["url"] == f"/companies/{company.id}"
    assert body["citations"][0]["source_urls"] == ["https://masothue.example/0301234567"]


async def test_loc_theo_cong_ty_bat_ca_danh_thiep_lan_ho_so(db_session, user_a, embedder, cliproxy):
    """`metadata.company_id` là khoá chung của hai loại nguồn — lọc theo cột thì sót một nửa."""
    _, company = await seed_kb(db_session, user_a)

    hits = await kb_repo.search_similar(
        db_session,
        [0.0] * settings.embedding_dim,
        user_id=user_a.id,
        top_k=10,
        company_id=company.id,
    )

    assert {chunk.source_type for chunk, _ in hits} == {
        KBSourceType.CARD.value,
        KBSourceType.COMPANY_PROFILE.value,
    }


async def test_loc_theo_cong_ty_khac_thi_khong_ra_gi(db_session, user_a, embedder, cliproxy):
    await seed_kb(db_session, user_a)

    hits = await kb_repo.search_similar(
        db_session,
        [0.0] * settings.embedding_dim,
        user_id=user_a.id,
        top_k=10,
        company_id=uuid.uuid4(),
    )

    assert hits == []


async def test_loc_ap_dung_cho_ca_nhanh_full_text(db_session, user_a, embedder, cliproxy):
    """Hai nhánh lọc lệch nhau thì kết quả trộn ra một tập nửa trong nửa ngoài phạm vi."""
    await seed_kb(db_session, user_a)

    trong_pham_vi = await kb_repo.search_fulltext(
        db_session,
        ["0301234567"],
        user_id=user_a.id,
        top_k=5,
        source_type=KBSourceType.COMPANY_PROFILE.value,
    )
    ngoai_pham_vi = await kb_repo.search_fulltext(
        db_session, ["0301234567"], user_id=user_a.id, top_k=5, source_type=KBSourceType.CARD.value
    )

    assert len(trong_pham_vi) == 1
    assert ngoai_pham_vi == []


async def test_ho_so_chua_gan_cong_ty_khong_lot_vao_pham_vi_cong_ty_nao(
    db_session, user_a, embedder
):
    """Danh thiếp chưa gắn công ty có `metadata.company_id = null` → không thuộc phạm vi nào."""
    card = BusinessCard(
        id=uuid.uuid4(),
        user_id=user_a.id,
        image_path="cd/cde.jpg",
        image_hash=uuid.uuid4().hex,
        uploaded_at=NOW,
        status=CardStatus.CONFIRMED.value,
        full_name="田中 太郎",
        company_name_raw="東京テック株式会社",
        email="tanaka@tokyotech.co.jp",
        language_detected="ja",
    )
    db_session.add(card)
    await db_session.flush()
    await kb_router.reindex(db_session, user_a)

    tat_ca = await kb_repo.search_similar(
        db_session, [0.0] * settings.embedding_dim, user_id=user_a.id, top_k=10
    )
    theo_cong_ty = await kb_repo.search_similar(
        db_session,
        [0.0] * settings.embedding_dim,
        user_id=user_a.id,
        top_k=10,
        company_id=uuid.uuid4(),
    )

    assert len(tat_ca) == 1
    assert theo_cong_ty == []


@pytest.mark.parametrize(
    "source_type,expected",
    [
        (KBSourceType.CARD.value, "Danh thiếp"),
        (KBSourceType.COMPANY_PROFILE.value, "Hồ sơ doanh nghiệp"),
        ("gì đó lạ", "Nguồn"),
    ],
)
def test_nhan_loai_nguon(source_type, expected):
    assert prompt.source_label(source_type) == expected
