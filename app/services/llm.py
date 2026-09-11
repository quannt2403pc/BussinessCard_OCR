"""Client Gemini qua CLIProxy: generate_text / generate_vision. FILE DÙNG CHUNG — chỉ Q sửa.

Chủ sở hữu: Q | Task: 2.3 | xem Task.md

Đường đi: `app` → CLIProxy `POST /v1beta/models/<model>:generateContent` → provider
(channel `antigravity`, OAuth của người dùng). Payload theo chuẩn **Gemini native**.

Đã kiểm chứng trên container thật (v7.2.156, 2026-09-11) — bốn cách hỏng, ba cách trả lời:

| Tình huống | CLIProxy trả | Ném ra |
|------------|--------------|--------|
| Model sai tên | `400 unknown provider for model …` | `LLMInvalidModelError` |
| Model đúng, **chưa từng có credential** | `400 unknown provider for model …` ← y hệt dòng trên | `LLMNotConnectedError` |
| Model đúng, credential vừa bị xoá | `503 auth_unavailable: no auth available (providers=…)` | `LLMNotConnectedError` |
| Credential có nhưng token hỏng/hết hạn | `401 authentication_error` | `LLMNotConnectedError` |

Hai dòng đầu **giống hệt nhau** nhưng cách sửa trái ngược: một bên phải sửa `LLM_MODEL` trong
`.env`, một bên chỉ cần bấm nút OAuth. `_explain_unknown_provider()` phân biệt bằng cách tra
danh mục model của channel — đừng bỏ bước đó để "cho gọn".

Một điều nữa: route gọi model **không cần** management key (`api-keys: []` nghĩa là CLIProxy
không kiểm tra client). Ta cũng cố ý không gửi key ở đây để khỏi đụng bộ đếm ban của I-05.

**Không có `embed()`** — CLIProxy không có endpoint embedding, việc đó do service `embedder`
đảm nhiệm (Plan.md mục 2.6, `services/embeddings.py` task 6.4).
"""

from __future__ import annotations

import base64
import logging
from typing import Any

import httpx

from app.core.config import settings
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


class LLMError(RuntimeError):
    """Lỗi khi gọi LLM. Router bắt loại này là bắt được tất cả."""


class LLMNotConnectedError(LLMError):
    """Chưa kết nối OAuth, hoặc token hết hạn — gom ba dạng response ở bảng đầu file vào một lỗi.

    Gom lại vì cách xử lý y hệt nhau: mời người dùng bấm "Kết nối CLIProxy (OAuth)" ở
    `/settings`. Router chỉ cần bắt đúng loại này để hiện lời mời đó.
    """


class LLMBlockedError(LLMError):
    """Provider chặn câu trả lời (safety / recitation) — thử lại y nguyên cũng vô ích."""


class LLMInvalidModelError(LLMError):
    """`settings.llm_model` không có trong channel đang dùng (I-03).

    Đối chiếu danh mục thật: `CliProxyClient.model_ids()` hoặc
    `GET /v0/management/model-definitions/antigravity`.
    """


async def generate_text(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    temperature: float = 0.2,
    max_output_tokens: int | None = None,
    client: CliProxyClient | None = None,
) -> str:
    """Sinh văn bản thuần. Dùng cho nút "Kiểm tra kết nối" (2.4), enrichment và chat.

    `temperature` để thấp vì mọi chỗ dùng trong dự án đều là trích xuất/tổng hợp có cấu trúc,
    không phải viết sáng tạo.
    """
    parts: list[dict[str, Any]] = [{"text": prompt}]
    payload = _build_payload(
        parts, system=system, temperature=temperature, max_tokens=max_output_tokens
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
    client: CliProxyClient | None = None,
) -> str:
    """Sinh văn bản từ **ảnh + prompt** — trái tim của F1 (task 3.4 gọi hàm này).

    Ảnh đi trong `inline_data` dạng base64. Gemini đọc phần tử theo thứ tự nên để ảnh **trước**
    câu lệnh: model "nhìn" rồi mới đọc yêu cầu, cho kết quả ổn định hơn với danh thiếp.

    `temperature=0.0`: OCR là trích xuất, mọi mức sáng tạo đều là bịa (rủi ro R3).
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
        parts, system=system, temperature=temperature, max_tokens=max_output_tokens
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

    Dùng khi cần đọc thêm `usageMetadata` (logging token ở task 9.5) hoặc tự dựng payload lạ.
    Retry (mạng/429/5xx) do `CliProxyClient.request()` lo; 4xx không retry.
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

    try:
        if client is not None:
            return await _call(client)
        async with CliProxyClient(timeout=LLM_TIMEOUT) as proxy:
            return await _call(proxy)
    except CliProxyAuthError as exc:
        raise LLMNotConnectedError(
            "CLIProxy từ chối credential đang có (401/403) — token hỏng hoặc hết hạn. "
            "Vào /settings bấm 'Kết nối CLIProxy (OAuth)' để đăng nhập lại."
        ) from exc
    except CliProxyNoCredentialError as exc:
        # Nguyên văn của CLIProxy kèm cả body 401 của Google — hữu ích khi debug, nhưng dán lên
        # UI thì rối. Đẩy vào log, trả cho người dùng đúng một câu và một việc cần làm.
        logger.info("CLIProxy báo thiếu credential: %s", exc.message)
        raise LLMNotConnectedError(
            "Chưa kết nối OAuth: CLIProxy không có credential nào cho channel "
            f"{settings.cliproxy_auth_provider!r}. Vào /settings bấm 'Kết nối CLIProxy (OAuth)'."
        ) from exc
    except CliProxyResponseError as exc:
        if exc.status_code == 400 and "unknown provider for model" in str(exc.message).lower():
            raise await _explain_unknown_provider(model_name, exc, client) from exc
        raise LLMError(f"CLIProxy từ chối lời gọi model: {exc.message}") from exc
    except CliProxyError as exc:
        raise LLMError(f"Không gọi được model qua CLIProxy: {exc.message}") from exc


# --------------------------------------------------------------------------- nội bộ


async def _explain_unknown_provider(
    model_name: str,
    exc: CliProxyResponseError,
    client: CliProxyClient | None,
) -> LLMError:
    """Dịch `400 unknown provider for model …` thành đúng nguyên nhân.

    Đây là bẫy đo được trên container thật (2026-09-11, task 2.3): **CLIProxy trả y hệt một câu
    cho hai sự cố hoàn toàn khác nhau.**

    | Tình huống | Response |
    |------------|----------|
    | Model sai tên, có credential | `400 unknown provider for model gemini-flash-latest` |
    | Model đúng tên, **chưa có credential nào** | `400 unknown provider for model gemini-3-flash` |
    | Model đúng tên, credential hỏng/hết hạn | `401 authentication_error` |

    Lý do: CLIProxy định tuyến theo *client đã nạp*. Không có credential nào thì cũng chẳng có
    client nào nhận model đó → nó báo "unknown provider" y như khi gõ sai tên model.

    Phân biệt bằng danh mục model của channel: model có trong danh mục ⇒ lỗi là **chưa kết nối**,
    bảo người dùng bấm nút OAuth; không có trong danh mục ⇒ đúng là **sai `LLM_MODEL`** (I-03).
    Đoán nhầm ở đây tốn của người dùng cả buổi sửa nhầm `.env` trong khi chỉ cần bấm một nút.
    """
    channel = settings.cliproxy_auth_provider
    known = await _model_in_catalogue(model_name, client)

    if known is True:
        return LLMNotConnectedError(
            f"Model {model_name!r} có thật trong channel {channel!r} nhưng CLIProxy chưa nạp "
            "credential nào phục vụ được nó. Vào /settings bấm 'Kết nối CLIProxy (OAuth)'."
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
    return model_name in ids if ids else None


def _build_payload(
    parts: list[dict[str, Any]],
    *,
    system: str | None,
    temperature: float,
    max_tokens: int | None,
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
    return payload


def _extract_text(data: dict[str, Any]) -> str:
    """Rút text từ response Gemini, phân biệt rõ "bị chặn" với "trả rỗng".

    Gộp mọi `parts[].text` của candidate đầu: model có lúc tách câu trả lời thành nhiều phần
    (nhất là khi kèm ảnh), lấy mỗi `parts[0]` sẽ cắt cụt câu trả lời — lỗi âm thầm.
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
