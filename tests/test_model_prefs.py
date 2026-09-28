"""Chọn model theo từng chức năng: lưu, phân giải, lọc danh sách, và hai đường rơi về mặc định.

Chủ sở hữu: Q | Task: EX-16 | xem `docs/adr-model-per-feature.md`

Vì sao có file này: từ `EX-14`, **tên model không còn là một hằng số** mà là kết quả của
`lựa chọn của người dùng × bảng năng lực × danh mục thật lúc chạy`. Ba thứ đó sai lệch nhau theo
những cách **không ném lỗi nào**:

1. Chọn cho `enrich` một model không tra cứu được Internet → hồ sơ vẫn sinh, chỉ là **trống**
   (mọi trường không nguồn bị `enrichment.py` bỏ) → trượt tiêu chí **A5** mà không ai thấy.
2. Chọn cho `ocr` một model không đọc được ảnh → thẻ quét ra rỗng, `gpt-oss-120b-medium` thậm chí
   trả `{"full_name": "none"}` (đo ở `EX-12`).
3. Model đã lưu **biến mất khỏi danh mục** — danh mục tự đổi, đã đổi 11 → 12 trong 14 ngày.

Và một đường rò của **A9**: lựa chọn của A không được rơi sang B.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.user import User
from app.repositories import model_pref as pref_repo
from app.services import model_catalog, user_credentials
from app.services.cliproxy_client import AuthFile
from tests.conftest import CliProxyStub, make_user
from tests.test_card_api import make_card

pytestmark = pytest.mark.anyio

ClientFactory = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]

#: Đo ở `EX-12`: đọc được ảnh, tra cứu Internet có nguồn. Dùng làm model "tốt mọi mặt".
GOOD = "gemini-3-flash"
#: Đo ở `EX-12`: model **duy nhất** không đọc nổi ảnh, và cũng không có `groundingChunks`.
NO_VISION = "gpt-oss-120b-medium"
#: Đọc được ảnh nhưng **không** tra cứu Internet — đúng cái bẫy im lặng của `enrich`.
NO_WEB = "claude-sonnet-4-6"

CATALOGUE = [GOOD, NO_VISION, NO_WEB, "gemini-3.6-flash-high"]


@pytest.fixture(autouse=True)
def _fresh_catalogue_cache() -> None:
    """Danh mục được nhớ trong tiến trình 5 phút — test nào cũng phải bắt đầu từ trang giấy trắng."""
    model_catalog.forget_catalogue()


def _stub_catalogue(monkeypatch: pytest.MonkeyPatch, models: list[str]) -> None:
    async def fake(client: object = None) -> list[str]:
        return models

    monkeypatch.setattr(model_catalog, "catalogue", fake)


# --------------------------------------------------------------------------- bảng năng lực


def test_bang_nang_luc_loc_dung_tung_chuc_nang() -> None:
    """Ba chức năng, ba danh sách khác nhau — đây là toàn bộ lý do `EX-12` phải đo thật."""
    assert model_catalog.allowed_for("ocr", CATALOGUE) == [GOOD, NO_WEB, "gemini-3.6-flash-high"]
    assert model_catalog.allowed_for("enrich", CATALOGUE) == [GOOD, "gemini-3.6-flash-high"]
    assert model_catalog.allowed_for("chat", CATALOGUE) == CATALOGUE


def test_danh_sach_luon_giao_voi_danh_muc_that() -> None:
    """Bảng năng lực là ảnh chụp một phép đo; danh mục mới là sự thật lúc chạy (ADR M4).

    Model đã bị channel gỡ mà vẫn hiện trong ô chọn là mời người dùng chọn một thứ chắc chắn hỏng.
    """
    assert model_catalog.allowed_for("enrich", ["gemini-3-flash"]) == ["gemini-3-flash"]
    assert model_catalog.allowed_for("enrich", []) == []


# --------------------------------------------------------------------------- phân giải


async def test_chua_chon_gi_thi_dung_mac_dinh(db_session: AsyncSession, user_a: User) -> None:
    for feature in model_catalog.FEATURES:
        assert await model_catalog.resolve(db_session, user_a.id, feature) == settings.llm_model


async def test_moi_chuc_nang_di_bang_model_cua_chinh_no(
    db_session: AsyncSession, user_a: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_catalogue(monkeypatch, CATALOGUE)
    await pref_repo.save(db_session, user_a.id, {"ocr": NO_WEB, "enrich": GOOD, "chat": NO_VISION})
    await db_session.flush()

    assert await model_catalog.resolve(db_session, user_a.id, "ocr") == NO_WEB
    assert await model_catalog.resolve(db_session, user_a.id, "enrich") == GOOD
    assert await model_catalog.resolve(db_session, user_a.id, "chat") == NO_VISION


async def test_model_khong_du_nang_luc_thi_roi_ve_mac_dinh(
    db_session: AsyncSession, user_a: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR M5: rơi về mặc định **kèm cảnh báo**, không ném lỗi.

    Người dùng không làm gì sai — bảng năng lực có thể đổi sau một lượt đo mới, hoặc dòng này đã
    nằm trong DB từ trước khi ai đó đo ra rằng model đó không đọc được ảnh. Ném lỗi ở đây là làm
    hỏng lượt quét vì một chuyện của quá khứ.
    """
    _stub_catalogue(monkeypatch, CATALOGUE)
    # Ghi thẳng qua repository: API sẽ từ chối tổ hợp này, mà đúng cái ta cần dựng là trạng thái
    # DB "đã lỡ có" — nó có thật, vì bảng năng lực và dữ liệu cũ không sinh ra cùng lúc.
    await pref_repo.save(db_session, user_a.id, {"ocr": NO_VISION, "enrich": NO_WEB})
    await db_session.flush()

    assert await model_catalog.resolve(db_session, user_a.id, "ocr") == settings.llm_model
    assert await model_catalog.resolve(db_session, user_a.id, "enrich") == settings.llm_model


async def test_model_bien_mat_khoi_danh_muc_thi_roi_ve_mac_dinh(
    db_session: AsyncSession, user_a: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Danh mục tự đổi (11 → 12 model trong 14 ngày) — đây là ca **sẽ** xảy ra thật."""
    await pref_repo.save(db_session, user_a.id, {"chat": GOOD})
    await db_session.flush()

    _stub_catalogue(monkeypatch, [NO_WEB])
    assert await model_catalog.resolve(db_session, user_a.id, "chat") == settings.llm_model


async def test_danh_muc_rong_thi_van_tin_lua_chon_cua_nguoi_dung(
    db_session: AsyncSession, user_a: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Danh mục rỗng = **không hỏi được CLIProxy**, không phải "model đã bị gỡ".

    Đổi model của người dùng vì ta đang mất mạng là một kiểu hỏng khác, âm thầm hơn hẳn: họ chọn
    A, hệ thống lặng lẽ chạy B, và mọi thứ vẫn "hoạt động bình thường".
    """
    await pref_repo.save(db_session, user_a.id, {"chat": GOOD})
    await db_session.flush()

    _stub_catalogue(monkeypatch, [])
    assert await model_catalog.resolve(db_session, user_a.id, "chat") == GOOD


# --------------------------------------------------------------------------- lưu trữ


async def test_khong_dung_dong_rong_trong_db(db_session: AsyncSession, user_a: User) -> None:
    """Chọn rồi bỏ thì xoá hẳn dòng — dòng toàn `NULL` là rác mang một nghĩa không ai dùng."""
    await pref_repo.save(db_session, user_a.id, {"ocr": None})
    await db_session.flush()
    assert await pref_repo.get(db_session, user_a.id) is None

    await pref_repo.save(db_session, user_a.id, {"ocr": GOOD})
    await db_session.flush()
    assert await pref_repo.get(db_session, user_a.id) is not None

    await pref_repo.save(db_session, user_a.id, {"ocr": None})
    await db_session.flush()
    assert await pref_repo.get(db_session, user_a.id) is None


async def test_chuc_nang_khong_nhac_toi_thi_giu_nguyen(
    db_session: AsyncSession, user_a: User
) -> None:
    await pref_repo.save(db_session, user_a.id, {"ocr": GOOD, "chat": NO_WEB})
    await db_session.flush()
    await pref_repo.save(db_session, user_a.id, {"chat": GOOD})
    await db_session.flush()

    assert await pref_repo.as_dict(db_session, user_a.id) == {
        "ocr": GOOD,
        "enrich": None,
        "chat": GOOD,
    }


# --------------------------------------------------------------------------- API


async def test_get_tra_ba_chuc_nang_da_loc(
    app_client: ClientFactory, user_a: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_catalogue(monkeypatch, CATALOGUE)
    async with app_client(user_a) as http:
        body = (await http.get("/api/integration/models")).json()

    assert body["default_model"] == settings.llm_model
    assert body["reachable"] is True
    by_key = {f["key"]: f for f in body["features"]}
    assert set(by_key) == {"ocr", "enrich", "chat"}
    assert NO_VISION not in by_key["ocr"]["available"]
    assert by_key["enrich"]["available"] == [GOOD, "gemini-3.6-flash-high"]
    assert all(f["selected"] is None for f in body["features"])


async def test_put_luu_roi_doc_lai_duoc(
    app_client: ClientFactory, user_a: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_catalogue(monkeypatch, CATALOGUE)
    async with app_client(user_a) as http:
        saved = await http.put("/api/integration/models", json={"ocr": NO_WEB})
        read = await http.get("/api/integration/models")

    assert saved.status_code == 200
    for body in (saved.json(), read.json()):
        by_key = {f["key"]: f for f in body["features"]}
        assert by_key["ocr"]["selected"] == NO_WEB
        assert by_key["ocr"]["selected_available"] is True
        assert by_key["enrich"]["selected"] is None


async def test_put_tu_choi_model_khong_du_nang_luc(
    app_client: ClientFactory, user_a: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chỗ **duy nhất** báo được cho người dùng bằng thứ họ hiểu — sau đó mọi thứ đều im lặng."""
    _stub_catalogue(monkeypatch, CATALOGUE)
    async with app_client(user_a) as http:
        anh = await http.put("/api/integration/models", json={"ocr": NO_VISION})
        web = await http.put("/api/integration/models", json={"enrich": NO_WEB})
        la = await http.put("/api/integration/models", json={"chat": "model-khong-co-that"})
        body = (await http.get("/api/integration/models")).json()

    assert (anh.status_code, web.status_code, la.status_code) == (422, 422, 422)
    assert "Lập hồ sơ" in web.json()["detail"]
    # Từ chối là **không lưu gì cả**, không phải lưu rồi báo.
    assert all(f["selected"] is None for f in body["features"])


async def test_put_null_la_ve_mac_dinh(
    app_client: ClientFactory, user_a: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_catalogue(monkeypatch, CATALOGUE)
    async with app_client(user_a) as http:
        await http.put("/api/integration/models", json={"chat": GOOD})
        cleared = await http.put("/api/integration/models", json={"chat": None})

    by_key = {f["key"]: f for f in cleared.json()["features"]}
    assert by_key["chat"]["selected"] is None


async def test_khong_lay_duoc_danh_muc_thi_tu_choi_luu(
    app_client: ClientFactory, user_a: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Không có gì để đối chiếu thì lưu gì cũng là lưu mò — mà thứ lưu mò sẽ hỏng âm thầm."""
    _stub_catalogue(monkeypatch, [])
    async with app_client(user_a) as http:
        response = await http.put("/api/integration/models", json={"chat": GOOD})
        body = (await http.get("/api/integration/models")).json()

    assert response.status_code == 503
    assert body["reachable"] is False


async def test_model_da_luu_bien_mat_thi_giao_dien_phai_noi_ra(
    app_client: ClientFactory, user_a: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lời gọi rơi về mặc định trong im lặng (M5) — nhưng trang Cài đặt thì không được im lặng."""
    _stub_catalogue(monkeypatch, CATALOGUE)
    async with app_client(user_a) as http:
        await http.put("/api/integration/models", json={"chat": GOOD})
        _stub_catalogue(monkeypatch, [NO_WEB])
        body = (await http.get("/api/integration/models")).json()

    by_key = {f["key"]: f for f in body["features"]}
    assert by_key["chat"]["selected"] == GOOD
    assert by_key["chat"]["selected_available"] is False


async def test_chua_dang_nhap_thi_khong_doc_duoc(app_client: ClientFactory) -> None:
    async with app_client() as http:
        assert (await http.get("/api/integration/models")).status_code == 401


# --------------------------------------------------------------------------- tách theo người dùng


async def test_lua_chon_cua_a_khong_roi_sang_b(
    app_client: ClientFactory, user_a: User, user_b: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tiêu chí **A9**. Cùng một bảng, khoá chính là `user_id` — đúng chỗ dễ quên `WHERE`."""
    _stub_catalogue(monkeypatch, CATALOGUE)
    async with app_client(user_a) as http:
        await http.put("/api/integration/models", json={"chat": NO_WEB})
    async with app_client(user_b) as http:
        body = (await http.get("/api/integration/models")).json()

    by_key = {f["key"]: f for f in body["features"]}
    assert by_key["chat"]["selected"] is None


async def test_b_khong_sua_duoc_lua_chon_cua_a(
    db_session: AsyncSession,
    app_client: ClientFactory,
    user_a: User,
    user_b: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_catalogue(monkeypatch, CATALOGUE)
    async with app_client(user_a) as http:
        await http.put("/api/integration/models", json={"chat": NO_WEB})
    async with app_client(user_b) as http:
        await http.put("/api/integration/models", json={"chat": GOOD})

    assert (await pref_repo.as_dict(db_session, user_a.id))["chat"] == NO_WEB
    assert (await pref_repo.as_dict(db_session, user_b.id))["chat"] == GOOD


# --------------------------------------------------------------------------- đầu–cuối


def _generate_paths(stub: CliProxyStub) -> list[str]:
    return [
        call.request.url.path
        for call in stub.calls
        if call.request.url.path.endswith(":generateContent")
    ]


async def test_loi_goi_that_di_dung_model_da_chon(
    db_session: AsyncSession,
    app_client: ClientFactory,
    cliproxy: CliProxyStub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phép kiểm cuối cùng: đường `/v1beta/models/<tiền tố>/<model đã chọn>:generateContent`.

    Hai nửa của tên model đến từ hai nơi và **cả hai đều phải đúng**: tiền tố nói lời gọi đi bằng
    credential của ai (13.2), tên model nói model nào trả lời (`EX-14`). Test khẳng định cả tên
    đầy đủ chứ không khẳng định riêng từng nửa — ghép sai thứ tự thì hai phép kiểm riêng vẫn xanh.

    Đi qua nút *Dịch lại* (`POST /api/cards/{id}/translate`) vì đó là lời gọi model **ngắn nhất**
    chạm tới `model_for(..., "ocr")`: không cần ảnh thật, không cần KB, không cần embedder — ba
    thứ chỉ làm test dài ra mà không kiểm thêm được gì về việc chọn model.
    """
    _stub_catalogue(monkeypatch, CATALOGUE)
    erin = await make_user(db_session, "erin@example.com")
    card = await make_card(db_session, erin)
    cliproxy.reply('{"full_name": "Le Thi C"}')

    async with app_client(erin) as http:
        await http.put("/api/integration/models", json={"ocr": NO_WEB})
        response = await http.post(f"/api/cards/{card.id}/translate")

    assert response.status_code == 200
    prefix = user_credentials.credential_prefix(erin.id)
    assert _generate_paths(cliproxy) == [f"/v1beta/models/{prefix}/{NO_WEB}:generateContent"]


async def test_lua_chon_ocr_dung_chung_cho_buoc_viet_hoa(
    db_session: AsyncSession,
    app_client: ClientFactory,
    cliproxy: CliProxyStub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """QĐ-5 / ADR M3: Việt hoá **không** có ô chọn riêng, nó đi theo ô *Quét danh thiếp*.

    Và `translation_meta.model` phải khai đúng model vừa gọi — trước `EX-14` chỗ đó đọc thẳng
    `settings.llm_model`, tức sổ sách nói dối ngay khi hai thứ khác nhau (**I-34**).
    """
    _stub_catalogue(monkeypatch, CATALOGUE)
    gina = await make_user(db_session, "gina@example.com")
    card = await make_card(db_session, gina)
    cliproxy.reply('{"job_title_vi": "Trưởng phòng kinh doanh"}')

    async with app_client(gina) as http:
        await http.put("/api/integration/models", json={"ocr": NO_WEB})
        body = (await http.post(f"/api/cards/{card.id}/translate")).json()

    assert body["translation_meta"]["model"] == NO_WEB


async def test_xoa_tai_khoan_thi_lua_chon_di_theo(db_session: AsyncSession) -> None:
    """`ON DELETE CASCADE` — lựa chọn model chỉ có nghĩa khi gắn với credential của chính người đó."""
    frank = await make_user(db_session, "frank@example.com")
    frank_id: uuid.UUID = frank.id
    await pref_repo.save(db_session, frank_id, {"ocr": GOOD})
    await db_session.flush()

    await db_session.delete(frank)
    await db_session.flush()
    assert await pref_repo.get(db_session, frank_id) is None


# ------------------------------------------------- cooldown của credential (I-42)


def _auth_file(name: str, *, label: str, cooldowns: list[dict[str, object]]) -> AuthFile:
    """Một credential như CLIProxy trả về, kèm `cooldowns` đúng hình dạng đo được trên prod."""
    return AuthFile.from_payload(
        {
            "name": name,
            "provider": "antigravity",
            "label": label,
            "status": "active",
            "disabled": False,
            "unavailable": False,
            "cooldowns": cooldowns,
        }
    )


COOLDOWN: list[dict[str, object]] = [
    {
        "scope": "model",
        "model_key": GOOD,
        "reason": "payment_required",
        "retry_at": "2026-09-28T05:58:55Z",
        "remaining_seconds": 267,
        "http_status": 403,
    }
]


def test_cooldown_khong_lam_credential_thanh_hong() -> None:
    """Gốc của I-42: badge đọc `usable`, mà `usable` không nhìn `cooldowns`.

    Giữ nguyên hành vi ấy **có chủ đích** — credential đang nghỉ vẫn lành lặn, model khác vẫn
    gọi được. Nhưng phải có test ghim lại, vì cám dỗ "cho cooldown vào `usable` cho badge đỏ"
    rất lớn, mà làm thế là đẩy người dùng đi đăng nhập lại để chữa một thứ đăng nhập lại không
    đụng tới được.
    """
    assert _auth_file("a.json", label="ai@gmail.com", cooldowns=COOLDOWN).usable is True


def test_cooldown_khop_ca_khi_ten_model_con_tien_to() -> None:
    """Lời gọi đi bằng `u<hex>/gemini-3-flash`, CLIProxy ghi cooldown theo `gemini-3-flash`.

    Không cắt tiền tố thì không bao giờ khớp, và `llm._explain_no_credential()` sẽ bỏ sót đúng
    cái lý do nó sinh ra để tìm.
    """
    auth_file = _auth_file("a.json", label="ai@gmail.com", cooldowns=COOLDOWN)

    assert auth_file.cooldown_for("uabc123456789/" + GOOD) is not None
    assert auth_file.cooldown_for(GOOD) is not None
    assert auth_file.cooldown_for(NO_WEB) is None
