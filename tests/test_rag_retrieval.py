"""Test truy hồi RAG: ngưỡng điểm (7.1), hybrid full-text (7.2), auto-ingest khi confirm (7.3).

Chủ sở hữu: Q | Task: 7.1–7.4 | xem Task.md

Phần cần DB dùng `embedder` giả lập của `conftest.py`, nơi vector suy từ **hàm băm của chính
đoạn văn**. Nghe như một hạn chế nhưng lại là thứ làm các test này tất định: hỏi bằng đúng nội
dung một chunk thì tương đồng bằng **1.0**, hỏi bằng bất cứ chuỗi nào khác thì hai vector đơn vị
384 chiều gần như trực giao nên tương đồng ~**0** — hai đầu của thang điểm, đủ để kiểm chắc chắn
việc ngưỡng có cắt đúng hay không.

Đổi lại, các test ở đây **không nói được gì về chất lượng truy hồi thật**: cái đó đo bằng model
thật ở `scripts/eval_retrieval.py` (task 7.4), kết quả ghi trong `docs/retrieval.md`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.core.config import settings
from app.models.card import BusinessCard, CardStatus
from app.models.company import Company
from app.models.kb import KBSourceType
from app.repositories import kb as kb_repo
from app.routers import cards as cards_router
from app.routers import kb as kb_router
from app.services import retriever
from app.services.retriever import Hit

NOW = datetime(2026, 9, 17, 9, 0, tzinfo=UTC)


def make_hit(name: str, **overrides) -> Hit:
    """Một `Hit` tối thiểu — `_fuse()` chỉ quan tâm `chunk_id`, phần còn lại là chỗ chứa."""
    values = {
        "chunk_id": uuid.uuid5(uuid.NAMESPACE_OID, name),
        "source_type": KBSourceType.CARD.value,
        "source_id": uuid.uuid5(uuid.NAMESPACE_DNS, name),
        "content": name,
        "meta": {},
        "score": 0.0,
    }
    values.update(overrides)
    return Hit(**values)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- 7.2 lọc từ khoá


def test_tu_khoa_giu_ca_hai_dang_cua_so_dien_thoai():
    """Người hỏi gõ số như in trên thẻ, KB lưu bản đã chuẩn hoá ở 3.6 — phải bắc cầu được."""
    terms = retriever.query_terms("số 0912 345 678 là của ai?")

    assert "+84912345678" in terms
    assert "0912 345 678" in terms


def test_tu_khoa_bat_email_nguyen_ven():
    """Email phải ra **một** từ khoá, không bị hai regex sau xé thành `a.nguyen` + `abc.vn`."""
    assert retriever.query_terms("ai dùng email a.nguyen@abc.vn?") == ["a.nguyen@abc.vn"]


def test_tu_khoa_bat_ma_so_thue():
    assert "0301234567" in retriever.query_terms("mã số thuế 0301234567 là của công ty nào")


def test_tu_khoa_bat_ten_rieng_viet_hoa_giua_cau():
    terms = retriever.query_terms("công ty Vinamilk và Hòa Phát làm gì?")

    assert "Vinamilk" in terms
    assert "Phát" in terms


def test_tu_khoa_bo_nhan_truong_va_tu_dau_cau():
    """ "Email"/"Công ty" có trong **mọi** chunk nên tìm theo chúng là kéo về cả KB.

    Từ đầu câu cũng bị bỏ vì ai cũng viết hoa nó — giữ lại thì "Ai", "Công" thành từ khoá.
    """
    assert retriever.query_terms("Công ty nào có Email liên hệ") == []


def test_cau_hoi_thuan_ngu_nghia_khong_sinh_tu_khoa_nao():
    """Không có định danh nào thì nhánh full-text **phải** im lặng, nhường cho nhánh vector."""
    assert retriever.query_terms("công ty nào làm về logistics?") == []


# --------------------------------------------------------------------------- 7.2 trộn hai nhánh


def test_trộn_uu_tien_chunk_ca_hai_nhanh_deu_tim_ra():
    """Hạng 2 ở **cả hai** nhánh phải đứng trên hạng 1 ở đúng một nhánh.

    Đây là toàn bộ giá trị của hybrid: hai cách tìm độc lập cùng chỉ vào một chỗ là bằng chứng
    mạnh hơn một cách tìm chỉ vào đó rất chắc chắn.
    """
    chung = make_hit("chung", similarity=0.8)
    rieng_vector = make_hit("chỉ vector", similarity=0.9)
    rieng_text = make_hit("chỉ text", text_rank=0.5, matched_by="text")

    fused = retriever._fuse(
        [rieng_vector, chung],
        [rieng_text, make_hit("chung", text_rank=0.4, matched_by="text")],
    )

    assert fused[0].chunk_id == chung.chunk_id
    assert fused[0].matched_by == "both"
    assert (fused[0].similarity, fused[0].text_rank) == (0.8, 0.4)


def test_trộn_giu_nguyen_thu_tu_khi_tat_nhanh_full_text():
    """Tắt hybrid không được làm đảo lộn thứ tự của nhánh vector."""
    hits = [make_hit(f"chunk {i}", similarity=1.0 - i / 10) for i in range(5)]

    fused = retriever._fuse(hits, [])

    assert [hit.chunk_id for hit in fused] == [hit.chunk_id for hit in hits]
    assert {hit.matched_by for hit in fused} == {"vector"}


def test_trộn_danh_sach_rong_tra_ve_rong():
    assert retriever._fuse([], []) == []


# --------------------------------------------------------------------------- cần DB


async def seed_kb(db_session, user) -> tuple[BusinessCard, Company]:
    """Một danh thiếp đã xác nhận + công ty của nó, đã nằm trong KB."""
    company = Company(
        id=uuid.uuid4(),
        user_id=user.id,
        display_name="Công ty TNHH Logistics ABC",
        name_normalized=f"abc-{uuid.uuid4().hex[:8]}",
        aliases=["ABC Co., Ltd"],
    )
    db_session.add(company)
    await db_session.flush()

    card = BusinessCard(
        id=uuid.uuid4(),
        user_id=user.id,
        image_path="ab/abc.jpg",
        image_hash=uuid.uuid4().hex,
        uploaded_at=NOW,
        status=CardStatus.CONFIRMED.value,
        company_id=company.id,
        full_name="Nguyễn Văn A",
        job_title="Giám đốc kinh doanh",
        company_name_raw="Cty ABC",
        email="a.nguyen@abc.vn",
        phone="+84912345678",
        address="123 Lê Lợi, Quận 1, TP.HCM",
        website="https://abc.vn",
        language_detected="vi",
    )
    db_session.add(card)
    await db_session.flush()
    await kb_router.reindex(db_session, user)
    return card, company


async def _stored_content(db_session, user) -> str:
    """Nội dung chunk duy nhất đang có trong KB **của người này**."""
    hits = await kb_repo.search_similar(
        db_session, [0.0] * settings.embedding_dim, user_id=user.id, top_k=1
    )
    return hits[0][0].content


# --------------------------------------------------------------------------- 7.1 ngưỡng điểm


async def test_nguong_diem_cat_bo_chunk_khong_lien_quan(db_session, user_a, embedder):
    """Hỏi một câu KB không có gì liên quan thì trả **rỗng**, không phải 5 chunk gần nhất.

    Không có bước này thì D8 nhận về mấy chunk ngẫu nhiên và trả lời bằng chúng — đúng kiểu bịa
    mà R4 cấm.
    """
    await seed_kb(db_session, user_a)

    hits = await retriever.search(
        db_session, "giá vàng hôm nay thế nào", hybrid=False, user_id=user_a.id
    )

    assert hits == []


async def test_duoi_nguong_van_tim_thay_neu_ha_nguong(db_session, user_a, embedder):
    """Phân biệt "KB rỗng" với "có nhưng bị ngưỡng cắt" — hai nguyên nhân rất dễ nhầm."""
    await seed_kb(db_session, user_a)

    hits = await retriever.search(
        db_session, "giá vàng hôm nay", min_similarity=-1.0, hybrid=False, user_id=user_a.id
    )

    assert hits, "KB có dữ liệu mà hạ hết ngưỡng vẫn không ra gì"
    assert hits[0].similarity is not None and hits[0].similarity < retriever.MIN_SIMILARITY


async def test_tim_bang_dung_noi_dung_chunk_thi_dat_diem_toi_da(db_session, user_a, embedder):
    await seed_kb(db_session, user_a)
    content = await _stored_content(db_session, user_a)

    hits = await retriever.search(db_session, content, hybrid=False, user_id=user_a.id)

    assert hits[0].similarity == pytest.approx(1.0, abs=1e-6)
    assert hits[0].matched_by == "vector"
    assert hits[0].title.startswith("Danh thiếp — Nguyễn Văn A")


# --------------------------------------------------------------------------- 7.2 hybrid


async def test_full_text_bat_duoc_email_ma_vector_khong_bat_duoc(db_session, user_a, embedder):
    """Câu hỏi chứa một địa chỉ email: nhánh vector rớt ngưỡng, nhánh full-text khớp chính xác."""
    await seed_kb(db_session, user_a)
    query = "ai dùng email a.nguyen@abc.vn?"

    chi_vector = await retriever.search(db_session, query, hybrid=False, user_id=user_a.id)
    ca_hai = await retriever.search(db_session, query, hybrid=True, user_id=user_a.id)

    assert chi_vector == []
    assert len(ca_hai) == 1
    assert ca_hai[0].matched_by == "text"
    assert "a.nguyen@abc.vn" in ca_hai[0].content


async def test_full_text_bat_duoc_so_dien_thoai_go_theo_kieu_noi_dia(db_session, user_a, embedder):
    """`0912 345 678` trong câu hỏi phải tìm ra thẻ lưu `+84912345678` (nhờ `expand_query`)."""
    await seed_kb(db_session, user_a)

    hits = await retriever.search(db_session, "số 0912 345 678 là của ai?", user_id=user_a.id)

    assert len(hits) == 1
    assert hits[0].matched_by == "text"


async def test_full_text_khong_chuan_hoa_thi_truot_dung_so_do(db_session, user_a, embedder):
    """Đối chứng cho test trên: tìm bằng đúng chữ người dùng gõ thì **không** khớp gì cả.

    Có test này thì bước sinh dạng E.164 trong `query_terms()` không thể bị xoá đi mà cả bộ
    test vẫn xanh.
    """
    await seed_kb(db_session, user_a)

    hits = await kb_repo.search_fulltext(db_session, ["0912 345 678"], user_id=user_a.id, top_k=5)

    assert hits == []


async def test_full_text_khong_ghep_and_ca_cau_hoi(db_session, user_a, embedder):
    """Bắt đúng lỗi đo được khi viết bộ test này (xem `repositories/kb.py::search_fulltext`).

    `websearch_to_tsquery` nối mọi từ bằng `AND`, nên ném cả câu hỏi xuống là đòi chunk chứa cả
    "ai" lẫn "dùng". Ghép bằng `OR` từng từ khoá đã lọc thì một mình địa chỉ email là đủ khớp.
    """
    await seed_kb(db_session, user_a)

    ca_cau = await kb_repo.search_fulltext(
        db_session, ["ai dùng email a.nguyen@abc.vn"], user_id=user_a.id, top_k=5
    )
    loc_tu_khoa = await kb_repo.search_fulltext(
        db_session, ["a.nguyen@abc.vn"], user_id=user_a.id, top_k=5
    )

    assert ca_cau == []
    assert len(loc_tu_khoa) == 1


async def test_full_text_khong_co_tu_khoa_thi_khong_cham_db(db_session, user_a, embedder):
    assert await kb_repo.search_fulltext(db_session, [], user_id=user_a.id, top_k=5) == []


async def test_hybrid_khong_tra_ve_trung_chunk(db_session, user_a, embedder):
    """Chunk khớp ở cả hai nhánh chỉ được xuất hiện **một lần**, và mang nhãn `both`."""
    await seed_kb(db_session, user_a)
    content = await _stored_content(db_session, user_a)

    hits = await retriever.search(db_session, content, user_id=user_a.id)

    assert len({hit.chunk_id for hit in hits}) == len(hits)
    assert hits[0].matched_by == "both"


async def test_loc_theo_loai_nguon(db_session, user_a, embedder):
    await seed_kb(db_session, user_a)
    content = await _stored_content(db_session, user_a)

    ho_so = await retriever.search(
        db_session, content, source_type=KBSourceType.COMPANY_PROFILE, user_id=user_a.id
    )

    assert ho_so == []


async def test_cau_hoi_rong_khong_goi_embedder(db_session, user_a, embedder):
    await seed_kb(db_session, user_a)
    truoc = len(embedder.requests)

    assert await retriever.search(db_session, "   ", user_id=user_a.id) == []
    assert len(embedder.requests) == truoc


async def test_cau_hoi_di_qua_kind_query(db_session, user_a, embedder):
    """Nhầm `passage` cho câu hỏi không sinh lỗi nào, chỉ làm chất lượng tụt — phải có test."""
    await seed_kb(db_session, user_a)
    embedder.requests.clear()

    await retriever.search(db_session, "công ty logistics", user_id=user_a.id)

    assert embedder.kinds == ["query"]


# --------------------------------------------------------------------------- 7.3 hook khi confirm


async def _pending_card(db_session, user) -> BusinessCard:
    card = BusinessCard(
        id=uuid.uuid4(),
        user_id=user.id,
        image_path="cd/cde.jpg",
        image_hash=uuid.uuid4().hex,
        uploaded_at=NOW,
        status=CardStatus.NEEDS_REVIEW.value,
        full_name="Trần Thị B",
        job_title="Trưởng phòng",
        company_name_raw="Công ty CP Vận tải XYZ",
        email="b.tran@xyz.vn",
        phone="+84987654321",
        language_detected="vi",
    )
    db_session.add(card)
    await db_session.flush()
    return card


async def test_xac_nhan_danh_thiep_la_vao_kb_ngay(db_session, user_a, embedder):
    """Tiêu chí của 7.3: xác nhận xong là hỏi trợ lý được ngay, không phải bấm reindex."""
    card = await _pending_card(db_session, user_a)

    result = await cards_router.confirm_card(card.id, db_session, user_a)

    assert result.status == CardStatus.CONFIRMED
    assert result.kb_indexed is True
    assert (
        await kb_repo.count_chunks(db_session, user_id=user_a.id, source_type=KBSourceType.CARD)
        == 1
    )

    hits = await retriever.search(db_session, "b.tran@xyz.vn", user_id=user_a.id)
    assert hits and hits[0].source_id == card.id


async def test_embedder_chet_khong_chan_duoc_viec_xac_nhan(db_session, user_a):
    """F3 hỏng không được làm đứng luồng nhập liệu của F1 — cùng lý lẽ với `_upsert_company()`.

    Cố ý **không** dùng fixture `embedder`: hai router respx lồng nhau thì cái ngoài bắt request
    trước, và test sẽ xanh vì lý do sai.
    """
    # Đọc `user_a.id` **trước** khi gọi confirm: đường lỗi của `_sync_kb()` gọi `db.rollback()`,
    # mà `rollback()` làm hết hạn **mọi** object ORM của session — không chỉ object nó vừa ghi dở.
    # Chạm `user_a.id` sau đó là một lượt nạp lại đồng bộ giữa hàm async, tức `MissingGreenlet`.
    # Cùng họ với cái bẫy đã ghi ở `routers/cards.py::_sync_kb`, chỉ lần này nó rơi vào chính test.
    user_id = user_a.id
    card = await _pending_card(db_session, user_a)

    with respx.mock(base_url=settings.embedder_url) as router:
        router.post("/embed").mock(side_effect=httpx.ConnectError("connection refused"))

        result = await cards_router.confirm_card(card.id, db_session, user_a)

    assert result.status == CardStatus.CONFIRMED
    assert result.kb_indexed is False
    assert result.detail and "embedder" in result.detail
    assert (
        await kb_repo.count_chunks(db_session, user_id=user_id, source_type=KBSourceType.CARD) == 0
    )


async def test_sua_the_da_xac_nhan_thi_kb_cap_nhat_theo(db_session, user_a, embedder):
    """Sửa số điện thoại rồi vẫn nghe trợ lý đọc số cũ là lỗi không ai nghĩ tới việc đi tìm."""
    card = await _pending_card(db_session, user_a)
    await cards_router.confirm_card(card.id, db_session, user_a)

    from app.schemas.card import CardUpdateIn

    await cards_router.update_card(
        card.id, CardUpdateIn(job_title="Giám đốc điều hành"), db_session, user_a
    )

    content = await _stored_content(db_session, user_a)
    assert "Giám đốc điều hành" in content
    assert "Trưởng phòng" not in content
    assert (
        await kb_repo.count_chunks(db_session, user_id=user_a.id, source_type=KBSourceType.CARD)
        == 1
    )


async def test_sua_the_chua_xac_nhan_thi_khong_dung_toi_kb(db_session, user_a, embedder):
    """Màn hình review bấm Lưu liên tục; thẻ chưa xác nhận thì chưa bao giờ vào KB."""
    card = await _pending_card(db_session, user_a)
    embedder.requests.clear()

    from app.schemas.card import CardUpdateIn

    await cards_router.update_card(card.id, CardUpdateIn(job_title="Phó phòng"), db_session, user_a)

    assert embedder.requests == []
    assert (
        await kb_repo.count_chunks(db_session, user_id=user_a.id, source_type=KBSourceType.CARD)
        == 0
    )


async def test_xoa_danh_thiep_thi_go_luon_khoi_kb(db_session, user_a, embedder):
    """Không gỡ thì trợ lý vẫn trích dẫn được một danh thiếp người dùng tưởng đã xoá."""
    card = await _pending_card(db_session, user_a)
    await cards_router.confirm_card(card.id, db_session, user_a)

    await cards_router.delete_card(card.id, db_session, user_a)

    assert (
        await kb_repo.count_chunks(db_session, user_id=user_a.id, source_type=KBSourceType.CARD)
        == 0
    )
