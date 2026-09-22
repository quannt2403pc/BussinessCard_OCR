"""Nút OAuth CLIProxy: connect / status / disconnect / test.

Chủ sở hữu: Q | Task: 2.4 | xem Task.md

Router này khai **đường dẫn đầy đủ** (không đặt `prefix`) vì nó phục vụ cả API
(`/api/integration/*`) lẫn trang HTML `/settings` — gom vào một file để không phải đụng
`app/main.py` (quy ước số 4).

Hai điều dễ làm sai, đã ghi thành ràng buộc trong mã bên dưới:

1. **Badge đọc từ `auth-files`.** `get-auth-status` không có `state` trả `{"status":"ok"}` kể cả
   khi chưa đăng nhập bao giờ → badge xanh vĩnh viễn (I-02). `oauth_status` chỉ dùng cho
   endpoint poll, và `CliProxyClient.oauth_status()` bắt buộc có `state`.
2. **CLIProxy chết ≠ đã ngắt kết nối.** Token nằm trong volume `cliproxy_auths`, container chết
   không làm mất token. Lúc đó `/status` trả `reachable=false` kèm giá trị cache lần cuối, chứ
   không được vẽ badge "Chưa kết nối" (docker-compose cố ý cho `api` lên mà không chờ cliproxy).
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.security import CurrentUser
from app.core.templates import templates
from app.models.integration import IntegrationStatus
from app.services import llm
from app.services.cliproxy_client import (
    CliProxyAuthError,
    CliProxyClient,
    CliProxyDisabledError,
    CliProxyError,
    CliProxyUnavailableError,
)

logger = logging.getLogger(__name__)

router = APIRouter()

#: UI poll `get-auth-status` 2 giây một lần trong lúc chờ người dùng đồng ý (Plan.md mục 2.5).
POLL_INTERVAL_MS = 2000

#: Hết giờ chờ OAuth. Quá mốc này UI tự huỷ phiên để không treo `state` vô hạn trong CLIProxy.
OAUTH_TIMEOUT_SECONDS = 300

#: Prompt của nút "Kiểm tra kết nối": ngắn, rẻ, và câu trả lời đúng thì nhìn là biết ngay.
TEST_PROMPT = (
    "Trả lời đúng một câu tiếng Việt ngắn (dưới 20 từ) xác nhận bạn đang hoạt động, "
    "kèm tên model của bạn."
)


# --------------------------------------------------------------------------- schema


class IntegrationStatusOut(BaseModel):
    """Trạng thái kết nối để vẽ badge. `reachable=false` nghĩa là **chưa biết**, không phải chưa kết nối."""

    provider: str
    connected: bool
    account_label: str | None = None
    accounts: list[str] = Field(default_factory=list)
    model: str
    models: list[str] = Field(default_factory=list)
    model_available: bool | None = Field(
        default=None,
        description="LLM_MODEL có trong danh mục thật của channel không (I-03); "
        "None = chưa lấy được danh mục.",
    )
    reachable: bool = True
    from_cache: bool = False
    detail: str | None = None
    last_checked_at: datetime | None = None


class ConnectOut(BaseModel):
    """URL để mở tab OAuth + `state` để poll. Không trả management key ra ngoài."""

    url: str
    state: str
    poll_interval_ms: int = POLL_INTERVAL_MS
    timeout_seconds: int = OAUTH_TIMEOUT_SECONDS


class OAuthStatusOut(BaseModel):
    """Kết quả poll. `status` ∈ {wait, ok, error} — đọc body, không nhìn mã HTTP."""

    status: str
    error: str | None = None


class DisconnectOut(BaseModel):
    removed: list[str] = Field(default_factory=list)
    connected: bool = False


class ConnectionTestOut(BaseModel):
    """Kết quả nút "Kiểm tra kết nối".

    Gọi hỏng **không** trả 5xx: với trang cài đặt thì "gọi thử thất bại" là một kết quả hợp lệ
    cần hiển thị, không phải sự cố của API này.
    """

    ok: bool
    model: str
    text: str | None = None
    detail: str | None = None
    elapsed_ms: int


# --------------------------------------------------------------------------- trang HTML


@router.get("/settings", response_class=HTMLResponse, tags=["ui"])
async def settings_page(request: Request) -> HTMLResponse:
    """Trang Cài đặt — badge trạng thái + nút OAuth (task 2.5).

    Trang render ngay, không chờ CLIProxy: badge do JavaScript gọi `/api/integration/status`
    điền vào. Nhờ vậy cliproxy chết thì trang vẫn mở được và hiện đúng lý do.
    """
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "active_nav": "settings",
            "provider": settings.cliproxy_auth_provider,
            "model": settings.llm_model,
            "poll_interval_ms": POLL_INTERVAL_MS,
            "oauth_timeout_seconds": OAUTH_TIMEOUT_SECONDS,
        },
    )


# --------------------------------------------------------------------------- API


@router.get("/api/integration/status", response_model=IntegrationStatusOut, tags=["integration"])
async def get_status(
    db: Annotated[AsyncSession, Depends(get_db)], user: CurrentUser
) -> IntegrationStatusOut:
    """Trạng thái kết nối OAuth. **Nguồn sự thật: `GET /v0/management/auth-files`** (I-02).

    ⚠️ **Tính đến D12, cache là của từng người nhưng CLIProxy thì vẫn dùng chung.** Revision
    `0005` đã đổi khoá chính `integration_status` thành `(user_id, provider)` nên hai người có
    hai dòng cache riêng, còn `auth_files()` bên dưới vẫn liệt kê **mọi** credential của cả
    máy — A nhìn badge sẽ thấy cả tài khoản Google mà B đã nối. Tách hẳn credential theo người
    là **task 13.1/13.2** (gắn tiền tố, phương án (a) đã chốt ở ADR 12.1); ở đây chỉ đủ để mọi
    lệnh ghi không còn hỏng vì thiếu `user_id`.
    """
    provider = settings.cliproxy_auth_provider
    cached = await db.get(IntegrationStatus, (user.id, provider))

    try:
        async with CliProxyClient() as proxy:
            files = await proxy.auth_files()
            models = await _safe_model_ids(proxy)
    except (CliProxyUnavailableError, CliProxyAuthError, CliProxyDisabledError) as exc:
        # Ba lỗi này đều là "không hỏi được CLIProxy" chứ không phải "người dùng chưa kết nối".
        logger.warning("Không đọc được trạng thái CLIProxy: %s", exc)
        return IntegrationStatusOut(
            provider=provider,
            connected=bool(cached.connected) if cached else False,
            account_label=cached.account_label if cached else None,
            accounts=[cached.account_label] if cached and cached.account_label else [],
            model=settings.llm_model,
            reachable=False,
            from_cache=cached is not None,
            detail=exc.message,
            last_checked_at=cached.last_checked_at if cached else None,
        )
    except CliProxyError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=exc.message) from exc

    usable = [f for f in files if f.usable]
    accounts = [f.label for f in usable]
    label = ", ".join(accounts) if accounts else None
    row = await _save_cache(db, user.id, provider, connected=bool(usable), account_label=label)

    detail = None
    if files and not usable:
        # Có credential nhưng CLIProxy đánh dấu hỏng — badge phải đỏ, kèm lý do.
        detail = "Credential đã lưu nhưng CLIProxy đánh dấu không dùng được. Hãy kết nối lại."

    return IntegrationStatusOut(
        provider=provider,
        connected=bool(usable),
        account_label=label,
        accounts=accounts,
        model=settings.llm_model,
        models=models,
        model_available=(settings.llm_model in models) if models else None,
        reachable=True,
        detail=detail,
        last_checked_at=row.last_checked_at,
    )


@router.post("/api/integration/connect", response_model=ConnectOut, tags=["integration"])
async def connect() -> ConnectOut:
    """Mở phiên OAuth, trả URL cho UI mở tab mới + `state` để poll.

    Backend giữ management key; UI chỉ nhận `url` và `state` (Plan.md mục 2.5 bước 2).
    """
    try:
        async with CliProxyClient() as proxy:
            session = await proxy.start_oauth()
    except CliProxyUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.message) from exc
    except CliProxyError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=exc.message) from exc
    return ConnectOut(url=session.url, state=session.state)


@router.get("/api/integration/oauth-status", response_model=OAuthStatusOut, tags=["integration"])
async def oauth_status(
    state: Annotated[str, Query(min_length=1, description="`state` nhận từ /connect")],
) -> OAuthStatusOut:
    """Poll một phiên OAuth **đang chạy** — chỉ dùng trong lúc chờ người dùng đồng ý.

    `state` là tham số bắt buộc, cố ý: thiếu nó thì CLIProxy trả "ok" kể cả khi chưa đăng nhập
    bao giờ (I-02). Xong (`status=ok`) thì UI phải gọi lại `/status` để vẽ badge.
    """
    try:
        async with CliProxyClient() as proxy:
            result = await proxy.oauth_status(state)
    except CliProxyUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.message) from exc
    except CliProxyError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=exc.message) from exc
    return OAuthStatusOut(status=result.status, error=result.error)


@router.delete("/api/integration/oauth-session", tags=["integration"])
async def cancel_oauth(
    state: Annotated[str, Query(min_length=1, description="`state` của phiên cần huỷ")],
) -> dict[str, bool]:
    """Huỷ phiên OAuth đang chờ: người dùng bấm Huỷ, hoặc UI hết giờ poll."""
    try:
        async with CliProxyClient() as proxy:
            cancelled = await proxy.cancel_oauth(state)
    except CliProxyUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.message) from exc
    except CliProxyError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=exc.message) from exc
    return {"cancelled": cancelled}


@router.post("/api/integration/disconnect", response_model=DisconnectOut, tags=["integration"])
async def disconnect(
    db: Annotated[AsyncSession, Depends(get_db)], user: CurrentUser
) -> DisconnectOut:
    """Ngắt kết nối: xoá mọi credential của provider trong CLIProxy rồi hạ cờ trong cache.

    Không có API "xoá tất cả" bên CLIProxy — phải liệt kê rồi xoá từng file (xem client).
    """
    provider = settings.cliproxy_auth_provider
    try:
        async with CliProxyClient() as proxy:
            removed = await proxy.disconnect()
    except CliProxyUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.message) from exc
    except CliProxyError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=exc.message) from exc

    await _save_cache(db, user.id, provider, connected=False, account_label=None)
    return DisconnectOut(removed=removed, connected=False)


@router.post("/api/integration/test", response_model=ConnectionTestOut, tags=["integration"])
async def test_connection() -> ConnectionTestOut:
    """Gọi thử một prompt ngắn tới model đang cấu hình — tiêu chí hoàn thành D2.

    Đây là phép thử **đầu–cuối thật**: đi qua CLIProxy, dùng credential OAuth thật, đúng model
    trong `LLM_MODEL`. Badge xanh mà nút này đỏ nghĩa là token còn nhưng model sai tên (I-03)
    hoặc tài khoản hết quota.
    """
    started = time.perf_counter()
    try:
        text = await llm.generate_text(TEST_PROMPT, max_output_tokens=128)
    except llm.LLMError as exc:
        return ConnectionTestOut(
            ok=False,
            model=settings.llm_model,
            detail=str(exc),
            elapsed_ms=_elapsed_ms(started),
        )
    return ConnectionTestOut(
        ok=True,
        model=settings.llm_model,
        text=text,
        elapsed_ms=_elapsed_ms(started),
    )


# --------------------------------------------------------------------------- nội bộ


async def _safe_model_ids(proxy: CliProxyClient) -> list[str]:
    """Danh mục model của channel; hỏng thì trả rỗng.

    Chỉ để cảnh báo `LLM_MODEL` sai tên — không đáng làm hỏng cả badge nếu endpoint này lỗi.
    """
    try:
        return await proxy.model_ids()
    except CliProxyError as exc:
        logger.info("Không lấy được danh mục model: %s", exc)
        return []


async def _save_cache(
    db: AsyncSession,
    user_id: uuid.UUID,
    provider: str,
    *,
    connected: bool,
    account_label: str | None,
) -> IntegrationStatus:
    """Ghi cache trạng thái để badge hiện ngay khi tải trang, không phải chờ CLIProxy.

    Khoá chính là `(user_id, provider)` từ revision `0005`, nên `db.get()` phải nhận **tuple** —
    truyền một mình `provider` như trước 12.5 thì SQLAlchemy báo thiếu thành phần khoá.
    """
    row = await db.get(IntegrationStatus, (user_id, provider))
    if row is None:
        row = IntegrationStatus(user_id=user_id, provider=provider)
        db.add(row)
    row.connected = connected
    row.account_label = account_label
    # Cột là `timestamp without time zone` (migration 0001) → lưu UTC dạng naive cho khỏi lệch.
    row.last_checked_at = datetime.now(UTC).replace(tzinfo=None)
    await db.commit()
    await db.refresh(row)
    return row


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
