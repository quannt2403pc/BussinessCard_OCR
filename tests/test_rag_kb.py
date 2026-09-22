"""Test Knowledge Base: serialize + chunk (6.2), repository kb_chunks (6.3), embeddings & reindex (6.4).

Chủ sở hữu: Q | Task: 6.1–6.4 | xem Task.md

Phần đầu không cần gì ngoài Python. Phần sau cần Postgres thật (fixture `db_session` tự skip
nếu không có) vì `vector(384)`, `JSONB` và toán tử `<=>` không có bản giả nào đáng tin.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest
import respx
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.card import BusinessCard, CardStatus
from app.models.company import Company, CompanyProfile
from app.models.kb import KBSourceType
from app.repositories import kb as kb_repo
from app.routers import kb as kb_router
from app.schemas.kb import ReindexScope
from app.services import embeddings, kb
from app.services.embeddings import (
    EmbedderUnavailableError,
    EmbeddingDimError,
    EmbeddingError,
)
from tests.conftest import fake_vector

NOW = datetime(2026, 9, 16, 8, 30, tzinfo=UTC)

#: Chủ sở hữu mặc định của các factory dưới đây. Phần lớn test trong file này **chỉ
#: serialize** (không ghi DB) nên một UUID rời là đủ; test nào ghi thật thì truyền
#: `user_id=user_a.id` để khoá ngoại `users` khớp — xem `seed()`.
ANY_USER = uuid.uuid4()


def make_card(**overrides) -> BusinessCard:
    values = {
        "id": uuid.uuid4(),
        "user_id": ANY_USER,
        "image_path": "ab/abc.jpg",
        "image_hash": uuid.uuid4().hex,
        "uploaded_at": NOW,
        "status": CardStatus.CONFIRMED.value,
        "full_name": "Nguyễn Văn A",
        "job_title": "Giám đốc kinh doanh",
        "company_name_raw": "Cty ABC",
        "email": "a.nguyen@abc.vn",
        "phone": "+84912345678",
        "address": "123 Lê Lợi, Quận 1, TP.HCM",
        "website": "https://abc.vn",
        "language_detected": "vi",
    }
    values.update(overrides)
    return BusinessCard(**values)


def make_company(**overrides) -> Company:
    values = {
        "id": uuid.uuid4(),
        "user_id": ANY_USER,
        "display_name": "Công ty TNHH ABC",
        "name_normalized": f"abc-{uuid.uuid4().hex[:8]}",
        "aliases": ["ABC Co., Ltd"],
    }
    values.update(overrides)
    return Company(**values)


def make_profile(company: Company, **overrides) -> CompanyProfile:
    values = {
        "id": uuid.uuid4(),
        "user_id": company.user_id,
        "company_id": company.id,
        "legal_name": "Công ty TNHH Thương mại ABC",
        "tax_code": "0301234567",
        "founded_year": 2005,
        "size_label": "Vừa",
        "employee_range": "200-500",
        "industry": ["Logistics", "Kho vận"],
        "products": ["Vận tải đường bộ", "Kho ngoại quan"],
        "address": "456 Nguyễn Huệ, Quận 1, TP.HCM",
        "website": "https://abc.vn",
        "description": "ABC là doanh nghiệp logistics hoạt động từ 2005.",
        "sources": {
            "tax_code": [{"url": "https://masothue.com/0301234567", "title": "masothue.com"}],
            "address": [{"url": "https://abc.vn/lien-he"}],
        },
        "status": "generated",
        "generated_at": NOW.replace(tzinfo=None),
    }
    values.update(overrides)
    return CompanyProfile(**values)


# --------------------------------------------------------------------------- 6.2 serialize


def test_serialize_card_bo_truong_rong_va_dung_nhan_tieng_viet():
    title, body = kb.serialize_card(make_card(phone_alt=None), company_name="Công ty TNHH ABC")

    assert title == "Danh thiếp — Nguyễn Văn A · Công ty TNHH ABC"
    assert "Chức vụ: Giám đốc kinh doanh" in body
    assert "Điện thoại khác" not in body
    assert "Ngày thu thập: 2026-09-16" in body


def test_serialize_card_giu_ca_ten_in_tren_the_khi_khac_ten_da_gop():
    _, body = kb.serialize_card(make_card(), company_name="Công ty TNHH ABC")

    assert "Công ty: Công ty TNHH ABC" in body
    assert "Tên công ty trên danh thiếp: Cty ABC" in body


def test_serialize_card_khong_lap_lai_ten_cong_ty_y_het():
    _, body = kb.serialize_card(
        make_card(company_name_raw="Công ty TNHH ABC"), company_name="Công ty TNHH ABC"
    )

    assert "Tên công ty trên danh thiếp" not in body


def test_serialize_card_ghi_ten_ngon_ngu_chu_khong_chi_ghi_ma():
    """Chunk phải nói *bằng tiếng Việt* rằng thẻ này là tiếng Nhật (task 10.2, lỗi B-03).

    Trước 10.2 chunk chỉ ghi `Ngôn ngữ: ja`. Đo ở 10.1 trên câu 8 của `docs/qa-testset.md`:
    truy hồi đúng thẻ `田中 太郎` nhưng trợ lý vẫn trả "Không có thông tin này trong dữ liệu đã
    nhập", vì không chỗ nào trong ngữ cảnh nói thẻ này là tiếng Nhật và quy tắc 1 của
    `prompts/assistant.py` cấm model tự suy ra từ kiến thức sẵn có.
    """
    _, body = kb.serialize_card(make_card(language_detected="ja"))

    assert "Ngôn ngữ: Tiếng Nhật (ja)" in body


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("vi", "Ngôn ngữ: Tiếng Việt (vi)"),
        ("EN", "Ngôn ngữ: Tiếng Anh (EN)"),  # hoa thường không được làm mất nhãn
        ("ko", "Ngôn ngữ: Tiếng Hàn (ko)"),
        ("zh", "Ngôn ngữ: Tiếng Trung (zh)"),
    ],
)
def test_serialize_card_phu_du_nam_ngon_ngu_trong_pham_vi(code: str, expected: str):
    _, body = kb.serialize_card(make_card(language_detected=code))

    assert expected in body


def test_serialize_card_ma_ngon_ngu_la_thi_giu_nguyen_khong_bia_ten():
    """Mã không có nhãn trong `LANGUAGE_LABELS` → ghi lại đúng mã, không đoán tên.

    Trước EX-05 ca này dùng `th`: hồi đó phạm vi đóng ở 5 ngôn ngữ nên tiếng Thái đúng là "mã
    lạ". Nay `th` đã có nhãn (EX-05 mở phạm vi ra không giới hạn và bảng nhãn dài thêm), nên ca
    kiểm phải chuyển sang một mã thật sự chưa có nhãn — `sw` (Swahili). **Hành vi được bảo vệ
    không đổi một chữ**: gặp mã lạ thì chép nguyên mã, tuyệt đối không bịa tên tiếng Việt cho nó.
    """
    _, body = kb.serialize_card(make_card(language_detected="sw"))

    assert "Ngôn ngữ: sw" in body


def test_serialize_card_khong_co_ngon_ngu_thi_bo_han_dong():
    _, body = kb.serialize_card(make_card(language_detected=None))

    assert "Ngôn ngữ" not in body


def test_serialize_ho_so_dua_mo_ta_xuong_cuoi():
    company = make_company()
    title, body = kb.serialize_company_profile(company, make_profile(company))

    assert title == "Hồ sơ doanh nghiệp — Công ty TNHH ABC"
    assert "Mã số thuế: 0301234567" in body
    assert "Ngành nghề: Logistics, Kho vận" in body
    assert body.splitlines()[-1].startswith("Mô tả:")


def test_serialize_ho_so_khong_nhet_url_vao_van_ban_dem_nhung():
    company = make_company()
    _, body = kb.serialize_company_profile(company, make_profile(company))

    assert "masothue.com" not in body


# --------------------------------------------------------------------------- 6.2 chunk


def test_chunk_van_ban_ngan_la_mot_chunk():
    assert kb.chunk_text("Mã số thuế: 0301234567\nĐịa chỉ: Hà Nội") == [
        "Mã số thuế: 0301234567\nĐịa chỉ: Hà Nội"
    ]


def test_chunk_khong_cat_giua_mot_dong_truong_du_lieu():
    lines = [f"Trường {index}: {'x' * 60}" for index in range(10)]

    for chunk in kb.chunk_text("\n".join(lines), max_chars=200):
        for line in chunk.splitlines():
            assert line in lines  # dòng nào cũng còn nguyên vẹn


def test_chunk_dong_qua_dai_bi_cat_va_co_phan_lap():
    long_line = "Mô tả: " + " ".join(f"câu{index}" for index in range(300))

    chunks = kb.chunk_text(long_line, max_chars=200, overlap=40)

    assert len(chunks) > 1
    assert all(len(chunk) <= 200 for chunk in chunks)
    # Phần lặp giữ cho câu bị cắt đôi vẫn khớp được ở một trong hai chunk.
    assert chunks[0][-10:] in chunks[1]


def test_chunk_van_ban_cjk_khong_co_khoang_trang_van_cat_duoc():
    chunks = kb.chunk_text("説明: " + "会社概要" * 200, max_chars=150)

    assert len(chunks) > 1
    assert all(len(chunk) <= 150 for chunk in chunks)


def test_chunk_bo_qua_dong_trong():
    assert kb.chunk_text("A: 1\n\n\nB: 2") == ["A: 1\nB: 2"]


# --------------------------------------------------------------------------- 6.2 Document


def test_moi_chunk_deu_mang_theo_dong_tieu_de():
    company = make_company()
    profile = make_profile(company, description="Đoạn mô tả rất dài. " * 200)

    document = kb.build_profile_document(company, profile)

    assert len(document.chunks) > 1
    for index, chunk in enumerate(document.chunks):
        assert chunk.content.startswith("Hồ sơ doanh nghiệp — Công ty TNHH ABC")
        assert chunk.meta["chunk_index"] == index
        assert chunk.meta["chunk_count"] == len(document.chunks)


def test_ho_so_neo_theo_id_cong_ty_chu_khong_phai_id_ho_so():
    """Bấm "Tạo lại hồ sơ" (6.7) sinh hàng mới; neo theo công ty thì bản cũ bị ghi đè."""
    company = make_company()
    document = kb.build_profile_document(company, make_profile(company))

    assert document.source_id == company.id
    assert document.source_type is KBSourceType.COMPANY_PROFILE


def test_metadata_ho_so_rut_duoc_url_nguon_cho_trich_dan():
    company = make_company()
    meta = kb.build_profile_document(company, make_profile(company)).chunks[0].meta

    assert meta["sources"] == ["https://masothue.com/0301234567", "https://abc.vn/lien-he"]
    assert meta["company_id"] == str(company.id)


def test_sources_sai_kieu_thi_bo_qua_chu_khong_lam_hong_ca_luot_index():
    company = make_company()
    document = kb.build_profile_document(company, make_profile(company, sources="hỏng"))

    assert document.chunks[0].meta["sources"] == []


def test_danh_thiep_trong_ron_khong_sinh_chunk_nao():
    card = make_card(
        full_name=None,
        job_title=None,
        company_name_raw=None,
        email=None,
        phone=None,
        address=None,
        website=None,
        language_detected=None,
        uploaded_at=None,
    )

    assert kb.build_card_document(card).chunks == []


# --------------------------------------------------------------------------- 6.4 client embedder


async def test_embed_chia_batch_va_giu_nguyen_thu_tu(embedder):
    texts = [f"đoạn {index}" for index in range(embeddings.MAX_BATCH + 5)]

    vectors = await embeddings.embed_passages(texts)

    assert len(vectors) == len(texts)
    assert vectors[0] == pytest.approx(fake_vector("đoạn 0"))
    assert vectors[-1] == pytest.approx(fake_vector(texts[-1]))
    assert embedder.batch_sizes == [embeddings.MAX_BATCH, 5]


async def test_embed_query_gui_dung_kind(embedder):
    await embeddings.embed_query("công ty ABC làm gì?")
    await embeddings.embed_passages(["Hồ sơ doanh nghiệp — ABC"])

    # Gắn nhầm `passage` cho câu hỏi không gây lỗi nào, chỉ làm truy hồi tệ đi — Plan.md 2.6.
    assert embedder.kinds == ["query", "passage"]


async def test_embed_danh_sach_rong_khong_goi_mang(embedder):
    assert await embeddings.embed_passages([]) == []
    assert embedder.requests == []


async def test_embed_chan_doan_trong_truoc_khi_goi(embedder):
    with pytest.raises(EmbeddingError, match="thứ 1"):
        await embeddings.embed_passages(["có nội dung", "   "])

    assert embedder.requests == []


async def test_embed_bao_loi_ro_khi_lech_so_chieu():
    with respx.mock(base_url=settings.embedder_url) as router:
        router.post("/embed").mock(
            return_value=httpx.Response(
                200, json={"vectors": [[0.1, 0.2]], "dim": 2, "model": "model-la"}
            )
        )

        with pytest.raises(EmbeddingDimError, match="vector\\(384\\)"):
            await embeddings.embed_passages(["một đoạn"])


async def test_embed_thu_lai_khi_503_roi_bao_khong_goi_duoc(monkeypatch):
    monkeypatch.setattr(embeddings.asyncio, "sleep", _no_sleep)

    with respx.mock(base_url=settings.embedder_url) as router:
        route = router.post("/embed").mock(return_value=httpx.Response(503))

        with pytest.raises(EmbedderUnavailableError, match="docker compose ps"):
            await embeddings.embed_passages(["một đoạn"])

        assert route.call_count == embeddings.DEFAULT_ATTEMPTS


async def test_embed_khong_thu_lai_khi_422():
    """422 là ta gửi sai — thử lại vẫn sai y như vậy."""
    with respx.mock(base_url=settings.embedder_url) as router:
        route = router.post("/embed").mock(return_value=httpx.Response(422, json={"detail": "x"}))

        with pytest.raises(EmbeddingError):
            await embeddings.embed_passages(["một đoạn"])

        assert route.call_count == 1


async def _no_sleep(_seconds: float) -> None:
    return None


# --------------------------------------------------------------------------- 6.3 + 6.4 cần DB


async def seed(db_session, user) -> tuple[BusinessCard, Company, CompanyProfile]:
    company = make_company(user_id=user.id)
    db_session.add(company)
    await db_session.flush()

    card = make_card(user_id=user.id, company_id=company.id)
    profile = make_profile(company)
    db_session.add_all([card, profile])
    await db_session.flush()
    return card, company, profile


async def test_reindex_ghi_ca_danh_thiep_lan_ho_so(db_session, user_a, embedder):
    card, company, _ = await seed(db_session, user_a)

    result = await kb_router.reindex(db_session, user_a)

    assert (result.cards, result.profiles) == (1, 1)
    assert result.chunks == result.total_chunks > 0
    assert result.model == settings.embedding_model
    assert (
        await kb_repo.count_chunks(db_session, user_id=user_a.id, source_type=KBSourceType.CARD)
        == 1
    )
    assert (
        await kb_repo.count_chunks(
            db_session, user_id=user_a.id, source_type=KBSourceType.COMPANY_PROFILE
        )
        >= 1
    )


async def test_reindex_khong_dua_vao_expire_on_commit(db_session, user_a, embedder):
    """Bắt đúng lỗi đã gặp khi chạy thật 2026-09-16.

    `index_documents()` commit sau mỗi lô, và commit làm mọi object ORM hết hạn. Bản đầu của
    `_reindex_cards()` đọc `rows[-1][0].id` **sau** lời gọi đó → SQLAlchemy nạp lại đồng bộ
    giữa một hàm async và nổ `MissingGreenlet`. Ở app thật không thấy vì `SessionLocal` đặt
    `expire_on_commit=False`; test này dùng session **có** hết hạn để lỗi ấy không quay lại.
    """
    await seed(db_session, user_a)
    strict = AsyncSession(
        bind=await db_session.connection(),
        join_transaction_mode="create_savepoint",
        expire_on_commit=True,
    )

    result = await kb_router.reindex(strict, user_a)

    assert (result.cards, result.profiles) == (1, 1)
    await strict.close()


async def test_reindex_hai_lan_khong_nhan_doi_du_lieu(db_session, user_a, embedder):
    await seed(db_session, user_a)

    first = await kb_router.reindex(db_session, user_a)
    second = await kb_router.reindex(db_session, user_a)

    assert second.total_chunks == first.total_chunks


async def test_reindex_bo_qua_the_chua_xac_nhan_va_ho_so_draft(db_session, user_a, embedder):
    company = make_company(user_id=user_a.id)
    db_session.add(company)
    await db_session.flush()
    db_session.add_all(
        [
            make_card(
                user_id=user_a.id,
                company_id=company.id,
                status=CardStatus.NEEDS_REVIEW.value,
            ),
            make_profile(company, status="draft", description=None),
        ]
    )
    await db_session.flush()

    result = await kb_router.reindex(db_session, user_a)

    assert (result.cards, result.profiles, result.chunks) == (0, 0, 0)


async def test_reindex_gioi_han_pham_vi_theo_scope(db_session, user_a, embedder):
    await seed(db_session, user_a)

    result = await kb_router.reindex(db_session, user_a, scope=ReindexScope.CARD)

    assert (result.cards, result.profiles) == (1, 0)


async def test_reindex_bao_503_khi_embedder_chua_len(db_session, user_a):
    await seed(db_session, user_a)

    with respx.mock(base_url=settings.embedder_url) as router:
        router.get("/health").mock(side_effect=httpx.ConnectError("connection refused"))

        with pytest.raises(HTTPException) as exc:
            await kb_router.reindex(db_session, user_a)

    assert exc.value.status_code == 503


async def test_tim_theo_vector_tra_ve_dung_chunk_gan_nhat(db_session, user_a, embedder):
    card, company, _ = await seed(db_session, user_a)
    await kb_router.reindex(db_session, user_a)

    chunk_content = (await kb_repo.card_batch(db_session, user_id=user_a.id))[0][0]
    assert chunk_content is not None  # giữ cho mypy vui; dữ liệu đã có ở trên

    truy_van = await kb_repo.search_similar(
        db_session, fake_vector("không liên quan"), user_id=user_a.id, top_k=5
    )
    assert truy_van, "KB rỗng — reindex chưa ghi được gì"

    # Tìm bằng đúng vector của một chunk đã lưu thì chính nó phải đứng đầu, khoảng cách ~0.
    first = truy_van[0][0]
    exact = await kb_repo.search_similar(
        db_session, fake_vector(first.content), user_id=user_a.id, top_k=1
    )
    assert exact[0][0].id == first.id
    assert exact[0][1] == pytest.approx(0.0, abs=1e-6)


async def test_xoa_nguon_khoi_kb(db_session, user_a, embedder):
    card, _, _ = await seed(db_session, user_a)
    await kb.ingest_card(db_session, card)

    removed = await kb_repo.delete_for_source(
        db_session, user_id=user_a.id, source_type=KBSourceType.CARD, source_id=card.id
    )

    assert removed == 1
    assert (
        await kb_repo.count_chunks(db_session, user_id=user_a.id, source_type=KBSourceType.CARD)
        == 0
    )


async def test_ingest_ho_so_la_ham_T_goi_o_task_7_5(db_session, user_a, embedder):
    _, company, profile = await seed(db_session, user_a)

    written = await kb.ingest_company_profile(db_session, profile, company=company)

    assert written >= 1
    assert embedder.kinds == ["passage"]
