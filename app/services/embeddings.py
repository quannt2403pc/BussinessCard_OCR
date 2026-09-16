"""Client HTTP gọi service `embedder` — sinh vector cho Knowledge Base (F3).

Chủ sở hữu: Q | Task: 6.4 | xem Task.md

Vì sao là HTTP chứ không nạp model thẳng trong `api`: CLIProxy **không có** endpoint embedding
(đã kiểm chứng mã nguồn — Plan.md mục 2.6), còn nhét `torch` vào image `api` thì mỗi lần rebuild
kéo thêm ~2.5GB. Hợp đồng `POST /embed` đã chốt ở Plan.md mục 2.6, service do T dựng (task 3.10).

**Tiền tố `passage:` / `query:` do CHÍNH service embedder gắn**, dựa vào trường `kind` trong
body (`embedder/main.py::with_prefix`). File này chỉ truyền `kind` cho đúng và **tuyệt đối không
tự thêm tiền tố** — thêm lần nữa thành `passage: passage: …`, chất lượng truy hồi tụt mà không
có lỗi nào báo ra. Đây đúng là loại hỏng âm thầm mà Plan.md mục 2.6 cảnh báo.

Ba quy tắc còn lại, cùng lý do với `cliproxy_client.py`:

- **Chỉ retry lỗi mạng và 429/5xx.** 4xx là ta gửi sai (chuỗi rỗng, quá 64 phần tử) — thử lại
  vẫn sai y như vậy, chỉ tốn thời gian của người đang ngồi chờ.
- **Kiểm số chiều trả về.** Cột `kb_chunks.embedding` là `vector(384)` cố định; embedder chạy
  model khác chiều thì Postgres từ chối lúc `INSERT` với thông báo rất khó lần ra. Bắt ngay tại
  đây, kèm câu chỉ rõ phải làm gì.
- **Không gọi trong lúc đang mở transaction DB.** Một batch trên CPU mất vài giây; giữ
  transaction suốt thời gian đó là bài học đã trả giá ở task 5.4.
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

#: Sinh vector trên CPU: một batch 32 đoạn mất cỡ vài giây, lần gọi đầu sau khi container lên
#: còn cộng thêm thời gian nạp model. 60s là rộng rãi mà vẫn không treo request vô hạn.
EMBED_TIMEOUT = httpx.Timeout(60.0, connect=5.0)

#: `/health` chỉ đọc biến trong tiến trình — chậm hơn 10s nghĩa là service đang hỏng.
HEALTH_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

#: Số đoạn tối đa gửi trong MỘT request. Trần cứng của embedder là 64
#: (`embedder/main.py::MAX_TEXTS`) — gửi quá là 422, không phải lỗi tạm thời.
#: Để 32 cho có biên: batch to hơn không nhanh hơn đáng kể (bên kia vẫn chia nhỏ 16/lượt)
#: mà lại dễ chạm timeout hơn.
MAX_BATCH = 32

#: Chỉ những mã này mới đáng thử lại. 4xx cố ý không có mặt.
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

DEFAULT_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 0.5


class EmbeddingError(RuntimeError):
    """Lỗi gốc khi sinh embedding. Router bắt loại này là bắt được tất cả."""


class EmbedderUnavailableError(EmbeddingError):
    """Không gọi được `embedder`: lỗi mạng, timeout, hoặc 5xx sau khi hết lượt thử.

    Thường gặp nhất khi container chưa lên — lần build đầu kéo torch + nhúng model mất vài
    phút. Thông báo phải nói được điều đó, vì nhìn từ UI thì nó chỉ là "reindex hỏng".
    """


class EmbeddingDimError(EmbeddingError):
    """Vector trả về không đúng `EMBEDDING_DIM`.

    Là lỗi cấu hình/triển khai, không phải lỗi dữ liệu: image `embedder` đang chạy model khác
    với model mà cột `vector(n)` được tạo theo. Sửa bằng cách đổi lại `EMBEDDING_MODEL` hoặc
    nhờ Q sinh revision đổi kiểu cột (quy ước số 5) — xem Plan.md mục 2.6.
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
    """Vector cho **câu hỏi của người dùng** (`kind="query"`) — dùng ở task 7.1.

    Tách hàm riêng thay vì bắt chỗ gọi tự nhớ `kind`: nhầm `passage` cho câu hỏi không gây lỗi
    nào, chỉ làm kết quả tìm kiếm tệ đi một cách khó truy ra.
    """
    vectors = await embed([text], kind="query", client=client)
    return vectors[0]


async def health(*, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """`GET /health` của embedder: `{"status","model","dim","max_seq_length"}`.

    Dùng để báo lỗi sớm và rõ (endpoint reindex ở task 6.4 gọi trước khi làm gì khác) thay vì
    để người dùng chờ hết một lượt embed mới biết service chưa sẵn sàng.
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

    Công khai có chủ đích: `routers/kb.py` dựng sẵn một `AsyncClient` dùng chung cho cả lượt
    reindex nên cần URL này. Để dưới dấu `_` rồi bắt module khác chép lại chuỗi cấu hình đúng
    là cái bẫy đã ghi ở I-17.
    """
    return settings.embedder_url.rstrip("/")


def _reject_blank(texts: Sequence[str]) -> None:
    """Chặn chuỗi rỗng trước khi gửi.

    Embedder trả 422 cho chuỗi rỗng (`embedder/main.py::reject_blank_texts`). Bắt ở đây thì
    thông báo nói được chỉ số phần tử hỏng — đọc 422 của FastAPI để suy ra chỗ đó tốn thời gian
    hơn nhiều, mà lỗi này gần như luôn do khâu chunk ở `services/kb.py` sinh ra đoạn trắng.
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

    Kiểm số lượng vì vector lệch chỉ số so với đoạn văn là hỏng câm: KB vẫn đầy dữ liệu, chỉ
    có điều mỗi đoạn mang vector của đoạn khác, và không có cách nào phát hiện ra ngoài việc
    thấy trợ lý trả lời sai lung tung.
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
