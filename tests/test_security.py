"""Test cổng đăng nhập + tách dữ liệu ở phần của Q — tiêu chí **A8**, và phần A9 thuộc F1/F3.

Chủ sở hữu: Q | Task: 12.4, 12.5

Ranh giới với `tests/test_isolation.py` (của T): file đó phủ **bảng ca đầy đủ** của A9 trên cả ba
chức năng. File này chỉ giữ những khẳng định về **chính cơ chế `core/security.py`**:

1. **Mặc định là ĐÓNG** — duyệt mọi route đã gắn vào app, cái nào không nằm trong danh sách miễn
   thì phải chặn. Route mới quên bảo vệ sẽ làm test này đỏ ngay.
2. **Chặn đúng kiểu theo loại client**: trình duyệt nhận `303`, API nhận `401` JSON.
3. **Tài nguyên của người khác trả `404`, không phải `403`**.
"""

from __future__ import annotations

import uuid

import pytest
from starlette.routing import Mount

from app.core import security
from app.main import app
from app.models.card import BusinessCard, CardStatus
from app.models.user import User
from app.repositories import kb as kb_repo
from app.routers import cards as cards_router
from app.services import kb
from tests.conftest import api_client, workspace_id_of

HTML_HEADERS = {"accept": "text/html,application/xhtml+xml"}

#: Route có tham số đường dẫn: điền một UUID bất kỳ để dựng được URL cụ thể.
ANY_UUID = str(uuid.uuid4())


def walk_routes(routes: object) -> list[object]:
    """Làm phẳng bảng route của app.

    ⚠️ Không duyệt phẳng `app.routes` được: mỗi `include_router()` nằm trong một `_IncludedRouter`
    (router thật ở `.original_router`), nên `app.routes` chỉ có 15 phần tử mà đúng **2** là route
    của ứng dụng. Bản đầu của test này đọc phẳng và "đạt" trên 1 route — xanh vì không kiểm gì cả.
    """
    found: list[object] = []
    for route in routes or []:  # type: ignore[union-attr]
        if isinstance(route, Mount):  # `/static` — miễn theo A8, và không có `methods`
            continue
        nested = getattr(route, "routes", None) or getattr(
            getattr(route, "original_router", None), "routes", None
        )
        if nested is not None and getattr(route, "path", None) is None:
            found.extend(walk_routes(nested))
        elif hasattr(route, "path") and hasattr(route, "methods"):
            found.append(route)
    return found


def all_guarded_urls() -> list[tuple[str, str]]:
    """`(method, url)` của mọi route **đáng lẽ phải chặn**, đọc từ chính app đang chạy."""
    urls: list[tuple[str, str]] = []
    for route in walk_routes(app.routes):
        if security.is_exempt(route.path):  # type: ignore[attr-defined]
            continue
        path: str = route.path  # type: ignore[attr-defined]
        for name in ("card_id", "company_id", "session_id", "job_id"):
            path = path.replace(f"{{{name}}}", ANY_UUID)
        if "{" in path:  # còn tham số lạ → bỏ qua chứ không đoán kiểu
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):  # type: ignore[attr-defined]
            urls.append((method, path))
    return urls


# --------------------------------------------------------------------------- 12.4 cổng vào


async def test_moi_route_khong_duoc_mien_deu_bi_chan(db_session) -> None:
    """Quét **toàn bộ** route của app: khách chưa đăng nhập không được vào bất kỳ đâu.

    Ca này thay cho cả một danh sách viết tay, và là lý do `core/security.py` chặn bằng middleware
    thay vì bằng `Depends` trên từng route.
    """
    guarded = all_guarded_urls()
    assert len(guarded) > 15, "đọc sai danh sách route — không thể chỉ có vài route cần chặn"

    async with api_client(db_session) as guest:
        for method, url in guarded:
            response = await guest.request(method, url, headers=HTML_HEADERS)
            assert response.status_code in (303, 401), f"{method} {url} không bị chặn"
            if response.status_code == 303:
                assert response.headers["location"].startswith("/auth/login?next=")


@pytest.mark.parametrize("path", ["/health", "/auth/login", "/auth/register"])
async def test_duong_dan_duoc_mien_van_vao_duoc_khi_chua_dang_nhap(db_session, path: str) -> None:
    """Đúng danh sách miễn của A8, không rộng hơn: `/health` cho CD, `/auth/*` để đăng nhập."""
    async with api_client(db_session) as guest:
        response = await guest.get(path, headers=HTML_HEADERS)

    assert response.status_code == 200


async def test_trinh_duyet_bi_doi_huong_kem_next_giu_ca_query(db_session) -> None:
    """`?next=` phải mang theo **cả query string**, nếu không đăng nhập xong mất bộ lọc đang xem."""
    async with api_client(db_session) as guest:
        response = await guest.get("/cards?status=confirmed&page=2", headers=HTML_HEADERS)

    assert response.status_code == 303
    # `?` và `&` **phải** được mã hoá: không mã hoá thì `?next=/cards?status=confirmed` bị cắt ở
    # dấu `?` thứ hai và bộ lọc biến mất.
    assert response.headers["location"] == "/auth/login?next=/cards%3Fstatus%3Dconfirmed%26page%3D2"


async def test_api_nhan_401_json_chu_khong_phai_trang_html(db_session) -> None:
    """UI gọi bằng `fetch` và đọc `response.json()` — trả HTML cho nó là vỡ ngay tại chỗ."""
    async with api_client(db_session) as guest:
        response = await guest.get("/api/cards")

    assert response.status_code == 401
    assert response.json()["detail"] == security.UNAUTHENTICATED_DETAIL


async def test_dang_nhap_roi_thi_vao_duoc_va_nav_hien_ten_voi_nut_dang_xuat(
    db_session, user_a: User
) -> None:
    """Yêu cầu của 12.4 với `base.html`, kiểm qua HTML thật chứ không qua biến context."""
    async with api_client(db_session, user_a) as client:
        response = await client.get("/cards", headers=HTML_HEADERS)

    assert response.status_code == 200
    assert "Người dùng A" in response.text
    assert 'action="/auth/logout"' in response.text


async def test_cookie_bi_sua_thi_coi_nhu_chua_dang_nhap(db_session, user_a: User) -> None:
    """Chữ ký `itsdangerous` là thứ duy nhất phân biệt phiên thật với phiên bịa."""
    async with api_client(db_session, user_a) as client:
        client.cookies.set("bizcard_session", "cookie-bia-dat")
        response = await client.get("/api/cards")

    assert response.status_code == 401


async def test_tai_khoan_bi_khoa_thi_phien_cu_het_hieu_luc(db_session, user_a: User) -> None:
    """Khoá tài khoản mà phiên đang mở vẫn ghi được dữ liệu thì việc khoá chẳng có nghĩa gì."""
    async with api_client(db_session, user_a) as client:
        user_a.is_active = False
        await db_session.flush()
        response = await client.get("/api/cards")

    assert response.status_code == 401


# --------------------------------------------------------------------------- 12.5 F1 + F3


async def make_card(db_session, user: User, **overrides) -> BusinessCard:
    values = {
        "workspace_id": await workspace_id_of(db_session, user),
        "user_id": user.id,
        "image_path": f"sec/{uuid.uuid4().hex[:8]}.jpg",
        "image_hash": uuid.uuid4().hex,
        "status": CardStatus.CONFIRMED.value,
        "full_name": "Nguyễn Văn A",
        "company_name_raw": "Công ty TNHH ABC",
        "email": "a.nguyen@abc.vn",
        "language_detected": "vi",
    }
    values.update(overrides)
    card = BusinessCard(**values)
    db_session.add(card)
    await db_session.flush()
    return card


async def test_danh_sach_the_chi_thay_the_cua_minh(db_session, user_a, active_a, user_b) -> None:
    await make_card(db_session, user_a, full_name="Của A")
    await make_card(db_session, user_b, full_name="Của B")

    result = await cards_router.list_cards(db_session, user_a, active_a)

    assert [row.full_name for row in result.items] == ["Của A"]
    assert result.total == 1


async def test_the_cua_nguoi_khac_tra_404_khong_phai_403(
    db_session, user_a, active_a, user_b
) -> None:
    """403 là tự khai rằng bản ghi đó tồn tại — xem Plan.md mục 4."""
    from fastapi import HTTPException

    card_b = await make_card(db_session, user_b)

    with pytest.raises(HTTPException) as exc:
        await cards_router.get_card(card_b.id, db_session, user_a, active_a)

    assert exc.value.status_code == 404
    # Cùng **một câu** với ca id không tồn tại: khác câu chữ là còn một kênh để dò.
    with pytest.raises(HTTPException) as missing:
        await cards_router.get_card(uuid.uuid4(), db_session, user_a, active_a)
    assert exc.value.detail == missing.value.detail


async def test_upload_trung_anh_chi_tinh_trong_pham_vi_mot_khong_gian(
    db_session, user_a, workspace_a, user_b, workspace_b
) -> None:
    """Tổ chức khác upload đúng tấm ảnh này thì **được**, và không biết bên kia đã có.

    Nửa còn lại — hai người **cùng** một không gian quét trùng một thẻ thì phải bị chặn — nằm ở
    `test_isolation.py`.
    """
    from app.repositories import card as card_repo

    shared_hash = uuid.uuid4().hex
    await make_card(db_session, user_a, image_hash=shared_hash)

    assert await card_repo.get_by_hash(db_session, shared_hash, workspace_id=workspace_b) is None
    assert (
        await card_repo.get_by_hash(db_session, shared_hash, workspace_id=workspace_a) is not None
    )


async def test_truy_hoi_kb_khong_voi_sang_du_lieu_cua_khong_gian_khac(
    db_session, user_a, workspace_a, user_b, workspace_b, embedder
) -> None:
    """Đường rò khó thấy nhất của A9′: chunk của tổ chức B lọt vào ngữ cảnh trợ lý AI của A.

    Ca đầy đủ (gồm cả câu trả lời của model) nằm ở `tests/test_isolation.py`. Ở đây chặn tại tầng
    truy hồi, nơi rò bắt đầu.
    """
    from app.services import retriever

    card_b = await make_card(
        db_session, user_b, full_name="Trần Thị B", company_name_raw="Công ty CP Vận tải XYZ"
    )
    await kb.ingest_card(db_session, card_b)

    assert await kb_repo.count_chunks(db_session, workspace_id=workspace_b) >= 1
    assert await kb_repo.count_chunks(db_session, workspace_id=workspace_a) == 0

    hits_b = await retriever.search(db_session, "Vận tải XYZ", workspace_id=workspace_b)
    hits_a = await retriever.search(db_session, "Vận tải XYZ", workspace_id=workspace_a)

    assert hits_b, "tiền đề của test: chunk của B có thật và tìm được"
    assert hits_a == []


# ------------------------------------------------------------------ Swagger không lộ ra web (I-43)


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
async def test_swagger_tat_han_khi_khong_phai_may_dev(db_session, user_a: User, path: str) -> None:
    """Ba đường Swagger phải **không tồn tại** khi `DEBUG=false`, không chỉ bị chặn.

    Trên `ocrximi.io.vn` thì ai đăng ký một tài khoản cũng là "đã đăng nhập", nên cổng đăng nhập
    không còn là ranh giới đáng tin. Kiểm bằng client **đã đăng nhập** chính vì lý do đó.

    `/openapi.json` mới là cái đáng giấu nhất: tắt mỗi `/docs` thì giao diện đọc biến mất còn
    **toàn bộ lược đồ API vẫn tải về được**.
    """
    async with api_client(db_session, user_a) as client:
        response = await client.get(path, headers=HTML_HEADERS)

    assert response.status_code == 404, f"{path} vẫn mở khi DEBUG=false"


async def test_menu_tai_khoan_khong_con_lien_ket_api(db_session, user_a: User) -> None:
    """Tắt route mà để nguyên liên kết là dựng một mục dẫn tới 404 trong menu của mọi người."""
    async with api_client(db_session, user_a) as client:
        html = (await client.get("/cards", headers=HTML_HEADERS)).text

    assert 'href="/docs"' not in html
