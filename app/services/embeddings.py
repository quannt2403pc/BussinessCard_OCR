"""Client HTTP gọi service `embedder` — sinh vector cho Knowledge Base (F3).

Chủ sở hữu: Q | Task: 6.4

Là HTTP chứ không nạp model thẳng trong `api` vì CLIProxy **không có** endpoint embedding, còn
nhét `torch` vào image `api` thì mỗi lần rebuild kéo thêm ~2.5GB.

**Tiền tố `passage:` / `query:` do CHÍNH service embedder gắn** dựa vào trường `kind`. File này
chỉ truyền `kind` cho đúng và **tuyệt đối không tự thêm tiền tố** — thêm lần nữa thành
`passage: passage: …`, chất lượng truy hồi tụt mà không có lỗi nào báo ra.

Ba quy tắc còn lại:

- **Chỉ retry lỗi mạng và 429/5xx** — 4xx là ta gửi sai, thử lại vẫn sai y như vậy.
- **Kiểm số chiều trả về** — cột là `vector(384)` cố định; lệch chiều thì Postgres từ chối lúc
  `INSERT` với thông báo rất khó lần ra.
- **Không gọi trong lúc đang mở transaction DB** — một batch trên CPU mất vài giây.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any, Literal

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

Kind = Literal["passage", "query"]

#: Sinh vector trên CPU: một batch 32 đoạn mất cỡ vài giây, lần gọi đầu còn cộng thời gian nạp model.
EMBED_TIMEOUT = httpx.Timeout(60.0, connect=5.0)

#: `/health` chỉ đọc biến trong tiến trình — chậm hơn 10s nghĩa là service đang hỏng.
HEALTH_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

#: Số đoạn tối đa gửi trong MỘT request. Trần cứng của embedder là 64 — gửi quá là 422, không
#: phải lỗi tạm thời. Để 32 cho có biên.
MAX_BATCH = 32

#: Chỉ những mã này mới đáng thử lại. 4xx cố ý không có mặt.
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

DEFAULT_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 0.5


class EmbeddingError(RuntimeError):
    """Lỗi gốc khi sinh embedding. Router bắt loại này là bắt được tất cả."""


class EmbedderUnavailableError(EmbeddingError):
    """Không gọi được `embedder`: lỗi mạng, timeout, hoặc 5xx sau khi hết lượt thử.

    Thường gặp nhất khi container chưa lên — nhìn từ UI thì nó chỉ là "reindex hỏng".
    """


class EmbeddingDimError(EmbeddingError):
    """Vector trả về không đúng `EMBEDDING_DIM`.

    Lỗi cấu hình chứ không phải lỗi dữ liệu: image `embedder` đang chạy model khác với model mà
    cột `vector(n)` được tạo theo.
    """


async def embed(
    texts: Sequence[str],
    *,
    kind: Kind,
    client: httpx.AsyncClient | None = None,
) -> list[list[float]]:
    """Sinh vector cho nhiều đoạn, **giữ nguyên thứ tự đầu vào**.

    Tự chia thành nhiều request `MAX_BATCH` phần tử. Danh sách rỗng trả về danh sách rỗng mà
    không gọi mạng — reindex trên KB trống là chuyện bình thường, không phải lỗi.
    """
    if not texts:
        return []
    _reject_blank(texts)

    owns_client = client is None
    http = client or httpx.AsyncClient(base_url=base_url(), timeout=EMBED_TIMEOUT)
    vectors: list[list[float]] = []
    try:
        for start in range(0, len(texts), MAX_BATCH):
            batch = list(texts[start : start + MAX_BATCH])
            payload = await _request_json(
                http, "POST", "/embed", json={"texts": batch, "kind": kind}, timeout=EMBED_TIMEOUT
            )
            vectors.extend(_read_vectors(payload, expected=len(batch)))
    finally:
        if owns_client:
            await http.aclose()

    return vectors


async def embed_passages(
    texts: Sequence[str], *, client: httpx.AsyncClient | None = None
) -> list[list[float]]:
    """Vector cho văn bản **đem đi lưu** vào KB (`kind="passage"`)."""
    return await embed(texts, kind="passage", client=client)


async def embed_query(text: str, *, client: httpx.AsyncClient | None = None) -> list[float]:
    """Vector cho **câu hỏi của người dùng** (`kind="query"`).

    Tách hàm riêng thay vì bắt chỗ gọi tự nhớ `kind`: nhầm `passage` cho câu hỏi không gây lỗi
    nào, chỉ làm kết quả tìm kiếm tệ đi một cách khó truy ra.
    """
    vectors = await embed([text], kind="query", client=client)
    return vectors[0]


async def health(*, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """`GET /health` của embedder: `{"status","model","dim","max_seq_length"}`.

    Dùng để báo lỗi sớm và rõ thay vì để người dùng chờ hết một lượt embed mới biết service chưa
    sẵn sàng.
    """
    owns_client = client is None
    http = client or httpx.AsyncClient(base_url=base_url(), timeout=HEALTH_TIMEOUT)
    try:
        payload = await _request_json(http, "GET", "/health", json=None, timeout=HEALTH_TIMEOUT)
    finally:
        if owns_client:
            await http.aclose()

    dim = payload.get("dim")
    if isinstance(dim, int) and dim != settings.embedding_dim:
        raise EmbeddingDimError(_dim_message(dim))
    return payload


# --------------------------------------------------------------------------- nội bộ


def base_url() -> str:
    """URL gốc của service embedder, đã bỏ dấu `/` thừa.

    Công khai có chủ đích: `routers/kb.py` dựng sẵn một `AsyncClient` dùng chung nên cần URL này.
    """
    return settings.embedder_url.rstrip("/")


def _reject_blank(texts: Sequence[str]) -> None:
    """Chặn chuỗi rỗng trước khi gửi.

    Embedder trả 422 cho chuỗi rỗng; bắt ở đây thì thông báo nói được chỉ số phần tử hỏng.
    """
    for index, text in enumerate(texts):
        if not text or not text.strip():
            raise EmbeddingError(
                f"Đoạn văn bản thứ {index} rỗng — embedder từ chối cả batch. "
                "Lọc đoạn trắng trước khi gọi (xem services/kb.py)."
            )


async def _request_json(
    http: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    json: Any,
    timeout: httpx.Timeout,
    attempts: int = DEFAULT_ATTEMPTS,
) -> dict[str, Any]:
    """Gọi embedder, retry lỗi mạng/429/5xx với backoff 0.5s → 1s, rồi trả JSON."""
    url = path if http.base_url else f"{base_url()}{path}"
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = await http.request(method, url, json=json, timeout=timeout)
        except httpx.HTTPError as exc:  # timeout, DNS, connect refused
            last_error = exc
            logger.warning("embedder %s %s lỗi mạng (lần %d): %s", method, path, attempt, exc)
        else:
            if response.status_code < 400:
                return _safe_json(response)
            if response.status_code not in RETRY_STATUS:
                raise EmbeddingError(
                    f"embedder trả {response.status_code} cho {method} {path}: "
                    f"{_preview(response.text)}"
                )
            last_error = EmbedderUnavailableError(
                f"embedder trả {response.status_code}: {_preview(response.text)}"
            )
            logger.warning(
                "embedder %s %s trả %d (lần %d)", method, path, response.status_code, attempt
            )

        if attempt < attempts:
            await asyncio.sleep(BACKOFF_BASE_SECONDS * attempt)

    raise EmbedderUnavailableError(
        f"Không gọi được embedder tại {base_url()} sau {attempts} lượt thử ({last_error}). "
        "Kiểm tra `docker compose ps embedder` — lần build đầu phải kéo torch và nhúng model "
        "nên container mất vài phút mới healthy."
    ) from last_error


def _read_vectors(payload: dict[str, Any], *, expected: int) -> list[list[float]]:
    """Đọc `{"vectors":[…],"dim":n}` và kiểm cả số lượng lẫn số chiều.

    Kiểm số lượng vì vector lệch chỉ số so với đoạn văn là hỏng câm: KB vẫn đầy dữ liệu, chỉ có
    điều mỗi đoạn mang vector của đoạn khác.
    """
    vectors = payload.get("vectors")
    if not isinstance(vectors, list) or len(vectors) != expected:
        got = len(vectors) if isinstance(vectors, list) else "không phải danh sách"
        raise EmbeddingError(f"embedder trả {got} vector cho {expected} đoạn văn bản.")

    dim = payload.get("dim")
    if isinstance(dim, int) and dim != settings.embedding_dim:
        raise EmbeddingDimError(_dim_message(dim))
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != settings.embedding_dim:
            raise EmbeddingDimError(_dim_message(len(vector) if isinstance(vector, list) else -1))
    return [[float(value) for value in vector] for vector in vectors]


def _dim_message(actual: int) -> str:
    return (
        f"embedder trả vector {actual} chiều nhưng cột kb_chunks.embedding là "
        f"vector({settings.embedding_dim}). Hai bên phải khớp: đặt lại EMBEDDING_MODEL về "
        f"{settings.embedding_model!r} rồi `docker compose build embedder`, hoặc báo Q sinh "
        "Alembic revision đổi kiểu cột (Plan.md mục 2.6)."
    )


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise EmbeddingError(
            f"embedder trả về thứ không phải JSON: {_preview(response.text)}"
        ) from exc
    if not isinstance(data, dict):
        raise EmbeddingError(f"embedder trả về {type(data).__name__}, cần một object JSON.")
    return data


def _preview(text: str, limit: int = 200) -> str:
    text = text.strip().replace("\n", " ")
    return text if len(text) <= limit else f"{text[:limit]}…"
