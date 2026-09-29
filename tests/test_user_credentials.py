import json
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.user import User
from app.services import llm, user_credentials
from app.services.cliproxy_client import AuthFile
from app.services.user_credentials import credential_prefix, model_for, pick_new_file
from tests.conftest import CliProxyStub, gemini_payload, make_user

ClientFactory = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]

PROVIDER = settings.cliproxy_auth_provider


def auth_file(name: str, modtime: str, *, disabled: bool = False) -> AuthFile:
    return AuthFile.from_payload(
        {
            "name": name,
            "provider": PROVIDER,
            "label": name.removeprefix(f"{PROVIDER}-").removesuffix(".json"),
            "status": "active",
            "disabled": disabled,
            "modtime": modtime,
        }
    )


@dataclass
class FakeProxy:
    stub: CliProxyStub
    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    prefixes: dict[str, str] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)
    oauth: str = "wait"
    clock: int = 0

    def __post_init__(self) -> None:
        router = self.stub.router
        router.get("/v0/management/auth-files").mock(side_effect=self._list)
        router.get(f"/v0/management/{PROVIDER}-auth-url").mock(side_effect=self._start)
        router.get("/v0/management/get-auth-status").mock(side_effect=self._status)
        router.patch("/v0/management/auth-files/fields").mock(side_effect=self._patch)
        router.delete("/v0/management/auth-files").mock(side_effect=self._delete)
        router.get(f"/v0/management/model-definitions/{PROVIDER}").mock(
            return_value=httpx.Response(200, json={"models": [{"id": settings.llm_model}]})
        )

    def login(self, email: str) -> str:
        self.clock += 1
        name = f"{PROVIDER}-{email}.json"
        self.files[name] = {
            "name": name,
            "provider": PROVIDER,
            "label": email,
            "status": "active",
            "disabled": False,
            "modtime": f"2026-09-22T10:00:{self.clock:02d}Z",
            # `None` chứ không bỏ trống khoá: CLIProxy thật **luôn** khai `project_id` trong
            # `auth-files`, để `null` khi Google không cấp (đo 2026-09-29, I-45). Bỏ khoá đi thì
            # fake dựng ra một hình dạng không tồn tại, và test hoá thành kiểm chính nó.
            "project_id": None,
        }
        self.prefixes.pop(name, None)
        self.oauth = "ok"
        return name

    def _list(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"files": list(self.files.values())})

    def _start(self, request: httpx.Request) -> httpx.Response:
        self.oauth = "wait"
        state = uuid.uuid4().hex
        return httpx.Response(
            200, json={"status": "ok", "url": "https://accounts.test/", "state": state}
        )

    def _status(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": self.oauth})

    def _patch(self, request: httpx.Request) -> httpx.Response:
        """`PATCH /auth-files/fields` nhận **một khoá mỗi lượt**: `prefix` hoặc `project_id`.

        Ghi thẳng vào `files` cho `project_id` (I-45) vì lần đọc `auth-files` sau đó phải thấy
        giá trị mới — đúng như CLIProxy thật: nó ghi vào file rồi nạp lại.
        """
        body = json.loads(request.content)
        name = body["name"]
        if "prefix" in body:
            self.prefixes[name] = body["prefix"]
        if "project_id" in body:
            self.files[name]["project_id"] = body["project_id"]
        return httpx.Response(200, json={"status": "ok"})

    def _delete(self, request: httpx.Request) -> httpx.Response:
        name = request.url.params["name"]
        if self.files.pop(name, None) is None:
            return httpx.Response(404, json={"error": "auth file not found"})
        self.deleted.append(name)
        return httpx.Response(200, json={"status": "ok"})


@pytest.fixture
def proxy(cliproxy: CliProxyStub) -> FakeProxy:
    return FakeProxy(cliproxy)


@pytest.fixture
async def alice(db_session: AsyncSession) -> User:
    return await make_user(db_session, "alice@example.com", connected=False)


@pytest.fixture
async def bob(db_session: AsyncSession) -> User:
    return await make_user(db_session, "bob@example.com", connected=False)


async def connect(http: httpx.AsyncClient, proxy: FakeProxy, email: str) -> httpx.Response:
    started = await http.post("/api/integration/connect")
    assert started.status_code == 200
    state = started.json()["state"]
    assert (await http.get("/api/integration/oauth-status", params={"state": state})).json()[
        "status"
    ] == "wait"
    proxy.login(email)
    return await http.get("/api/integration/oauth-status", params={"state": state})


def test_prefix_is_stable_and_distinct() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    assert credential_prefix(first) == credential_prefix(first)
    assert credential_prefix(first) != credential_prefix(second)
    assert credential_prefix(first).startswith("u")


def test_default_model_for_requires_own_credential() -> None:
    """Nút *Kiểm tra kết nối* — model mặc định, **không** tra lựa chọn của người dùng (EX-14)."""
    user = User(id=uuid.uuid4(), email="x@example.com", password_hash="!")
    with pytest.raises(llm.LLMNotConnectedError):
        user_credentials.default_model_for(user)
    user.cliproxy_auth_file = "antigravity-x@example.com.json"
    name = user_credentials.default_model_for(user)
    assert name == f"{credential_prefix(user.id)}/{settings.llm_model}"
    assert llm.base_model(name) == settings.llm_model


async def test_model_for_requires_own_credential(db_session: AsyncSession) -> None:
    user = User(id=uuid.uuid4(), email="x@example.com", password_hash="!")
    with pytest.raises(llm.LLMNotConnectedError):
        await model_for(db_session, user, "ocr")
    user.cliproxy_auth_file = "antigravity-x@example.com.json"
    name = await model_for(db_session, user, "ocr")
    assert name == f"{credential_prefix(user.id)}/{settings.llm_model}"
    assert llm.base_model(name) == settings.llm_model


def test_pick_new_file_prefers_the_file_this_login_wrote() -> None:
    old = auth_file("antigravity-old@x.json", "2026-09-22T09:00:00Z")
    fresh = auth_file("antigravity-new@x.json", "2026-09-22T10:00:00Z")
    rewritten = auth_file("antigravity-old@x.json", "2026-09-22T10:05:00Z")
    broken = auth_file("antigravity-bad@x.json", "2026-09-22T11:00:00Z", disabled=True)
    before = {old.name: "2026-09-22T09:00:00Z"}

    assert pick_new_file([old, fresh, broken], before) == fresh
    assert pick_new_file([rewritten], before) == rewritten
    assert pick_new_file([old], before) is None
    assert pick_new_file([old, fresh], None) == fresh


async def test_connect_claims_and_prefixes_the_new_credential(
    db_session: AsyncSession, app_client: ClientFactory, proxy: FakeProxy, alice: User
) -> None:
    async with app_client(alice) as http:
        done = await connect(http, proxy, "alice.google@gmail.com")
        status = (await http.get("/api/integration/status")).json()

    assert done.json() == {"status": "ok", "error": None}
    name = f"{PROVIDER}-alice.google@gmail.com.json"
    assert alice.cliproxy_auth_file == name
    assert proxy.prefixes == {name: credential_prefix(alice.id)}
    assert (status["connected"], status["accounts"]) == (True, ["alice.google@gmail.com"])


async def test_badge_shows_only_own_credential(
    app_client: ClientFactory, proxy: FakeProxy, alice: User, bob: User
) -> None:
    async with app_client(alice) as http:
        await connect(http, proxy, "alice.google@gmail.com")
    async with app_client(bob) as http:
        before = (await http.get("/api/integration/status")).json()
        await connect(http, proxy, "bob.google@gmail.com")
        after = (await http.get("/api/integration/status")).json()

    assert (before["connected"], before["accounts"]) == (False, [])
    assert after["accounts"] == ["bob.google@gmail.com"]


async def test_disconnect_removes_only_own_credential(
    db_session: AsyncSession, app_client: ClientFactory, proxy: FakeProxy, alice: User, bob: User
) -> None:
    async with app_client(alice) as http:
        await connect(http, proxy, "alice.google@gmail.com")
    async with app_client(bob) as http:
        await connect(http, proxy, "bob.google@gmail.com")

    async with app_client(alice) as http:
        response = await http.post("/api/integration/disconnect")
        alice_status = (await http.get("/api/integration/status")).json()
    async with app_client(bob) as http:
        bob_status = (await http.get("/api/integration/status")).json()

    alice_file = f"{PROVIDER}-alice.google@gmail.com.json"
    assert response.json()["removed"] == [alice_file]
    assert proxy.deleted == [alice_file]
    assert alice.cliproxy_auth_file is None
    assert alice_status["connected"] is False
    assert (bob_status["connected"], bob_status["accounts"]) == (True, ["bob.google@gmail.com"])


async def test_same_google_account_cannot_serve_two_users(
    app_client: ClientFactory, proxy: FakeProxy, alice: User, bob: User
) -> None:
    async with app_client(alice) as http:
        await connect(http, proxy, "shared@gmail.com")
    async with app_client(bob) as http:
        done = await connect(http, proxy, "shared@gmail.com")

    name = f"{PROVIDER}-shared@gmail.com.json"
    assert done.json()["status"] == "error"
    assert "người dùng khác" in done.json()["error"]
    assert bob.cliproxy_auth_file is None
    assert alice.cliproxy_auth_file == name
    assert proxy.prefixes[name] == credential_prefix(alice.id)


async def test_reconnecting_with_another_account_drops_the_old_one(
    app_client: ClientFactory, proxy: FakeProxy, alice: User
) -> None:
    async with app_client(alice) as http:
        await connect(http, proxy, "first@gmail.com")
        await connect(http, proxy, "second@gmail.com")

    assert alice.cliproxy_auth_file == f"{PROVIDER}-second@gmail.com.json"
    assert proxy.deleted == [f"{PROVIDER}-first@gmail.com.json"]


async def test_oauth_session_of_another_user_is_404(
    app_client: ClientFactory, proxy: FakeProxy, alice: User, bob: User
) -> None:
    async with app_client(alice) as http:
        state = (await http.post("/api/integration/connect")).json()["state"]
    async with app_client(bob) as http:
        polled = await http.get("/api/integration/oauth-status", params={"state": state})
        cancelled = await http.delete("/api/integration/oauth-session", params={"state": state})
    assert (polled.status_code, cancelled.status_code) == (404, 404)


def generate_paths(stub: CliProxyStub) -> list[str]:
    return [
        call.request.url.path
        for call in stub.calls
        if call.request.url.path.endswith(":generateContent")
    ]


async def test_llm_calls_carry_the_callers_prefix(
    db_session: AsyncSession, app_client: ClientFactory, cliproxy: CliProxyStub
) -> None:
    carol = await make_user(db_session, "carol@example.com")
    dave = await make_user(db_session, "dave@example.com")
    cliproxy.router.post(path__regex=r"^/v1beta/models/[^:]+:generateContent$").mock(
        return_value=httpx.Response(200, json=gemini_payload("Đang hoạt động."))
    )

    async with app_client(carol) as http:
        assert (await http.post("/api/integration/test")).json()["ok"] is True
    async with app_client(dave) as http:
        assert (await http.post("/api/integration/test")).json()["ok"] is True

    assert generate_paths(cliproxy) == [
        f"/v1beta/models/{credential_prefix(carol.id)}/{settings.llm_model}:generateContent",
        f"/v1beta/models/{credential_prefix(dave.id)}/{settings.llm_model}:generateContent",
    ]


async def test_unconnected_user_never_reaches_the_model(
    app_client: ClientFactory, cliproxy: CliProxyStub, alice: User
) -> None:
    cliproxy.reply("KHÔNG ĐƯỢC GỌI")
    async with app_client(alice) as http:
        body = (await http.post("/api/integration/test")).json()

    assert body["ok"] is False
    assert body["detail"] == user_credentials.NOT_CONNECTED
    assert generate_paths(cliproxy) == []


async def test_background_work_resolves_the_owner_model(
    db_session: AsyncSession, alice: User
) -> None:
    with pytest.raises(llm.LLMNotConnectedError):
        await user_credentials.model_for_user_id(db_session, alice.id, "chat")
    with pytest.raises(llm.LLMNotConnectedError):
        await user_credentials.model_for_user_id(db_session, uuid.uuid4(), "chat")
    alice.cliproxy_auth_file = "antigravity-alice@x.json"
    await db_session.flush()
    resolved = await user_credentials.model_for_user_id(db_session, alice.id, "chat")
    assert resolved == await model_for(db_session, alice, "chat")


async def test_prefixed_model_missing_credential_is_reported_as_not_connected(
    cliproxy: CliProxyStub,
) -> None:
    cliproxy.fail(400, {"error": "unknown provider for model u123/gemini-3-flash"})
    cliproxy.router.get(f"/v0/management/model-definitions/{PROVIDER}").mock(
        return_value=httpx.Response(200, json={"models": [{"id": settings.llm_model}]})
    )
    with pytest.raises(llm.LLMNotConnectedError):
        await llm.generate_text("xin chào", model=f"u123/{settings.llm_model}")


async def test_scan_and_translate_both_use_the_owner_prefix(cliproxy: CliProxyStub) -> None:
    from app.services import ocr
    from tests.test_translate import FAKE_JPEG, JP_TRANSLATION, OCR_JSON

    owner = uuid.uuid4()
    model = f"{credential_prefix(owner)}/{settings.llm_model}"
    cliproxy.reply(json.dumps(OCR_JSON), json.dumps(JP_TRANSLATION))

    result = await ocr.extract_and_translate(FAKE_JPEG, model=model)

    assert result.card_fields()["full_name_vi"] == "Tanaka Taro"
    assert generate_paths(cliproxy) == [f"/v1beta/models/{model}:generateContent"] * 2


# --------------------------------------------------- project_id của credential (I-45)


def test_thieu_project_id_thi_credential_khong_dung_duoc() -> None:
    """Gốc của I-45: credential thiếu `project_id` → **mọi** lời gọi trả `400`, mà badge vẫn xanh.

    Trước I-45 `usable` chỉ xét `disabled`/`unavailable`, nên TS-02 ghi nhận từ 2026-09-18 rằng
    badge báo *Đã kết nối* trong khi không gọi được gì. Nay `usable` biết chuyện đó.
    """
    thieu = AuthFile.from_payload({"name": "a.json", "status": "active", "project_id": None})
    co = AuthFile.from_payload({"name": "b.json", "status": "active", "project_id": "p-123"})

    assert thieu.missing_project_id is True
    assert thieu.usable is False
    assert co.missing_project_id is False
    assert co.usable is True


def test_vang_khoa_project_id_cung_la_thieu() -> None:
    """CLIProxy **bỏ hẳn khoá** khi Google không cấp project, chứ không để `null`.

    Bản đầu của I-45 coi *vắng khoá* là "không biết gì" nên bản vá **không bao giờ chạy** và lỗi
    y nguyên. Cái sai đến từ phép đo: `raw.get()` trả `None` cho cả *vắng khoá* lẫn *khoá rỗng*,
    nhìn vào thì tưởng CLIProxy luôn khai trường ấy. Ca này ghim lại hình dạng thật:

        chidientu75@gmail.com   "project_id" in raw → False
        quanpyke1@gmail.com     "project_id" in raw → True
    """
    vang_khoa = AuthFile.from_payload({"name": "a.json", "status": "active"})

    assert "project_id" not in vang_khoa.raw
    assert vang_khoa.missing_project_id is True
    assert vang_khoa.usable is False
    # Vẫn chọn được lúc `claim()` — đó là việc của `alive`, và là lý do hai thuộc tính này tách nhau.
    assert vang_khoa.alive is True


def test_credential_moi_thieu_project_id_van_chon_duoc() -> None:
    """`claim()` phải chọn được credential vừa OAuth xong, rồi mới vá `project_id` cho nó.

    Lọc bằng `usable` ở bước chọn là tự khoá mình: credential mới tinh thường **chưa** có
    `project_id`, nên không cái nào lọt qua và người dùng không kết nối nổi dù luồng OAuth vừa
    chạy hoàn hảo. Đó là lý do có `alive` tách khỏi `usable`.
    """
    moi = AuthFile.from_payload({"name": "a.json", "status": "active", "project_id": None})

    assert moi.alive is True
    assert user_credentials.pick_new_file([moi], None) is moi


async def test_ket_noi_tu_gan_project_id_cho_credential_moi(
    app_client: ClientFactory, proxy: FakeProxy, alice: User
) -> None:
    """Đường đi thật của I-45: đăng nhập xong là credential dùng được ngay.

    Từ ~2026-09-29 Google trả `UNSUPPORTED_CLIENT` cho `free-tier` của OAuth client Antigravity,
    nên `loadCodeAssist` **không còn cấp project** cho tài khoản mới đăng nhập. Không vá thì
    người dùng đi hết luồng OAuth của 13.7 — mở tab Google, đồng ý, chép URL về dán — để nhận
    về một câu `400` ở lần quét đầu tiên.

    Kiểm qua HTTP chứ không gọi thẳng `claim()`: chỗ dễ hỏng là **thứ tự** trong luồng kết nối,
    và chỉ đi trọn đường mới thấy.
    """
    async with app_client(alice) as http:
        await connect(http, proxy, "moi@gmail.com")
        status = (await http.get("/api/integration/status")).json()

    name = f"{PROVIDER}-moi@gmail.com.json"
    assert proxy.files[name]["project_id"] == settings.cliproxy_project_id
    # Vá xong thì badge phải xanh ngay — đó mới là điều người dùng thấy.
    assert status["connected"] is True
