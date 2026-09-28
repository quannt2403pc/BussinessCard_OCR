"""Test API danh thiếp: xác nhận, sửa tay, lọc danh sách.

Chủ sở hữu: Q | Task: 10.2, 12.8

Bộ kiểm thử đầu–cuối của 10.1 chạy qua HTTP thật nên cần Docker + model thật, tức **CI không chạy
được** — file này là lưới đỡ hồi quy chạy được trong CI.

Không phủ hết API danh thiếp và không giả vờ là đã phủ: nhắm đúng những hành vi **có người đi
qua** mà lại **không tốn model để kiểm** — chỗ nối sang `company_matching`, ranh giới chuẩn hoá
khi sửa tay, và các đường 4xx.
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image

from app.core.config import settings
from app.models.card import BusinessCard, CardStatus
from app.models.company import Company
from app.models.user import User
from app.routers import cards as cards_router
from app.schemas.card import CardUpdateIn
from app.services import card_batch
from tests.conftest import workspace_id_of

NOW = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)


async def make_company(db_session, user: User, name: str = "Công ty TNHH Phú Cơ") -> Company:
    """Công ty thật trong DB.

    Không dùng UUID bịa: gán một id không có trong bảng `companies` là `ForeignKeyViolation` ngay
    lúc INSERT, đúng như ứng dụng thật sẽ hành xử.
    """
    company = Company(
        id=uuid.uuid4(),
        workspace_id=await workspace_id_of(db_session, user),
        user_id=user.id,
        display_name=name,
        name_normalized=f"phu co-{uuid.uuid4().hex[:8]}",
        aliases=[],
    )
    db_session.add(company)
    await db_session.flush()
    return company


async def make_card(db_session, user: User, **overrides) -> BusinessCard:
    values = {
        "id": uuid.uuid4(),
        "workspace_id": await workspace_id_of(db_session, user),
        "user_id": user.id,
        "image_path": "ef/efg.jpg",
        "image_hash": uuid.uuid4().hex,
        "uploaded_at": NOW,
        "status": CardStatus.NEEDS_REVIEW.value,
        "full_name": "Lê Thị C",
        "job_title": "Trưởng phòng kinh doanh",
        "company_name_raw": "Công ty TNHH Phú Cơ",
        "email": "c.le@phuco.vn",
        "phone": "+84912345678",
        "phone_alt": "+84987654321",
        "website": "https://phuco.vn",
        "language_detected": "vi",
    }
    values.update(overrides)
    card = BusinessCard(**values)
    db_session.add(card)
    await db_session.flush()
    return card


# --------------------------------------------------------- 4.3 xác nhận → gắn công ty (I-18)


async def test_xac_nhan_truyen_ca_email_va_website_xuong_upsert_company(
    db_session, user_a, workspace_a, active_a, embedder, monkeypatch
):
    """I-18: thiếu hai tham số này thì quy tắc gộp theo tên miền không bao giờ chạy.

    `upsert_company()` nhận `email=` / `website=` để rút tên miền, và tên miền cắt theo **cả hai
    chiều**: chung miền thì nới ngưỡng so mờ, khác miền thì không bao giờ gộp.

    Test bám vào **chữ ký lời gọi**, không bám vào kết quả gộp — kết quả là hành vi trong file
    của T và T có quyền đổi.
    """
    card = await make_card(db_session, user_a, company_name_vi="Công ty TNHH Phú Cơ (VN)")
    company = await make_company(db_session, user_a)
    seen: dict[str, object] = {}

    async def fake_upsert(
        db, raw_name, *, workspace_id, user_id, display_name_vi=None, email=None, website=None
    ):
        seen.update(
            raw_name=raw_name,
            workspace_id=workspace_id,
            user_id=user_id,
            display_name_vi=display_name_vi,
            email=email,
            website=website,
        )
        return company.id

    monkeypatch.setattr("app.services.company_matching.upsert_company", fake_upsert, raising=True)

    result = await cards_router.confirm_card(card.id, db_session, user_a, active_a)

    assert result.company_matched is True
    assert seen == {
        "raw_name": "Công ty TNHH Phú Cơ",
        "workspace_id": workspace_a,
        "user_id": user_a.id,
        # Bản Việt hoá của thẻ phải đi xuống, không thì danh sách công ty hiện chữ gốc.
        "display_name_vi": "Công ty TNHH Phú Cơ (VN)",
        "email": "c.le@phuco.vn",
        "website": "https://phuco.vn",
    }


async def test_xac_nhan_van_xong_khi_the_khong_co_ten_cong_ty(
    db_session, user_a, active_a, embedder
):
    """Không đọc ra tên công ty thì vẫn phải xác nhận được, và phải nói rõ vì sao chưa gắn."""
    card = await make_card(db_session, user_a, company_name_raw=None)

    result = await cards_router.confirm_card(card.id, db_session, user_a, active_a)

    assert result.status == CardStatus.CONFIRMED
    assert result.company_matched is False
    assert result.detail and "tên công ty" in result.detail


async def test_xac_nhan_khong_ghi_de_cong_ty_da_gan_tu_truoc(
    db_session, user_a, active_a, embedder, monkeypatch
):
    """Thẻ đã có `company_id` (người dùng gắn tay) thì lượt xác nhận không được gọi lại matching."""
    company = await make_company(db_session, user_a)
    card = await make_card(db_session, user_a, company_id=company.id)

    async def must_not_run(*args, **kwargs):  # pragma: no cover — chạy vào là hỏng test
        raise AssertionError("không được gọi upsert_company khi thẻ đã gắn công ty")

    monkeypatch.setattr("app.services.company_matching.upsert_company", must_not_run)

    result = await cards_router.confirm_card(card.id, db_session, user_a, active_a)

    assert result.company_id == company.id
    assert result.company_matched is True


# --------------------------------------------------------------------- 4.2 sửa tay


async def test_patch_chuan_hoa_sdt_va_email_giong_luc_quet(db_session, user_a, active_a):
    """Người gõ `0912 345 678` thì DB phải lưu `+84912345678`, y như đường đi của OCR."""
    card = await make_card(db_session, user_a, phone=None, email=None)

    result = await cards_router.update_card(
        card.id,
        CardUpdateIn(phone="0912 345 678", email="  C.Le@PhuCo.VN  "),
        db_session,
        user_a,
        active_a,
    )

    assert result.phone == "+84912345678"
    assert result.email == "c.le@phuco.vn"


async def test_patch_chi_sua_phone_thi_khong_xoa_mat_phone_alt(db_session, user_a, active_a):
    """Ranh giới của `_normalize_edits()`: sửa ô nào chỉ đổi ô đó.

    `normalize_card_fields()` xử lý `phone`/`phone_alt` như một cặp và luôn ghi lại cả hai — dùng
    lại nó ở đây thì một lần PATCH gửi mỗi `phone` sẽ xoá sạch số thứ hai đang có.
    """
    card = await make_card(db_session, user_a)

    result = await cards_router.update_card(
        card.id, CardUpdateIn(phone="0333222111"), db_session, user_a, active_a
    )

    assert result.phone == "+84333222111"
    assert result.phone_alt == "+84987654321"


async def test_patch_khong_co_truong_nao_thi_400(db_session, user_a, active_a):
    card = await make_card(db_session, user_a)

    with pytest.raises(HTTPException) as exc:
        await cards_router.update_card(card.id, CardUpdateIn(), db_session, user_a, active_a)

    assert exc.value.status_code == 400


async def test_patch_the_khong_ton_tai_thi_404(db_session, user_a, active_a):
    with pytest.raises(HTTPException) as exc:
        await cards_router.update_card(
            uuid.uuid4(), CardUpdateIn(job_title="X"), db_session, user_a, active_a
        )

    assert exc.value.status_code == 404


# --------------------------------------------------------------------- 4.1 danh sách


async def test_loc_theo_trang_thai_la_tra_400_chu_khong_tra_rong(db_session, user_a, active_a):
    """Rỗng đọc như "chưa có danh thiếp nào" — người dùng sẽ đi tìm lỗi ở chỗ upload."""
    with pytest.raises(HTTPException) as exc:
        await cards_router.list_cards(db_session, user_a, active_a, card_status="khong-ton-tai")

    assert exc.value.status_code == 400
    assert "pending" in str(exc.value.detail)


async def test_so_trang_tinh_tu_tong_khong_tu_so_dong_tra_ve(db_session, user_a, active_a):
    """Trang cuối rỗng vẫn phải biết còn bao nhiêu trang, nếu không nút *về trước* dẫn vào hư không."""
    for _ in range(3):
        await make_card(db_session, user_a, image_hash=uuid.uuid4().hex)

    result = await cards_router.list_cards(db_session, user_a, active_a, size=2, page=1)

    assert result.total >= 3
    assert result.pages == max(1, -(-result.total // 2))


# ------------------------------------------------------------------ xếp hàng upload nhiều ảnh


def _anh_that(color: str = "white") -> bytes:
    """Một ảnh PNG hợp lệ, nhỏ nhất có thể mà `image_service.preprocess()` vẫn nhận."""
    buffer = io.BytesIO()
    Image.new("RGB", (48, 30), color).save(buffer, format="PNG")
    return buffer.getvalue()


async def test_stage_gan_ca_nguoi_bam_nut_chu_khong_rieng_khong_gian(
    db_session, user_a: User, tmp_path, monkeypatch
) -> None:
    """Mục xếp hàng phải mang **cả hai** id — `I-39`.

    `_stage()` từng nhận `user_id` nhưng **quên gán vào `BatchItem`**: lô vẫn nhận 202, rồi mọi
    ảnh chết ở nền tại bước chọn model với đúng một câu "Lỗi ngoài dự kiến".

    Không ca nào bắt được vì mọi test của batch đều dựng `BatchItem` bằng tay và tự điền sẵn hai
    id. Ca này đi qua chính `_stage()`, chỗ duy nhất dựng `BatchItem` thật.
    """
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    workspace_id = await workspace_id_of(db_session, user_a)

    item = await cards_router._stage(
        db_session,
        UploadFile(filename="the.png", file=io.BytesIO(_anh_that())),
        workspace_id=workspace_id,
        user_id=user_a.id,
    )

    assert item.status is card_batch.ItemStatus.PENDING
    assert item.workspace_id == workspace_id
    assert item.user_id == user_a.id, "thiếu user_id thì cả lô chết ở bước chọn model"


async def test_stage_gan_du_hai_id_ca_khi_anh_da_quet_truoc_do(
    db_session, user_a: User, tmp_path, monkeypatch
) -> None:
    """Nhánh ảnh trùng cũng phải mang đủ hai id.

    Nhánh này không đi qua `_scan()` nên hôm nay thiếu id cũng không nổ, nhưng một `BatchItem` có
    `card_id` mà không biết ai tạo ra nó là một bản ghi tự mâu thuẫn.
    """
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    workspace_id = await workspace_id_of(db_session, user_a)
    raw = _anh_that("ivory")

    first = await cards_router._stage(
        db_session,
        UploadFile(filename="the.png", file=io.BytesIO(raw)),
        workspace_id=workspace_id,
        user_id=user_a.id,
    )
    again = await cards_router._stage(
        db_session,
        UploadFile(filename="the-lan-hai.png", file=io.BytesIO(raw)),
        workspace_id=workspace_id,
        user_id=user_a.id,
    )

    assert again.duplicate is True
    assert again.card_id == first.card_id
    assert again.workspace_id == workspace_id
    assert again.user_id == user_a.id
