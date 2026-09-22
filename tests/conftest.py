"""Fixture dùng chung: DB Postgres thật (trong transaction rollback) + mock CLIProxy/embedder.

Chủ sở hữu: Q | Task: 6.1, 12.8 (người dùng + client đã đăng nhập) | xem Task.md

Ba nguyên tắc, mỗi cái đổi lấy một loại lỗi đã gặp thật trong dự án:

1. **DB thật, không SQLite.** Nửa số thứ dự án dùng không tồn tại trong SQLite: `vector(384)`,
   `JSONB`, `ARRAY(TEXT)`, index một phần của `enrich_job_items`. Test trên SQLite là test một
   hệ khác với hệ chạy thật.
2. **Mỗi test một transaction, cuối test rollback.** Session gắn vào một connection đang mở
   transaction (`join_transaction_mode="create_savepoint"`), nên `commit()` trong mã ứng dụng
   vẫn chạy đúng đường của nó mà DB sau test vẫn sạch — không test nào nhìn thấy rác của test
   khác.
3. **Không có DB thì SKIP, không FAIL.** Máy chưa bật Docker vẫn phải chạy được phần test
   thuần (parse JSON, chunk, chuẩn hoá). Fail ở đó chỉ dạy người ta thói quen bỏ qua màu đỏ.

Chạy phần cần DB ở máy (container `db` đã publish cổng 5432):

    TEST_DATABASE_URL=postgresql+psycopg://bizcard:change-me@localhost:5432/bizcard_test pytest

Tạo sẵn database test một lần:

    docker compose exec db createdb -U bizcard bizcard_test
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import pytest
import respx
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401  — nạp đủ bảng vào Base.metadata trước khi create_all
from app.core.config import settings
from app.core.db import Base, get_db
from app.models.user import User
from app.services import auth

# Windows: Python 3.12 mặc định dùng `ProactorEventLoop`, mà psycopg v3 ở chế độ async **không
# chạy được trên loop đó** (`InterfaceError: Psycopg cannot use the 'ProactorEventLoop'`). Ứng
# dụng thật chạy trong container Linux nên không gặp, nhưng pytest ở máy Windows thì gặp ngay —
# đặt policy trước khi pytest-asyncio tạo loop là cách sửa gọn nhất, và không ảnh hưởng CI.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

#: Đường dẫn gọi model của CLIProxy (Plan.md mục 2.4). Trùng chuỗi `services/llm.py` dựng.
GENERATE_PATH = f"/v1beta/models/{settings.llm_model}:generateContent"

#: Độ dài chuỗi model giả lập trả về khi test không quan tâm nội dung.
_MAX_SEQ_LENGTH = 512

#: Mật khẩu của mọi người dùng do fixture tạo ra (task 12.8). Đủ luật của `schemas/user.py`.
TEST_PASSWORD = "matkhau123"


# --------------------------------------------------------------------------- DB thật


def candidate_database_urls() -> list[str]:
    """Các URL sẽ thử, theo thứ tự ưu tiên.

    `TEST_DATABASE_URL` thắng tất cả. Không có thì suy từ `DATABASE_URL`: đổi tên database
    thành `<tên>_test` — **không bao giờ chạy test trên DB đang dùng thật**, vì `create_all()`
    và dữ liệu test sẽ nằm chung chỗ với danh thiếp đã quét.

    Thêm biến thể `localhost` khi host là `db`: bên trong mạng Docker thì `db` phân giải được,
    chạy pytest ở máy thì không — mà container `db` có publish cổng 5432 ra ngoài nên cùng một
    DB đó vẫn tới được qua `localhost`.
    """
    explicit = os.environ.get("TEST_DATABASE_URL", "").strip()
    if explicit:
        return [explicit]

    url = make_url(settings.database_url)
    if url.database and not url.database.endswith("_test"):
        url = url.set(database=f"{url.database}_test")

    urls = [url.render_as_string(hide_password=False)]
    if url.host == "db":
        urls.append(url.set(host="localhost").render_as_string(hide_password=False))
    return urls


def _reachable(url: str) -> bool:
    engine = create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def database_url() -> str:
    """URL của DB test đang dùng được, hoặc skip cả nhóm test cần DB."""
    for url in candidate_database_urls():
        if _reachable(url):
            return url

    tried = ", ".join(_hide_password(url) for url in candidate_database_urls())
    pytest.skip(
        "Không kết nối được Postgres để chạy test có DB (đã thử: "
        f"{tried}). Bật `docker compose up -d db`, tạo DB test bằng "
        "`docker compose exec db createdb -U bizcard bizcard_test`, hoặc đặt TEST_DATABASE_URL."
    )


@pytest.fixture(scope="session")
def db_schema(database_url: str) -> str:
    """Dựng extension `vector` + toàn bộ bảng một lần cho cả phiên test. Trả về URL đã sẵn sàng.

    Dùng engine **đồng bộ** cho phần DDL này có lý do thực dụng: fixture phạm vi `session` mà
    lại `async` thì nó chạy trên một event loop khác với loop của từng test, và mọi connection
    mở trong đó sẽ nổ "attached to a different loop". Phần async chỉ nằm ở `db_session`
    (phạm vi từng test).

    `TRUNCATE` một lần ở đầu phiên: mỗi test vốn đã tự rollback, nhưng một lượt chạy bị ngắt
    giữa chừng (hoặc một script thử tay) có thể để lại dữ liệu đã commit, và test nào đếm số
    bản ghi sẽ đỏ vì lý do chẳng liên quan gì tới nó. Chỉ chạy trên database **test** —
    `candidate_database_urls()` không bao giờ trỏ vào DB đang dùng thật.

    Không `drop_all()` ở cuối: lượt chạy sau lại phải dựng lại từ đầu (gồm cả index `ivfflat`)
    mà chẳng được gì.
    """
    engine = create_engine(database_url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            Base.metadata.create_all(connection)
            tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
            connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    finally:
        engine.dispose()
    return database_url


@pytest.fixture
async def db_session(db_schema: str) -> AsyncIterator[AsyncSession]:
    """Session cho một test, **luôn rollback** khi test kết thúc.

    `join_transaction_mode="create_savepoint"`: mã ứng dụng gọi `commit()` thoải mái (repository
    của cả Q lẫn T đều commit), nó chỉ giải phóng savepoint bên trong transaction ngoài — thứ
    mà fixture này rollback ngay sau đó.
    """
    engine = create_async_engine(db_schema, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(
                bind=connection,
                join_transaction_mode="create_savepoint",
                expire_on_commit=False,
            )
            try:
                yield session
            finally:
                await session.close()
                if transaction.is_active:
                    await transaction.rollback()
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------- người dùng (12.8)


@lru_cache(maxsize=1)
def test_password_hash() -> str:
    """Băm Argon2 của `TEST_PASSWORD`, tính **một lần cho cả phiên test**.

    Argon2 cố ý chậm (~50ms/lượt). Băm lại ở mỗi test cần `user_a` + `user_b` là cộng thêm cả
    chục giây vào một bộ 400 test mà không kiểm thêm được gì: bản thân việc băm đã có
    `tests/test_auth.py` của T lo. Ở đây mật khẩu chỉ cần **thật** đủ để đăng nhập được.
    """
    return auth.hash_password(TEST_PASSWORD)


async def make_user(
    db: AsyncSession,
    email: str,
    display_name: str | None = None,
    *,
    user_id: uuid.UUID | None = None,
) -> User:
    """Một người dùng thật trong DB, mật khẩu là `TEST_PASSWORD`.

    `user_id` để chỉ định trước id: các file test đã có sẵn một hằng chủ sở hữu dùng chung cho
    những object ORM được dựng ở tầng module (`OWNER_ID`), mà khoá ngoại `user_id` đòi hàng
    `users` đó **tồn tại thật** — nên phải tạo được đúng id ấy chứ không nhận một id ngẫu nhiên.
    """
    user = User(
        id=user_id or uuid.uuid4(),
        email=email,
        password_hash=test_password_hash(),
        display_name=display_name,
    )
    db.add(user)
    await db.flush()
    return user


@pytest.fixture
async def user_a(db_session: AsyncSession) -> User:
    """Người dùng A — chủ sở hữu mặc định của dữ liệu trong test một-người-dùng."""
    return await make_user(db_session, "a@example.com", "Người dùng A")


@pytest.fixture
async def user_b(db_session: AsyncSession) -> User:
    """Người dùng B — dùng cho các ca tách dữ liệu của task 12.7 (tiêu chí A9)."""
    return await make_user(db_session, "b@example.com", "Người dùng B")


class _SessionHandle:
    """`async with` trả về đúng session của test và **không đóng** nó.

    `core/security.RequireLoginMiddleware` tự mở session bằng `SessionLocal()` vì nó chạy trước
    khi FastAPI giải dependency — nên `dependency_overrides[get_db]` không với tới được nó. Để
    nguyên thì middleware đọc DB qua một connection khác, không thấy hàng `users` đang nằm trong
    transaction chưa commit của test, và **mọi** request trong test đều bị đá về `/auth/login`.
    Lớp này là chỗ để `app_client` trỏ `SessionLocal` của middleware vào đúng session ấy.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


@asynccontextmanager
async def api_client(
    db_session: AsyncSession, user: User | None = None
) -> AsyncIterator[httpx.AsyncClient]:
    """Client gọi **app thật** với tư cách `user` (`None` = khách chưa đăng nhập).

    Dùng `app.main.app` chứ không dựng một `FastAPI()` rỗng: phần đáng kiểm nhất của 12.4 nằm ở
    dây nối — middleware chặn, thứ tự middleware, context processor của template — mà app rỗng
    thì không có gì trong số đó.

    Cookie ký bằng đúng `services/auth.sign_session()` của T, không dựng tay: phiên phải hết hiệu
    lực khi người dùng đổi mật khẩu, và chỉ hàm đó biết dấu vân tay ấy tính thế nào.

    `follow_redirects=False`: bị chặn thì test phải **thấy** đúng cái `303`, chứ không lặng lẽ đi
    theo nó rồi khẳng định về nội dung trang đăng nhập.
    """
    from app.core import security
    from app.main import app

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    original_factory = security.SessionLocal
    security.SessionLocal = lambda: _SessionHandle(db_session)  # type: ignore[assignment]
    app.dependency_overrides[get_db] = override_get_db
    cookies = {auth.SESSION_COOKIE: auth.sign_session(user)} if user is not None else {}
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            cookies=cookies,
            follow_redirects=False,
        ) as client:
            yield client
    finally:
        security.SessionLocal = original_factory  # type: ignore[assignment]
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
async def app_client(
    db_session: AsyncSession,
) -> AsyncIterator[Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]]:
    """Fixture gói `api_client` lại: `async with app_client(user_a) as http: …`.

    Có cả hai dạng là cố ý: fixture cho test viết mới (12.7 của T), còn hàm `api_client` cho các
    helper module-level đã tồn tại từ D8 (`tests/test_rag_chat.py`) khỏi phải đổi cấu trúc.
    """

    def factory(user: User | None = None) -> AbstractAsyncContextManager[httpx.AsyncClient]:
        return api_client(db_session, user)

    yield factory


# --------------------------------------------------------------------------- mock CLIProxy


@dataclass
class CliProxyStub:
    """Điều khiển CLIProxy giả lập trong một test.

    Mock ở tầng **HTTP** chứ không monkeypatch `services/llm.py`: lỗi hay gặp nhất với CLIProxy
    không phải ở chỗ ta gọi sai hàm, mà ở hình dạng response (bọc ```json — I-15, báo lỗi bằng
    HTTP 200 — I-02). Chỉ mock HTTP mới kiểm được những thứ đó.
    """

    router: respx.MockRouter

    def reply(self, *texts: str) -> None:
        """Model trả lần lượt từng chuỗi cho từng lời gọi `generateContent`.

        Truyền nhiều chuỗi để dựng kịch bản thử lại: `stub.reply("xin chào", "{…}")` nghĩa là
        lượt đầu hỏng, lượt sau mới ra JSON.
        """
        self.router.post(GENERATE_PATH).mock(
            side_effect=[httpx.Response(200, json=gemini_payload(text)) for text in texts]
        )

    def reply_payload(self, *payloads: dict[str, Any]) -> None:
        """Trả nguyên văn JSON của Gemini — dùng khi cần `finishReason`, `groundingMetadata`…"""
        self.router.post(GENERATE_PATH).mock(
            side_effect=[httpx.Response(200, json=payload) for payload in payloads]
        )

    def fail(self, status_code: int, json: Any = None) -> None:
        """CLIProxy trả lỗi HTTP (400 `unknown provider`, 503 `auth_unavailable`…)."""
        self.router.post(GENERATE_PATH).mock(
            return_value=httpx.Response(status_code, json=json or {"error": "lỗi giả lập"})
        )

    def auth_files(self, *names: str) -> None:
        """`GET /v0/management/auth-files` — nguồn sự thật của badge kết nối (I-02)."""
        files = [
            {
                "name": name,
                "provider": settings.cliproxy_auth_provider,
                "label": name,
                "status": "active",
                "disabled": False,
            }
            for name in names
        ]
        self.router.get("/v0/management/auth-files").mock(
            return_value=httpx.Response(200, json={"files": files})
        )

    @property
    def calls(self) -> Any:
        return self.router.calls


def gemini_payload(text: str) -> dict[str, Any]:
    """Response `generateContent` tối thiểu mà `llm._extract_text()` đọc được."""
    return {
        "candidates": [
            {"content": {"parts": [{"text": text}], "role": "model"}, "finishReason": "STOP"}
        ]
    }


@pytest.fixture
def cliproxy() -> Iterator[CliProxyStub]:
    """CLIProxy giả lập tại đúng `CLIPROXY_BASE_URL` mà `services/llm.py` sẽ gọi."""
    with respx.mock(base_url=settings.cliproxy_base_url, assert_all_called=False) as router:
        yield CliProxyStub(router)


# --------------------------------------------------------------------------- mock embedder


@dataclass
class EmbedderStub:
    """Service `embedder` giả lập (hợp đồng ở Plan.md mục 2.6).

    Vector sinh theo hàm băm của chính đoạn văn: cùng một đoạn luôn ra cùng một vector, hai
    đoạn khác nhau ra vector khác nhau. Đủ để kiểm "đúng thứ tự", "đúng số lượng", "ghi đúng
    vào DB" mà không cần đụng tới `torch`.
    """

    router: respx.MockRouter
    requests: list[dict[str, Any]]

    @property
    def kinds(self) -> list[str]:
        """`kind` của từng request đã gửi — chỗ kiểm `passage:` / `query:` không bị nhầm."""
        return [body["kind"] for body in self.requests]

    @property
    def batch_sizes(self) -> list[int]:
        return [len(body["texts"]) for body in self.requests]


def fake_vector(text: str, dim: int | None = None) -> list[float]:
    """Vector đơn vị (chuẩn hoá L2) suy từ nội dung đoạn văn — tất định giữa các lần chạy."""
    size = dim or settings.embedding_dim
    rng = random.Random(text)
    values = [rng.gauss(0.0, 1.0) for _ in range(size)]
    norm = sum(value * value for value in values) ** 0.5 or 1.0
    return [value / norm for value in values]


@pytest.fixture
def embedder() -> Iterator[EmbedderStub]:
    """`embedder` giả lập tại đúng `EMBEDDER_URL`, trả vector đúng số chiều đang cấu hình."""
    requests: list[dict[str, Any]] = []

    def _embed(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(
            200,
            json={
                "vectors": [fake_vector(text) for text in payload["texts"]],
                "dim": settings.embedding_dim,
                "model": settings.embedding_model,
            },
        )

    with respx.mock(base_url=settings.embedder_url, assert_all_called=False) as router:
        router.get("/health").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "ok",
                    "model": settings.embedding_model,
                    "dim": settings.embedding_dim,
                    "max_seq_length": _MAX_SEQ_LENGTH,
                },
            )
        )
        router.post("/embed").mock(side_effect=_embed)
        yield EmbedderStub(router=router, requests=requests)


# --------------------------------------------------------------------------- tiện ích


def _hide_password(url: str) -> str:
    """Giấu mật khẩu trước khi in URL vào thông báo skip (gitleaks quét cả log CI)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if parts.password:
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        netloc = f"{parts.username}:***@{host}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return url
