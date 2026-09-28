"""Client Management API của CLIProxy (auth-url, auth-files, get-auth-status, oauth-session).

Chủ sở hữu: Q | Task: 2.2

Hai ràng buộc đã kiểm chứng bằng container thật:

- **Không retry khi 401/403.** Sai management key 5 lần là CLIProxy ban IP 30 phút, mà cả
  container `api` dùng chung một IP → tự khoá mình (I-05).
- **Trạng thái kết nối đọc từ `auth-files`, không phải `get-auth-status`.** Cái sau thiếu `state`
  thì trả `{"status":"ok"}` kể cả khi chưa đăng nhập bao giờ (I-02).

Hai điểm dễ vấp khác, đo trực tiếp trên container:

1. **`get-auth-status` báo lỗi bằng HTTP 200** — phải đọc trường `status` trong body.
2. **Không có "xoá tất cả credential"**: `DELETE /auth-files` bắt buộc `?name=<tên file>`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

#: Tiền tố của toàn bộ route quản trị. Route gọi model (`/v1beta/…`) KHÔNG nằm dưới đây và không
#: cần management key.
MANAGEMENT_PREFIX = "/v0/management"

#: Route quản trị trả nhanh (đọc file cục bộ), 10s là quá đủ.
MANAGEMENT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

#: Chỉ retry những mã này. **401/403 cố ý không có trong danh sách** — I-05.
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

DEFAULT_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 0.5


# --------------------------------------------------------------------------- lỗi


class CliProxyError(RuntimeError):
    """Lỗi gốc khi làm việc với CLIProxy. Router bắt loại này là bắt được tất cả."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        payload: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.payload = payload


class CliProxyAuthError(CliProxyError):
    """401/403 — sai hoặc thiếu management key.

    **Tuyệt đối không retry**: năm lần sai là CLIProxy ban IP 30 phút, mà cả container `api` chỉ
    có một IP (I-05). Gỡ ban: `docker compose restart cliproxy`.
    """


class CliProxyDisabledError(CliProxyError):
    """404 trên route quản trị — `remote-management.secret-key` để rỗng nên API bị tắt hẳn.

    CLIProxy trả 404 chứ không phải 401, rất dễ đọc nhầm thành "sai URL".
    """


class CliProxyNoCredentialError(CliProxyError):
    """503 `auth_unavailable` — CLIProxy sống nhưng chưa có credential nào phục vụ được model.

    **Không retry** — chờ thêm không làm token tự xuất hiện, người dùng phải bấm nút OAuth.
    """


class CliProxyResponseError(CliProxyError):
    """4xx còn lại (ví dụ `400 invalid name`, `404 auth file not found`)."""


class CliProxyCallbackError(CliProxyError):
    """URL callback người dùng dán vào không dùng được.

    Tách riêng vì đây là **lỗi thao tác của người dùng**: router trả 400 chứ không phải 502.
    """


class CliProxyUnavailableError(CliProxyError):
    """Không gọi được CLIProxy: lỗi mạng, timeout, hoặc 5xx sau khi hết lượt thử.

    Router phải coi đây là "chưa biết trạng thái", không phải "chưa kết nối" — container chết
    không có nghĩa là người dùng mất token (token nằm trong volume).
    """


# --------------------------------------------------------------------------- kiểu dữ liệu


@dataclass(frozen=True)
class AuthSession:
    """Một phiên OAuth vừa mở: URL để người dùng bấm + `state` để poll tiến trình."""

    url: str
    state: str


@dataclass(frozen=True)
class AuthStatus:
    """Kết quả poll một phiên OAuth đang chạy.

    `status` là `wait` | `ok` | `error`. CLIProxy trả `error` kèm **HTTP 200**, nên trường này
    mới là nguồn sự thật.
    """

    status: str
    error: str | None = None

    @property
    def is_done(self) -> bool:
        return self.status == "ok"

    @property
    def is_waiting(self) -> bool:
        return self.status == "wait"


@dataclass(frozen=True)
class ProviderBlock:
    """Nhà cung cấp từ chối credential, kèm việc người dùng phải làm để gỡ (I-42).

    Bóc từ `AuthFile.status_message` — chuỗi JSON lỗi nguyên văn của Google mà CLIProxy chép lại.
    """

    message: str
    reason: str
    action_url: str
    action_label: str

    @property
    def one_line(self) -> str:
        """Câu của Google, bỏ dấu chấm cuối để ghép tiếp không thành "continue.."."""
        return (self.message or self.reason or "không rõ lý do").rstrip(". ") + "."

    @property
    def needs_verification(self) -> bool:
        """Google đòi **xác minh tài khoản**, không phải đòi tiền.

        CLIProxy gắn nhãn `payment_required` cho mọi `403` upstream, kể cả cái này — tin nhãn đó
        là bảo người dùng đi mua gói để chữa một thứ chỉ cần bấm một đường link.
        """
        return self.reason == "VALIDATION_REQUIRED"

    @classmethod
    def parse(cls, status_message: str) -> ProviderBlock | None:
        """`None` nếu không bóc được — đoán bừa lý do còn tệ hơn im lặng."""
        if not status_message.strip():
            return None
        try:
            error = json.loads(status_message).get("error") or {}
        except (ValueError, AttributeError):
            return None

        message = str(error.get("message") or "").strip()
        reason, url, label = "", "", ""
        for detail in error.get("details") or []:
            if not isinstance(detail, dict):
                continue
            reason = reason or str(detail.get("reason") or "")
            meta = detail.get("metadata")
            if isinstance(meta, dict):
                url = url or str(meta.get("validation_url") or "")
                label = label or str(meta.get("validation_url_link_text") or "")

        if not message and not reason:
            return None
        return cls(message=message, reason=reason, action_url=url, action_label=label)


@dataclass(frozen=True)
class Cooldown:
    """Một cặp *(credential, model)* mà CLIProxy đang tạm ngừng gọi (I-42).

    Phải dựng kiểu riêng vì cooldown **không** bật `disabled`, cũng **không** bật `unavailable`:
    `AuthFile.usable` vẫn `True`, badge vẫn xanh, trong khi mọi lời gọi trả `503 auth_unavailable`.
    """

    model: str
    reason: str
    remaining_seconds: int
    http_status: int
    retry_at: str

    @classmethod
    def from_payload(cls, item: dict[str, Any]) -> Cooldown:
        """Chịu được khi thiếu trường — hợp đồng này dò ra bằng quan sát, không có tài liệu."""
        return cls(
            model=str(item.get("model_key") or item.get("model") or ""),
            reason=str(item.get("reason") or ""),
            remaining_seconds=int(item.get("remaining_seconds") or 0),
            http_status=int(item.get("http_status") or 0),
            retry_at=str(item.get("retry_at") or ""),
        )


@dataclass(frozen=True)
class AuthFile:
    """Một credential đã lưu trong `auth-dir` của CLIProxy.

    Có ít nhất một `AuthFile` của provider ta dùng = **đã kết nối**. Nguồn sự thật cho badge (I-02).
    """

    name: str
    provider: str
    label: str
    status: str
    disabled: bool
    cooldowns: tuple[Cooldown, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def usable(self) -> bool:
        """Credential còn dùng được — CLIProxy tự đánh dấu `disabled`/`unavailable` khi hỏng.

        **Cooldown cố ý KHÔNG tính vào đây**: credential đang nghỉ vẫn lành lặn và model khác vẫn
        gọi được ngay lúc này. Coi nó là "không dùng được" thì người dùng đi đăng nhập lại để
        chữa một thứ mà đăng nhập lại không đụng tới được (I-42).
        """
        return not self.disabled and not bool(self.raw.get("unavailable"))

    def cooldown_for(self, model: str) -> Cooldown | None:
        """Cooldown đang áp lên đúng model này, nếu có.

        So khớp theo tên **đã cắt tiền tố**: lời gọi đi bằng `u<hex>/gemini-3-flash` còn CLIProxy
        ghi cooldown theo `gemini-3-flash`.
        """
        bare = model.rsplit("/", 1)[-1]
        return next((c for c in self.cooldowns if c.model == bare), None)

    @property
    def provider_block(self) -> ProviderBlock | None:
        """Lý do **nhà cung cấp** chặn credential này, bóc từ `status_message` (I-42).

        CLIProxy nhét nguyên văn JSON lỗi của Google vào `status_message` rồi bật `unavailable`.
        Đó là chỗ **duy nhất** nói đúng chuyện gì đang xảy ra, và nó kèm sẵn đường link để người
        dùng tự gỡ. Đo thật: `403 PERMISSION_DENIED / VALIDATION_REQUIRED` kèm `validation_url`.
        """
        return ProviderBlock.parse(str(self.raw.get("status_message") or ""))

    @classmethod
    def from_payload(cls, item: dict[str, Any]) -> AuthFile:
        """Dựng từ một phần tử của `{"files":[…]}`, chịu được khi thiếu trường."""
        name = str(item.get("name") or item.get("id") or item.get("path") or "")
        # `label` là email hiển thị; bản cũ có thể chỉ có `email`/`account`.
        label = str(item.get("label") or item.get("email") or item.get("account") or name)
        raw_cooldowns = item.get("cooldowns")
        cooldowns = tuple(
            Cooldown.from_payload(c)
            for c in (raw_cooldowns if isinstance(raw_cooldowns, list) else [])
            if isinstance(c, dict)
        )
        return cls(
            name=name,
            provider=str(item.get("provider") or item.get("type") or ""),
            label=label,
            status=str(item.get("status") or ""),
            disabled=bool(item.get("disabled")),
            cooldowns=cooldowns,
            raw=item,
        )


# --------------------------------------------------------------------------- client


class CliProxyClient:
    """Bọc HTTP quanh CLIProxy: quản trị OAuth và chuyển tiếp lời gọi model.

    Dùng như context manager::

        async with CliProxyClient() as proxy:
            files = await proxy.auth_files()
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        mgmt_key: str | None = None,
        timeout: httpx.Timeout | None = None,
        provider: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = (base_url or settings.cliproxy_base_url).rstrip("/")
        self.mgmt_key = mgmt_key if mgmt_key is not None else settings.cliproxy_mgmt_key
        self.provider = provider or settings.cliproxy_auth_provider
        self._timeout = timeout or MANAGEMENT_TIMEOUT
        self._client = client
        self._owns_client = client is None

    # -- vòng đời ---------------------------------------------------------

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> CliProxyClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    # -- lớp HTTP ---------------------------------------------------------

    def _headers(self, management: bool) -> dict[str, str]:
        """Management key chỉ gắn cho route quản trị.

        Route gọi model không cần (`api-keys: []` = CLIProxy không kiểm tra client). Gắn thừa chỉ
        làm tăng rủi ro đụng bộ đếm ban của I-05.
        """
        if not management:
            return {}
        if not self.mgmt_key:
            raise CliProxyError(
                "Chưa đặt CLIPROXY_MGMT_KEY. Điền vào .env cho trùng với "
                "remote-management.secret-key trong cliproxy/config.yaml."
            )
        return {"Authorization": f"Bearer {self.mgmt_key}"}

    async def request(
        self,
        method: str,
        path: str,
        *,
        management: bool = True,
        params: dict[str, Any] | None = None,
        json: Any = None,
        timeout: httpx.Timeout | float | None = None,
        attempts: int = DEFAULT_ATTEMPTS,
    ) -> httpx.Response:
        """Gọi CLIProxy và ánh xạ lỗi HTTP sang exception của module này.

        Retry: chỉ với lỗi mạng và `RETRY_STATUS` (429/5xx), backoff 0.5s → 1s.
        **Không bao giờ retry 401/403** (I-05).
        """
        url = f"{MANAGEMENT_PREFIX}{path}" if management else path
        headers = self._headers(management)
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                response = await self.client.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers=headers,
                    timeout=timeout if timeout is not None else self._timeout,
                )
            except httpx.HTTPError as exc:  # gồm timeout, DNS, connect refused
                last_error = exc
                logger.warning("CLIProxy %s %s lỗi mạng (lần %d): %s", method, url, attempt, exc)
            else:
                if response.status_code in (401, 403):
                    raise CliProxyAuthError(
                        _error_message(
                            response,
                            "Management key sai hoặc bị từ chối. KHÔNG thử lại: sai 5 lần là "
                            "CLIProxy ban IP 30 phút (I-05). Kiểm tra CLIPROXY_MGMT_KEY trong "
                            ".env có trùng secret-key trong cliproxy/config.yaml không, rồi "
                            "`docker compose restart cliproxy`.",
                        ),
                        status_code=response.status_code,
                        payload=_safe_json(response),
                    )
                if response.status_code == 404 and management:
                    raise CliProxyDisabledError(
                        _error_message(
                            response,
                            "Route quản trị trả 404. Thường là do "
                            "remote-management.secret-key để rỗng → CLIProxy tắt hẳn "
                            "Management API (404 chứ không phải 401).",
                        ),
                        status_code=404,
                        payload=_safe_json(response),
                    )
                if response.status_code == 503 and _is_auth_unavailable(response):
                    # 503 nhưng KHÔNG retry: thiếu credential thì chờ bao lâu cũng vậy, chỉ khi
                    # người dùng bấm nút OAuth mới có.
                    raise CliProxyNoCredentialError(
                        _error_message(
                            response,
                            "CLIProxy đang chạy nhưng không có credential nào phục vụ được "
                            "model này.",
                        ),
                        status_code=503,
                        payload=_safe_json(response),
                    )
                if response.status_code in RETRY_STATUS:
                    last_error = CliProxyUnavailableError(
                        _error_message(response, f"CLIProxy trả {response.status_code}."),
                        status_code=response.status_code,
                        payload=_safe_json(response),
                    )
                    logger.warning(
                        "CLIProxy %s %s trả %d (lần %d)", method, url, response.status_code, attempt
                    )
                elif response.is_error:
                    raise CliProxyResponseError(
                        _error_message(response, f"CLIProxy trả {response.status_code}."),
                        status_code=response.status_code,
                        payload=_safe_json(response),
                    )
                else:
                    return response

            if attempt < attempts:
                await asyncio.sleep(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))

        raise CliProxyUnavailableError(
            f"Không gọi được CLIProxy ({self.base_url}) sau {attempts} lần: {last_error}",
            status_code=getattr(last_error, "status_code", None),
        )

    async def request_json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """Như `request()` nhưng trả body JSON; body không phải JSON object → lỗi rõ ràng."""
        response = await self.request(method, path, **kwargs)
        data = _safe_json(response)
        if not isinstance(data, dict):
            raise CliProxyError(
                f"CLIProxy trả body không phải JSON object cho {path}: {response.text[:200]!r}",
                status_code=response.status_code,
            )
        return data

    # -- Management API ---------------------------------------------------

    async def start_oauth(self, provider: str | None = None) -> AuthSession:
        """Mở một phiên OAuth, trả URL để người dùng bấm + `state` để poll.

        Luôn gửi `is_webui=1` — nhánh dành cho luồng bấm nút trên UI, CLIProxy dựng thêm forwarder
        ở cổng 51121 để tự nhận callback. Cổng 51121 vẫn phải publish trong compose (I-04).
        """
        prov = provider or self.provider
        data = await self.request_json("GET", f"/{prov}-auth-url", params={"is_webui": "1"})
        url = str(data.get("url") or "")
        state = str(data.get("state") or "")
        if not url or not state:
            raise CliProxyError(f"`{prov}-auth-url` không trả đủ url/state: {data!r}", payload=data)
        return AuthSession(url=url, state=state)

    async def oauth_status(self, state: str) -> AuthStatus:
        """Poll một phiên OAuth **đang chạy**. Chỉ dùng trong lúc chờ người dùng đồng ý.

        Bắt buộc có `state`: thiếu nó thì CLIProxy trả `{"status":"ok"}` kể cả khi chưa đăng nhập
        bao giờ (I-02) — chặn thẳng bằng `ValueError` thay vì để lỗi âm thầm lọt ra tận badge.

        Lỗi được báo bằng **HTTP 200** kèm `{"status":"error"}`, nên hàm này đọc body.
        """
        if not state.strip():
            raise ValueError(
                "oauth_status() bắt buộc có `state`. Thiếu `state`, CLIProxy trả "
                '{"status":"ok"} kể cả khi chưa đăng nhập bao giờ (I-02). '
                "Trạng thái kết nối phải đọc từ auth_files()."
            )
        data = await self.request_json("GET", "/get-auth-status", params={"state": state})
        return AuthStatus(
            status=str(data.get("status") or "error"),
            error=(str(data["error"]) if data.get("error") else None),
        )

    async def submit_oauth_callback(
        self,
        *,
        redirect_url: str | None = None,
        code: str | None = None,
        state: str | None = None,
        provider: str | None = None,
    ) -> None:
        """Nộp hộ trình duyệt cái URL mà Google trả về — lối kết nối OAuth trên tên miền thật.

        Google luôn chuyển trình duyệt về `http://localhost:51121/oauth-callback` — `redirect_uri`
        **ghi cứng trong mã nguồn** CLIProxy, không khoá cấu hình nào đổi được. Trên máy dev điều
        đó thông một cách tình cờ; lên máy chủ thì `localhost` là máy của **người dùng**.

        Phương án mở `51121` qua Caddy **không dùng được**: `redirect_uri` phải khớp cái đã đăng
        ký cho client OAuth của Antigravity, mà đó là client của Google.

        Lối đi được là chính cái CLIProxy tự chừa cho TUI của nó: `POST /v0/management/oauth-callback`
        nhận `redirect_url` rồi tự bóc `code` và `state`. Người dùng chỉ cần chép thanh địa chỉ.
        """
        body: dict[str, str] = {"provider": provider or self.provider}
        if redirect_url and redirect_url.strip():
            # Gửi nguyên URL cho CLIProxy tự bóc, thay vì nhận lấy việc phân tích URL mà upstream
            # đã làm sẵn và lệch ngay khi Google thêm tham số mới.
            body["redirect_url"] = redirect_url.strip()
        elif code and code.strip():
            # Người dùng chỉ chép được mỗi đoạn `code` — vẫn nhận, `state` ta đang giữ sẵn.
            body["code"] = code.strip()
            if state and state.strip():
                body["state"] = state.strip()
        else:
            raise ValueError("submit_oauth_callback() cần `redirect_url` hoặc `code`.")
        try:
            await self.request_json("POST", "/oauth-callback", json=body)
        except (CliProxyDisabledError, CliProxyResponseError) as exc:
            # ⚠️ Bẫy: route này trả **404 cho `state` không còn tồn tại**, mà `request()` lại
            # dịch mọi 404 trên nhánh quản trị thành "Management API bị tắt".
            raise _callback_error(exc) from exc

    async def cancel_oauth(self, state: str) -> bool:
        """Huỷ một phiên OAuth đang chờ (người dùng đóng tab, hoặc UI hết giờ poll)."""
        if not state.strip():
            raise ValueError("cancel_oauth() bắt buộc có `state`.")
        data = await self.request_json("DELETE", "/oauth-session", params={"state": state})
        return bool(data.get("cancelled"))

    async def auth_files(self, provider: str | None = None) -> list[AuthFile]:
        """**Nguồn sự thật của badge.** Danh sách credential đã lưu, lọc theo provider.

        `provider=""` để lấy tất cả (dùng khi debug).
        """
        prov = self.provider if provider is None else provider
        data = await self.request_json("GET", "/auth-files")
        raw_files = data.get("files") or []
        if not isinstance(raw_files, list):
            raise CliProxyError(f"`auth-files` trả `files` không phải list: {raw_files!r}")
        files = [AuthFile.from_payload(item) for item in raw_files if isinstance(item, dict)]
        if prov:
            files = [f for f in files if f.provider == prov]
        return files

    async def delete_auth_file(self, name: str) -> None:
        """Xoá một credential theo **tên file**. Không có biến thể "xoá tất cả"."""
        if not name.strip():
            raise ValueError("delete_auth_file() bắt buộc có `name`.")
        await self.request("DELETE", "/auth-files", params={"name": name})

    async def set_prefix(self, name: str, prefix: str) -> None:
        if not name.strip():
            raise ValueError("set_prefix() bắt buộc có `name`.")
        await self.request("PATCH", "/auth-files/fields", json={"name": name, "prefix": prefix})

    async def disconnect(self, provider: str | None = None) -> list[str]:
        """Ngắt kết nối: xoá mọi credential của provider. Trả tên các file đã xoá.

        Credential bị xoá mất giữa chừng (404) thì bỏ qua — kết quả cuối vẫn là "đã ngắt".
        """
        removed: list[str] = []
        for auth_file in await self.auth_files(provider):
            try:
                await self.delete_auth_file(auth_file.name)
            except CliProxyResponseError as exc:
                if exc.status_code != 404:
                    raise
                logger.info("Credential %s đã biến mất trước khi xoá, bỏ qua", auth_file.name)
                continue
            removed.append(auth_file.name)
        return removed

    async def model_ids(self, channel: str | None = None) -> list[str]:
        """Danh mục model **thật** của channel — dùng để đối chiếu `settings.llm_model`.

        CLIProxy tự cập nhật danh mục từ xa nên không được hardcode theo tài liệu (I-03).
        """
        chan = channel or self.provider
        data = await self.request_json("GET", f"/model-definitions/{chan}")
        models = data.get("models") or []
        if not isinstance(models, list):
            return []
        return [str(m["id"]) for m in models if isinstance(m, dict) and m.get("id")]


# --------------------------------------------------------------------------- tiện ích


#: Câu chữ CLIProxy trả về ở `oauth_callback.go` → câu người dùng đọc hiểu được.
#: Khớp theo chuỗi con vì upstream có thể thêm chi tiết vào sau.
_CALLBACK_MESSAGES: tuple[tuple[str, str], ...] = (
    (
        "unknown or expired state",
        "Phiên kết nối đã hết hạn (CLIProxy chỉ giữ 5 phút) hoặc đã bị huỷ. "
        "Bấm “Kết nối AI” lại từ đầu rồi dán URL mới.",
    ),
    (
        "already completed",
        "URL này đã dùng rồi — mỗi lượt đăng nhập chỉ nộp được một lần. "
        "Nếu badge vẫn báo chưa kết nối thì bấm kết nối lại từ đầu.",
    ),
    (
        "code or error is required",
        "URL dán vào không có tham số `code`. Hãy chép **nguyên** thanh địa chỉ của trang "
        "báo lỗi sau khi đồng ý ở Google, gồm cả phần sau dấu `?`.",
    ),
    (
        "state is required",
        "URL dán vào không có tham số `state`. Hãy chép nguyên thanh địa chỉ, đừng cắt bớt.",
    ),
    (
        "invalid state",
        "Tham số `state` trong URL không hợp lệ. Bấm kết nối lại rồi dán URL của đúng lượt đó.",
    ),
    (
        "provider does not match",
        "URL này thuộc một luồng đăng nhập khác. Bấm kết nối lại rồi dán URL của đúng lượt đó.",
    ),
)


def _callback_error(exc: CliProxyError) -> CliProxyCallbackError:
    """Dịch lỗi của `/oauth-callback` sang câu nói được cho người dùng."""
    raw = f"{exc.message} {exc.payload!r}".lower()
    for needle, friendly in _CALLBACK_MESSAGES:
        if needle in raw:
            return CliProxyCallbackError(friendly, status_code=exc.status_code, payload=exc.payload)
    return CliProxyCallbackError(
        f"CLIProxy không nhận URL callback này: {exc.message}",
        status_code=exc.status_code,
        payload=exc.payload,
    )


def _safe_json(response: httpx.Response) -> Any:
    """JSON của response, trả `None` nếu body không phải JSON (CLIProxy có lúc trả text thuần)."""
    try:
        return response.json()
    except ValueError:
        return None


def _is_auth_unavailable(response: httpx.Response) -> bool:
    """Nhận diện 503 do thiếu credential, phân biệt với 503 do CLIProxy thực sự quá tải."""
    text = f"{_error_message(response, '')} {response.text[:500]}".lower()
    return "auth_unavailable" in text or "no auth available" in text


def _error_message(response: httpx.Response, fallback: str) -> str:
    """Gộp thông báo lỗi của CLIProxy vào câu tiếng Việt cho dễ đọc trên UI.

    Hai dạng envelope gặp thật: `{"error":"…"}` (route quản trị) và `{"error":{"message":…}}`
    (route gọi model).
    """
    data = _safe_json(response)
    detail = ""
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            detail = str(err.get("message") or "")
        elif err:
            detail = str(err)
    return f"{fallback} {detail}".strip()
