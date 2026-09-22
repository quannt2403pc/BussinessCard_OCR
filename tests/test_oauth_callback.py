"""Lối hoàn tất OAuth bằng cách dán URL callback — task 13.7, gỡ I-29.

Chủ sở hữu: Q | xem Task.md

Vì sao có lối này: Google luôn trả trình duyệt về `http://localhost:51121/oauth-callback`.
Giá trị đó **ghi cứng trong mã nguồn CLIProxy** (`auth_files_provider_oauth.go:359` dựng từ
`antigravity.CallbackPort = 51121`), không có khoá cấu hình nào đổi được, và cũng không thể
đổi sang `oauth.ocrximi.io.vn` vì `redirect_uri` phải khớp cái đã đăng ký cho client OAuth của
Antigravity — client đó là của Google, ta không sửa được danh sách của nó.

Chạy ở localhost thì máy chạy trình duyệt **cũng là** máy chạy CLIProxy nên callback tự về.
Mở từ tên miền thật thì `localhost` là máy của *người dùng*: callback rơi vào khoảng không và
`get-auth-status` không bao giờ chuyển khỏi `wait`. Bộ test này khoá đúng hành vi đó lại.
"""

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
from app.services.user_credentials import credential_prefix
from tests.conftest import CliProxyStub, make_user

ClientFactory = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]

PROVIDER = settings.cliproxy_auth_provider
MGMT = "/v0/management"


@dataclass
class CallbackProxy:
    """CLIProxy giả lập ở đúng mức chi tiết mà task này cần.

    Khác `FakeProxy` của `test_user_credentials.py` ở một điểm quyết định: phiên OAuth **chỉ**
    chuyển sang `ok` khi ai đó nộp callback. Đây chính là tình huống trên máy chủ — không nộp
    thì poll `wait` mãi cho tới lúc hết giờ.
    """

    stub: CliProxyStub
    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    prefixes: dict[str, str] = field(default_factory=dict)
    #: state đang chờ -> email sẽ đăng nhập khi callback được nộp
    pending: dict[str, str] = field(default_factory=dict)
    done: set[str] = field(default_factory=set)
    next_email: str = "alice.google@gmail.com"
    callbacks: list[dict[str, Any]] = field(default_factory=list)
    clock: int = 0

    def __post_init__(self) -> None:
        router = self.stub.router
        router.get(f"{MGMT}/auth-files").mock(side_effect=self._list)
        router.get(f"{MGMT}/{PROVIDER}-auth-url").mock(side_effect=self._start)
        router.get(f"{MGMT}/get-auth-status").mock(side_effect=self._status)
        router.post(f"{MGMT}/oauth-callback").mock(side_effect=self._callback)
        router.patch(f"{MGMT}/auth-files/fields").mock(side_effect=self._patch)
        router.delete(f"{MGMT}/auth-files").mock(side_effect=self._delete)
        router.get(f"{MGMT}/model-definitions/{PROVIDER}").mock(
            return_value=httpx.Response(200, json={"models": [{"id": settings.llm_model}]})
        )

    # -- phía CLIProxy ----------------------------------------------------

    def _list(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"files": list(self.files.values())})

    def _start(self, request: httpx.Request) -> httpx.Response:
        state = uuid.uuid4().hex
        self.pending[state] = self.next_email
        return httpx.Response(
            200,
            json={
                "status": "ok",
                # Nguyên văn hình dạng đo được từ container thật ngày 2026-09-22.
                "url": (
                    "https://accounts.google.com/o/oauth2/v2/auth?response_type=code"
                    f"&redirect_uri=http%3A%2F%2Flocalhost%3A51121%2Foauth-callback&state={state}"
                ),
                "state": state,
            },
        )

    def _status(self, request: httpx.Request) -> httpx.Response:
        state = request.url.params.get("state", "")
        if state in self.done:
            return httpx.Response(200, json={"status": "ok"})
        if state in self.pending:
            return httpx.Response(200, json={"status": "wait"})
        return httpx.Response(200, json={"status": "error", "error": "unknown state"})

    def _callback(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.callbacks.append(body)
        state, code = _parse(body.get("redirect_url", ""))
        if not state:
            return httpx.Response(400, json={"status": "error", "error": "state is required"})
        if not code:
            return httpx.Response(
                400, json={"status": "error", "error": "code or error is required"}
            )
        if state in self.done:
            return httpx.Response(
                409, json={"status": "error", "error": "oauth flow is already completed"}
            )
        if state not in self.pending:
            # ⚠️ 404 — và `CliProxyClient.request()` dịch mọi 404 quản trị thành "Management
            # API bị tắt". Đây là lý do `submit_oauth_callback()` phải dịch lại.
            return httpx.Response(
                404, json={"status": "error", "error": "unknown or expired state"}
            )
        self._login(self.pending.pop(state))
        self.done.add(state)
        return httpx.Response(200, json={"status": "ok"})

    def _patch(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.prefixes[body["name"]] = body["prefix"]
        return httpx.Response(200, json={"status": "ok"})

    def _delete(self, request: httpx.Request) -> httpx.Response:
        name = request.url.params["name"]
        if self.files.pop(name, None) is None:
            return httpx.Response(404, json={"error": "auth file not found"})
        return httpx.Response(200, json={"status": "ok"})

    def _login(self, email: str) -> None:
        self.clock += 1
        name = f"{PROVIDER}-{email}.json"
        self.files[name] = {
            "name": name,
            "provider": PROVIDER,
            "label": email,
            "status": "active",
            "disabled": False,
            "modtime": f"2026-09-22T10:00:{self.clock:02d}Z",
        }


def _parse(url: str) -> tuple[str, str]:
    from urllib.parse import parse_qs, urlsplit

    query = parse_qs(urlsplit(url).query)
    return (query.get("state", [""])[0], query.get("code", [""])[0])


def callback_url(state: str, code: str = "4/0AVMBsJ-fake-code") -> str:
    """Đúng thứ người dùng chép từ thanh địa chỉ của trang "không kết nối được"."""
    return f"http://localhost:51121/oauth-callback?state={state}&code={code}&scope=email"


@pytest.fixture
def proxy(cliproxy: CliProxyStub) -> CallbackProxy:
    return CallbackProxy(cliproxy)


@pytest.fixture
async def alice(db_session: AsyncSession) -> User:
    return await make_user(db_session, "alice@example.com", connected=False)


@pytest.fixture
async def bob(db_session: AsyncSession) -> User:
    return await make_user(db_session, "bob@example.com", connected=False)


# --------------------------------------------------------------------------- tiền đề


async def test_without_the_paste_the_flow_never_completes(
    app_client: ClientFactory, proxy: CallbackProxy, alice: User
) -> None:
    """Tiền đề của cả task: trên tên miền thật, chỉ poll thôi thì không bao giờ xong.

    Không có test này thì ba test dưới chỉ chứng minh "đường mới chạy được", chứ không chứng
    minh **vì sao phải có nó**.
    """
    async with app_client(alice) as http:
        state = (await http.post("/api/integration/connect")).json()["state"]
        polls = [
            (await http.get("/api/integration/oauth-status", params={"state": state})).json()
            for _ in range(3)
        ]
        status = (await http.get("/api/integration/status")).json()

    assert [p["status"] for p in polls] == ["wait", "wait", "wait"]
    assert status["connected"] is False
    assert alice.cliproxy_auth_file is None


# --------------------------------------------------------------------------- đường đi đúng


async def test_pasting_the_url_connects_and_claims_the_credential(
    app_client: ClientFactory, proxy: CallbackProxy, alice: User
) -> None:
    """Dán URL vào là xong trọn vẹn: credential được gắn tiền tố và về đúng chủ (13.1)."""
    async with app_client(alice) as http:
        state = (await http.post("/api/integration/connect")).json()["state"]
        done = await http.post(
            "/api/integration/oauth-callback",
            json={"state": state, "redirect_url": callback_url(state)},
        )
        status = (await http.get("/api/integration/status")).json()

    name = f"{PROVIDER}-alice.google@gmail.com.json"
    assert done.json() == {"status": "ok", "error": None}
    assert alice.cliproxy_auth_file == name
    assert proxy.prefixes == {name: credential_prefix(alice.id)}
    assert (status["connected"], status["accounts"]) == (True, ["alice.google@gmail.com"])


async def test_backend_forwards_the_whole_url_not_just_the_code(
    app_client: ClientFactory, proxy: CallbackProxy, alice: User
) -> None:
    """Gửi nguyên `redirect_url` cho CLIProxy tự bóc — đúng hợp đồng `oauth_callback.go:55-74`.

    Tự bóc `code` rồi gửi riêng cũng chạy, nhưng sẽ tự nhận lấy việc phân tích URL mà upstream
    đã làm sẵn, và sai lệch ngay khi Google thêm tham số mới.
    """
    async with app_client(alice) as http:
        state = (await http.post("/api/integration/connect")).json()["state"]
        await http.post(
            "/api/integration/oauth-callback",
            json={"state": state, "redirect_url": callback_url(state, "code-xyz")},
        )

    assert proxy.callbacks == [
        {"provider": PROVIDER, "redirect_url": callback_url(state, "code-xyz")}
    ]


# --------------------------------------------------------------------------- dán sai


async def test_url_of_another_login_round_is_refused(
    app_client: ClientFactory, proxy: CallbackProxy, alice: User
) -> None:
    """Dán URL của lượt kết nối trước: chặn ngay ở backend, không tiêu lượt gọi CLIProxy."""
    async with app_client(alice) as http:
        first = (await http.post("/api/integration/connect")).json()["state"]
        second = (await http.post("/api/integration/connect")).json()["state"]
        response = await http.post(
            "/api/integration/oauth-callback",
            json={"state": second, "redirect_url": callback_url(first)},
        )

    assert response.status_code == 400
    assert "lượt kết nối khác" in response.json()["detail"]
    assert proxy.callbacks == []


async def test_expired_state_says_so_instead_of_blaming_the_config(
    app_client: ClientFactory, proxy: CallbackProxy, alice: User
) -> None:
    """404 của route này là "state hết hạn", KHÔNG phải "Management API bị tắt".

    `CliProxyClient.request()` dịch mọi 404 quản trị thành thông báo bảo đi sửa `config.yaml` —
    đúng với `auth-files`, sai hẳn ở đây. Người dán URL muộn 5 phút phải được bảo là bấm kết
    nối lại, chứ không phải đi sửa cấu hình máy chủ.
    """
    async with app_client(alice) as http:
        state = (await http.post("/api/integration/connect")).json()["state"]
        proxy.pending.clear()  # CLIProxy đã quên phiên này (quá 5 phút)
        response = await http.post(
            "/api/integration/oauth-callback",
            json={"state": state, "redirect_url": callback_url(state)},
        )

    detail = response.json()["detail"]
    assert response.status_code == 400
    assert "hết hạn" in detail
    assert "config.yaml" not in detail


async def test_url_without_code_is_a_user_error_not_a_server_error(
    app_client: ClientFactory, proxy: CallbackProxy, alice: User
) -> None:
    """Chép thiếu phần sau dấu `?` là lỗi thao tác — 400 kèm lời chỉ dẫn, không phải 502."""
    async with app_client(alice) as http:
        state = (await http.post("/api/integration/connect")).json()["state"]
        response = await http.post(
            "/api/integration/oauth-callback",
            json={
                "state": state,
                "redirect_url": f"http://localhost:51121/oauth-callback?state={state}",
            },
        )

    assert response.status_code == 400
    assert "`code`" in response.json()["detail"]


async def test_reusing_the_same_url_twice_is_refused(
    app_client: ClientFactory, proxy: CallbackProxy, alice: User
) -> None:
    """Bấm "Hoàn tất" hai lần: lần sau phải nói rõ là đã dùng rồi, không im lặng thành công."""
    async with app_client(alice) as http:
        state = (await http.post("/api/integration/connect")).json()["state"]
        url = callback_url(state)
        first = await http.post(
            "/api/integration/oauth-callback", json={"state": state, "redirect_url": url}
        )
        second = await http.post(
            "/api/integration/oauth-callback", json={"state": state, "redirect_url": url}
        )

    assert first.json()["status"] == "ok"
    # Lượt sau **không** bị chặn ở khâu sở hữu: `owns_session()` cố ý trả True cho state lạ
    # (phiên đã quên sau khi xong, và process khởi động lại cũng làm mọi state thành lạ — khoá
    # cứng ở đó thì mất luôn đường kết nối sau mỗi lần deploy). Chặn ở đây là CLIProxy, và câu
    # chữ của nó phải tới được người dùng nguyên vẹn.
    assert second.status_code == 400
    assert "đã dùng rồi" in second.json()["detail"]


# --------------------------------------------------------------------------- tách người dùng


async def test_callback_of_another_user_is_404(
    app_client: ClientFactory, proxy: CallbackProxy, alice: User, bob: User
) -> None:
    """Đường mới không được mở ra một lối vòng qua luật tách dữ liệu của 13.1.

    B biết `state` của A (ví dụ A dán nhầm vào chỗ công khai) vẫn không cướp được credential.
    """
    async with app_client(alice) as http:
        state = (await http.post("/api/integration/connect")).json()["state"]
    async with app_client(bob) as http:
        stolen = await http.post(
            "/api/integration/oauth-callback",
            json={"state": state, "redirect_url": callback_url(state)},
        )

    assert stolen.status_code == 404
    assert proxy.callbacks == []
    assert bob.cliproxy_auth_file is None
    assert alice.cliproxy_auth_file is None


async def test_two_users_paste_their_own_urls_and_each_keeps_a_separate_prefix(
    app_client: ClientFactory, proxy: CallbackProxy, alice: User, bob: User
) -> None:
    """Đúng tiêu chí **A10** trên bản deploy: hai người, hai Gmail, hai tiền tố riêng."""
    async with app_client(alice) as http:
        proxy.next_email = "alice.google@gmail.com"
        state = (await http.post("/api/integration/connect")).json()["state"]
        await http.post(
            "/api/integration/oauth-callback",
            json={"state": state, "redirect_url": callback_url(state)},
        )
    async with app_client(bob) as http:
        proxy.next_email = "bob.google@gmail.com"
        state = (await http.post("/api/integration/connect")).json()["state"]
        await http.post(
            "/api/integration/oauth-callback",
            json={"state": state, "redirect_url": callback_url(state)},
        )
        bob_status = (await http.get("/api/integration/status")).json()
    async with app_client(alice) as http:
        alice_status = (await http.get("/api/integration/status")).json()

    assert alice_status["accounts"] == ["alice.google@gmail.com"]
    assert bob_status["accounts"] == ["bob.google@gmail.com"]
    assert proxy.prefixes == {
        f"{PROVIDER}-alice.google@gmail.com.json": credential_prefix(alice.id),
        f"{PROVIDER}-bob.google@gmail.com.json": credential_prefix(bob.id),
    }
