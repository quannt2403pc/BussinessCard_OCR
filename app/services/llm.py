"""Client Gemini qua CLIProxy: generate_text / generate_vision. FILE DÙNG CHUNG — chỉ Q sửa.

Chủ sở hữu: Q | Task: 2.3

Đường đi: `app` → CLIProxy `POST /v1beta/models/<model>:generateContent` → provider. Payload theo
chuẩn **Gemini native**.

Bốn cách hỏng, đã kiểm chứng trên container thật:

| Tình huống | CLIProxy trả | Ném ra |
|------------|--------------|--------|
| Model sai tên | `400 unknown provider for model …` | `LLMInvalidModelError` |
| Model đúng, **chưa từng có credential** | `400 unknown provider for model …` ← y hệt dòng trên | `LLMNotConnectedError` |
| Model đúng, credential vừa bị xoá | `503 auth_unavailable` | `LLMNotConnectedError` |
| Credential có nhưng token hỏng/hết hạn | `401 authentication_error` | `LLMNotConnectedError` |

Hai dòng đầu **giống hệt nhau** nhưng cách sửa trái ngược: một bên sửa `LLM_MODEL`, một bên chỉ
cần bấm nút OAuth. `_explain_unknown_provider()` phân biệt bằng cách tra danh mục model.

Route gọi model **không cần** management key, và ta cố ý không gửi để khỏi đụng bộ đếm ban (I-05).

**Không có `embed()`** — CLIProxy không có endpoint embedding, việc đó do service `embedder` lo.
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import log_llm_call
from app.services.cliproxy_client import (
    CliProxyAuthError,
    CliProxyClient,
    CliProxyError,
    CliProxyNoCredentialError,
    CliProxyResponseError,
)

logger = logging.getLogger(__name__)

#: Sinh nội dung có ảnh chậm hơn hẳn route quản trị — nới timeout, giữ connect ngắn.
LLM_TIMEOUT = httpx.Timeout(120.0, connect=5.0)

#: Định dạng ảnh cho phép gửi kèm. Khớp với danh sách `services/image.py` nhận (task 3.2).
SUPPORTED_IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/heic"})

#: `finishReason` nghĩa là câu trả lời bị chặn chứ không phải model sinh ra nội dung rỗng.
BLOCKED_FINISH_REASONS = frozenset({"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"})

#: Tool bật tra cứu Internet của Gemini (dùng ở enrichment F2).
#:
#: **camelCase, không phải `google_search`**: executor của antigravity đọc đúng khoá này, gõ
#: snake_case thì tool bị bỏ qua **im lặng**, model trả lời bằng kiến thức nội tại và không có
#: nguồn nào — đúng kịch bản bịa thông tin của rủi ro R4.
TOOL_GOOGLE_SEARCH: dict[str, dict[str, Any]] = {"googleSearch": {}}


class LLMError(RuntimeError):
    """Lỗi khi gọi LLM. Router bắt loại này là bắt được tất cả."""


class LLMNotConnectedError(LLMError):
    """Chưa kết nối OAuth, hoặc token hết hạn — gom ba dạng response ở bảng đầu file vào một lỗi.

    Gom lại vì cách xử lý y hệt nhau: mời người dùng bấm "Kết nối AI" ở `/settings`.
    """


class LLMProviderBlockedError(LLMNotConnectedError):
    """Nhà cung cấp từ chối **chính tài khoản**, và việc cần làm nằm ngoài ứng dụng (I-42).

    Google trả `403 VALIDATION_REQUIRED` kèm một `validation_url`; đăng nhập lại không chữa được.

    `action_url` đi **riêng** khỏi câu chữ (I-45). Nhét URL vào giữa câu thì giao diện chỉ còn
    cách in nguyên một chuỗi 300 ký tự ra màn hình — người dùng phải bôi đen rồi chép tay. Tách
    ra thì trang dựng được một chữ *đây* bấm thẳng.
    """

    def __init__(self, message: str, *, action_url: str = "") -> None:
        super().__init__(message)
        self.action_url = action_url


class LLMBlockedError(LLMError):
    """Provider chặn câu trả lời (safety / recitation) — thử lại y nguyên cũng vô ích."""


class LLMInvalidModelError(LLMError):
    """`settings.llm_model` không có trong channel đang dùng (I-03).

    Đối chiếu danh mục thật bằng `CliProxyClient.model_ids()`.
    """


async def generate_text(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    temperature: float = 0.2,
    max_output_tokens: int | None = None,
    tools: list[dict[str, Any]] | None = None,
    client: CliProxyClient | None = None,
) -> str:
    """Sinh văn bản thuần. Dùng cho nút "Kiểm tra kết nối", enrichment và chat.

    `temperature` để thấp vì mọi chỗ dùng trong dự án đều là trích xuất/tổng hợp có cấu trúc.

    `tools` đi thẳng vào payload `generateContent`; F2 bật tra cứu Internet bằng
    `tools=[TOOL_GOOGLE_SEARCH]`. **Hàm này chỉ trả về text** — cần URL nguồn thì gọi
    `generate_content()` để lấy JSON thô.
    """
    parts: list[dict[str, Any]] = [{"text": prompt}]
    payload = _build_payload(
        parts,
        system=system,
        temperature=temperature,
        max_tokens=max_output_tokens,
        tools=tools,
    )
    data = await generate_content(payload, model=model, client=client)
    return _extract_text(data)


async def generate_vision(
    prompt: str,
    image_bytes: bytes,
    *,
    mime_type: str = "image/jpeg",
    system: str | None = None,
    model: str | None = None,
    temperature: float = 0.0,
    max_output_tokens: int | None = None,
    tools: list[dict[str, Any]] | None = None,
    client: CliProxyClient | None = None,
) -> str:
    """Sinh văn bản từ **ảnh + prompt** — trái tim của F1.

    Ảnh đi trong `inline_data` dạng base64 và để **trước** câu lệnh: Gemini đọc phần tử theo thứ
    tự, model "nhìn" rồi mới đọc yêu cầu cho kết quả ổn định hơn.

    `temperature=0.0`: OCR là trích xuất, mọi mức sáng tạo đều là bịa (R3).
    """
    if mime_type not in SUPPORTED_IMAGE_TYPES:
        raise LLMError(
            f"Định dạng ảnh {mime_type!r} không được hỗ trợ. "
            f"Chấp nhận: {', '.join(sorted(SUPPORTED_IMAGE_TYPES))}."
        )
    if not image_bytes:
        raise LLMError("Ảnh rỗng — không có gì để gửi cho model.")

    parts: list[dict[str, Any]] = [
        {
            "inline_data": {
                "mime_type": mime_type,
                "data": base64.b64encode(image_bytes).decode("ascii"),
            }
        },
        {"text": prompt},
    ]
    payload = _build_payload(
        parts,
        system=system,
        temperature=temperature,
        max_tokens=max_output_tokens,
        tools=tools,
    )
    data = await generate_content(payload, model=model, client=client)
    return _extract_text(data)


async def generate_content(
    payload: dict[str, Any],
    *,
    model: str | None = None,
    client: CliProxyClient | None = None,
) -> dict[str, Any]:
    """Lớp thấp: POST thẳng payload Gemini native, trả JSON thô.

    Dùng khi cần đọc thêm `usageMetadata` hoặc tự dựng payload lạ. Retry do
    `CliProxyClient.request()` lo; 4xx không retry.
    """
    model_name = model or settings.llm_model
    path = f"/v1beta/models/{model_name}:generateContent"

    async def _call(proxy: CliProxyClient) -> dict[str, Any]:
        return await proxy.request_json(
            "POST",
            path,
            management=False,
            json=payload,
            timeout=LLM_TIMEOUT,
        )

    # Đo quanh **cả** nhánh hỏng, không chỉ nhánh chạy được: lời gọi treo 120 giây rồi timeout là
    # con số đáng ghi nhất trong cả file log, mà ghi sau `return` thì không bao giờ thấy nó.
    started = time.perf_counter()
    try:
        if client is not None:
            data = await _call(client)
        else:
            async with CliProxyClient(timeout=LLM_TIMEOUT) as proxy:
                data = await _call(proxy)
    except CliProxyError as exc:
        log_llm_call(model_name, time.perf_counter() - started, None, error=type(exc).__name__)
        raise await _translate_error(model_name, exc, client) from exc

    log_llm_call(model_name, time.perf_counter() - started, data.get("usageMetadata"))
    return data


# --------------------------------------------------------------------------- nội bộ


async def _translate_error(
    model_name: str,
    exc: CliProxyError,
    client: CliProxyClient | None,
) -> LLMError:
    """Dịch lỗi tầng CLIProxy thành lỗi tầng LLM — bảng bốn dòng ở đầu file.

    Tách khỏi `generate_content()` để đúng một chỗ ghi log thời gian gọi dùng được cho cả nhánh
    chạy được lẫn bốn nhánh hỏng.
    """
    if isinstance(exc, CliProxyAuthError):
        # **Hỏi lý do trước khi kết luận** (I-45). `401/403` gộp hai chuyện khác hẳn nhau: token
        # thật sự hỏng (đăng nhập lại là xong) và Google chặn tài khoản chờ xác thực (đăng nhập
        # lại **không** chữa được gì). Đo 2026-09-29: lần gọi **đầu tiên** của một tài khoản chưa
        # xác thực rơi thẳng vào đây, nên nó nhận đúng câu sai đường — chỉ từ lần thứ hai, khi
        # CLIProxy đã đánh dấu credential `unavailable` và chuyển sang trả `503`, mới ra câu đúng.
        return await _explain_blocked_or_stale(model_name, client)
    if isinstance(exc, CliProxyNoCredentialError):
        # Nguyên văn của CLIProxy kèm cả body 401 của Google — hữu ích khi debug, rối trên UI.
        logger.info("CLIProxy báo thiếu credential: %s", exc.message)
        return await _explain_no_credential(model_name, client)
    if isinstance(exc, CliProxyResponseError):
        loi = str(exc.message).lower()
        if exc.status_code == 400 and "unknown provider for model" in loi:
            return await _explain_unknown_provider(model_name, exc, client)
        if exc.status_code == 400 and "missing project_id" in loi:
            # Nguyên văn (`antigravity auth missing project_id: no project_id in response`) nói
            # đúng chuyện gì hỏng nhưng không nói **phải làm gì**, mà đây là lỗi người dùng tự
            # gỡ được trong một phút — chỉ cần biết đường (I-45, TS-02).
            return LLMNotConnectedError(
                "Tài khoản Google này không dùng được với AI: Google không cấp `project_id` "
                "cho nó. Thường gặp với tài khoản do trường hay công ty cấp. Vào /settings bấm "
                "'Ngắt kết nối' rồi kết nối lại bằng một tài khoản Gmail cá nhân."
            )
        return LLMError(f"CLIProxy từ chối lời gọi model: {exc.message}")
    return LLMError(f"Không gọi được model qua CLIProxy: {exc.message}")


async def _explain_unknown_provider(
    model_name: str,
    exc: CliProxyResponseError,
    client: CliProxyClient | None,
) -> LLMError:
    """Dịch `400 unknown provider for model …` thành đúng nguyên nhân.

    **CLIProxy trả y hệt một câu cho hai sự cố hoàn toàn khác nhau**: model sai tên, và model đúng
    tên nhưng chưa có credential nào. Lý do: nó định tuyến theo *client đã nạp*, không có
    credential thì cũng chẳng có client nào nhận model đó.

    Phân biệt bằng danh mục model của channel: có trong danh mục ⇒ **chưa kết nối**, bảo người
    dùng bấm nút OAuth; không có ⇒ đúng là **sai `LLM_MODEL`** (I-03). Đoán nhầm ở đây tốn của
    người dùng cả buổi sửa nhầm `.env` trong khi chỉ cần bấm một nút.
    """
    channel = settings.cliproxy_auth_provider
    known = await _model_in_catalogue(model_name, client)

    if known is True:
        return LLMNotConnectedError(
            f"Model {model_name!r} có thật trong channel {channel!r} nhưng CLIProxy chưa nạp "
            "credential nào phục vụ được nó. Vào /settings bấm 'Kết nối AI'."
        )
    if known is False:
        return LLMInvalidModelError(
            f"Model {model_name!r} không có trong channel {channel!r}. Xem danh mục thật bằng "
            f"GET /v0/management/model-definitions/{channel} rồi sửa LLM_MODEL trong .env "
            f"(I-03). Chi tiết: {exc.message}"
        )
    return LLMError(
        f"CLIProxy không định tuyến được model {model_name!r}, và cũng không đọc được danh mục "
        f"model để biết vì sao. Hai khả năng: chưa kết nối OAuth (vào /settings), hoặc LLM_MODEL "
        f"sai tên (I-03). Chi tiết: {exc.message}"
    )


async def _explain_blocked_or_stale(model_name: str, client: CliProxyClient | None) -> LLMError:
    """`401/403` từ CLIProxy: token hỏng, hay Google đang chặn tài khoản? (I-45)

    Hai câu trả lời dẫn tới hai việc khác hẳn nhau, nên phải đọc `auth-files` mới biết. Không
    đọc được thì rơi về câu chung — đoán bừa "tài khoản bị chặn" khi thật ra chỉ hết hạn token
    là đẩy người dùng đi tìm một trang xác thực không tồn tại.
    """
    blocked = await _provider_block(client)
    if blocked is not None:
        return blocked
    return LLMNotConnectedError(
        "CLIProxy từ chối credential đang có (401/403) — token hỏng hoặc hết hạn. "
        "Vào /settings bấm 'Kết nối AI' để đăng nhập lại."
    )


async def _provider_block(client: CliProxyClient | None) -> LLMError | None:
    """Credential nào đang bị **nhà cung cấp** chặn? `None` nếu không có, hoặc không tra được."""
    try:
        if client is not None:
            files = await client.auth_files(settings.cliproxy_auth_provider)
        else:
            async with CliProxyClient() as proxy:
                files = await proxy.auth_files(settings.cliproxy_auth_provider)
    except CliProxyError as exc:
        logger.info("Không đọc được auth-files để giải thích lỗi: %s", exc)
        return None

    for auth_file in files:
        block = auth_file.provider_block
        if block is None:
            continue
        if block.needs_verification:
            return LLMProviderBlockedError(
                "Tài khoản của bạn chưa được Google xác thực.",
                action_url=block.action_url,
            )
        return LLMProviderBlockedError(
            f"Google từ chối tài khoản {auth_file.label}: {block.one_line} "
            "Vào /settings kết nối bằng tài khoản Google khác."
        )
    return None


async def _explain_no_credential(model_name: str, client: CliProxyClient | None) -> LLMError:
    """Dịch `503 auth_unavailable` thành đúng nguyên nhân — **ba chuyện rất khác nhau** (I-42).

    | Tình huống | Dấu hiệu | Việc người dùng phải làm |
    |------------|----------|--------------------------|
    | Chưa ai đăng nhập | `auth-files` rỗng | bấm *Kết nối AI* |
    | Google chặn tài khoản | `status_message` có lỗi | mở link xác thực — **đăng nhập lại vô ích** |
    | Model đang bị tạm ngừng | `cooldowns[…]` | đợi, hoặc chọn model khác |

    Gộp cả ba thành "chưa kết nối OAuth" là lời khuyên sai cho hai ca sau.

    Không tra được `auth-files` thì rơi về câu chung — đoán bừa còn tệ hơn.
    """
    channel = settings.cliproxy_auth_provider
    generic = LLMNotConnectedError(
        f"Chưa kết nối OAuth: CLIProxy không có credential nào cho channel {channel!r}. "
        "Vào /settings bấm 'Kết nối AI'."
    )

    # **Hỏi nhà cung cấp trước, hỏi CLIProxy sau**: `status_message` chép nguyên văn lỗi của
    # Google nên nói đúng chuyện gì xảy ra; `cooldowns[].reason` là nhãn CLIProxy tự đặt và nó
    # gắn `payment_required` cho **mọi** 403 upstream (I-42).
    blocked = await _provider_block(client)
    if blocked is not None:
        return blocked

    try:
        if client is not None:
            files = await client.auth_files(channel)
        else:
            async with CliProxyClient() as proxy:
                files = await proxy.auth_files(channel)
    except CliProxyError as exc:
        logger.info("Không đọc được auth-files để giải thích 503: %s", exc)
        return generic

    if not files:
        return generic

    bare = base_model(model_name)
    blocked_models = [(f, c) for f in files if (c := f.cooldown_for(bare)) is not None]
    if blocked_models:
        auth_file, cooldown = blocked_models[0]
        return LLMNotConnectedError(
            f"Model {bare!r} đang tạm ngừng với tài khoản {auth_file.label} "
            f"({cooldown.reason or 'không rõ lý do'}). Thử lại sau "
            f"{max(cooldown.remaining_seconds, 1)} giây, hoặc chọn model khác ở /settings."
        )

    return LLMNotConnectedError(
        f"CLIProxy có credential của {files[0].label} nhưng không phục vụ được model "
        f"{bare!r} lúc này. Thử lại sau ít phút, hoặc chọn model khác ở /settings."
    )


async def _model_in_catalogue(model_name: str, client: CliProxyClient | None) -> bool | None:
    """`True`/`False` nếu tra được danh mục channel, `None` nếu không tra nổi (đừng đoán bừa)."""
    try:
        if client is not None:
            ids = await client.model_ids()
        else:
            async with CliProxyClient() as proxy:
                ids = await proxy.model_ids()
    except CliProxyError as exc:
        logger.info("Không tra được danh mục model để giải thích lỗi 400: %s", exc)
        return None
    return base_model(model_name) in ids if ids else None


def base_model(model_name: str) -> str:
    return model_name.rsplit("/", 1)[-1]


def _build_payload(
    parts: list[dict[str, Any]],
    *,
    system: str | None,
    temperature: float,
    max_tokens: int | None,
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Dựng body chuẩn Gemini native `generateContent`."""
    generation_config: dict[str, Any] = {"temperature": temperature}
    if max_tokens is not None:
        generation_config["maxOutputTokens"] = max_tokens

    payload: dict[str, Any] = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": generation_config,
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    # Chỉ thêm khoá khi thật sự có tool: có provider coi mảng rỗng là lỗi.
    if tools:
        payload["tools"] = tools
    return payload


def _extract_text(data: dict[str, Any]) -> str:
    """Rút text từ response Gemini, phân biệt rõ "bị chặn" với "trả rỗng".

    Gộp mọi `parts[].text` của candidate đầu: model có lúc tách câu trả lời thành nhiều phần, lấy
    mỗi `parts[0]` sẽ cắt cụt câu trả lời — lỗi âm thầm.
    """
    feedback = data.get("promptFeedback")
    if isinstance(feedback, dict) and feedback.get("blockReason"):
        raise LLMBlockedError(f"Provider chặn ngay từ prompt: {feedback['blockReason']}.")

    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise LLMError(f"Response không có `candidates`: {_preview(data)}")

    candidate = candidates[0] if isinstance(candidates[0], dict) else {}
    finish_reason = str(candidate.get("finishReason") or "")
    if finish_reason in BLOCKED_FINISH_REASONS:
        raise LLMBlockedError(f"Câu trả lời bị chặn (finishReason={finish_reason}).")

    content = candidate.get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    texts = [
        str(part["text"])
        for part in (parts or [])
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    ]
    text = "".join(texts).strip()

    if not text:
        if finish_reason == "MAX_TOKENS":
            raise LLMError(
                "Model dừng vì chạm giới hạn token trước khi kịp trả chữ nào — "
                "tăng `max_output_tokens`."
            )
        raise LLMError(
            f"Model trả về nội dung rỗng (finishReason={finish_reason or 'không rõ'}): "
            f"{_preview(data)}"
        )
    return text


def _preview(data: Any, limit: int = 300) -> str:
    """Cắt ngắn response để nhét vào thông báo lỗi mà không làm ngập log."""
    text = repr(data)
    return text if len(text) <= limit else f"{text[:limit]}…"
