"""Fixture dùng chung: DB Postgres thật (trong transaction rollback) + mock CLIProxy/embedder.

Chủ sở hữu: Q | Task: 6.1, 12.8

Ba nguyên tắc, mỗi cái đổi lấy một loại lỗi đã gặp thật:

1. **DB thật, không SQLite** — `vector(384)`, `JSONB`, `ARRAY(TEXT)`, index một phần đều không
   tồn tại trong SQLite; test trên đó là test một hệ khác với hệ chạy thật.
2. **Mỗi test một transaction, cuối test rollback** — `commit()` trong mã ứng dụng vẫn chạy đúng
   đường của nó mà DB sau test vẫn sạch.
3. **Không có DB thì SKIP, không FAIL** — máy chưa bật Docker vẫn chạy được phần test thuần.

Chạy phần cần DB ở máy (container `db` đã publish cổng 5432)::

    docker compose exec db createdb -U bizcard bizcard_test
    TEST_DATABASE_URL=postgresql+psycopg://bizcard:change-me@localhost:5432/bizcard_test pytest
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import uuid
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
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
from app.core import workspace as workspace_dep
from app.core.config import settings
from app.core.db import Base, get_db
from app.core.workspace import ActiveWorkspace
from app.models.user import User
from app.models.workspace import Role, Workspace, WorkspaceMember
from app.services import auth

# Windows: `ProactorEventLoop` mặc định của Python 3.12 **không chạy được** với psycopg v3 async.
# Ứng dụng thật chạy trong container Linux nên không gặp; pytest ở máy Windows thì gặp ngay.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

#: Đường dẫn gọi model của CLIProxy (Plan.md mục 2.4). Trùng chuỗi `services/llm.py` dựng.
GENERATE_PATH = f"/v1beta/models/{settings.llm_model}:generateContent"
GENERATE_PATTERN = r"^/v1beta/models/[^:]+:generateContent$"

#: Độ dài chuỗi model giả lập trả về khi test không quan tâm nội dung.
_MAX_SEQ_LENGTH = 512

#: Mật khẩu của mọi người dùng do fixture tạo ra (task 12.8). Đủ luật của `schemas/user.py`.
TEST_PASSWORD = "matkhau123"


# --------------------------------------------------------------------------- DB thật


def candidate_database_urls() -> list[str]:
    """Các URL sẽ thử, theo thứ tự ưu tiên.

    `TEST_DATABASE_URL` thắng tất cả. Không có thì suy từ `DATABASE_URL` và đổi tên database
    thành `<tên>_test` — **không bao giờ chạy test trên DB đang dùng thật**.

    Thêm biến thể `localhost` khi host là `db`: chạy pytest ở máy thì `db` không phân giải được,
    mà container `db` có publish cổng 5432 ra ngoài.
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

    Engine **đồng bộ** cho phần DDL: fixture phạm vi `session` mà `async` thì nó chạy trên một
    event loop khác với loop của từng test, và mọi connection mở trong đó sẽ nổ "attached to a
    different loop".

    `TRUNCATE` một lần ở đầu phiên vì một lượt chạy bị ngắt giữa chừng có thể để lại dữ liệu đã
    commit. Không `drop_all()` ở cuối: lượt sau lại phải dựng lại cả index `ivfflat`.
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

    `join_transaction_mode="create_savepoint"`: mã ứng dụng gọi `commit()` thoải mái, nó chỉ giải
    phóng savepoint bên trong transaction ngoài — thứ mà fixture này rollback ngay sau đó.
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

    Argon2 cố ý chậm (~50ms/lượt); băm lại ở mỗi test là cộng cả chục giây vào bộ test mà không
    kiểm thêm được gì.
    """
    return auth.hash_password(TEST_PASSWORD)


async def make_user(
    db: AsyncSession,
    email: str,
    display_name: str | None = None,
    *,
    user_id: uuid.UUID | None = None,
    connected: bool = True,
    workspace_id: uuid.UUID | None = None,
    role: str = Role.MEMBER,
) -> User:
    """Một người dùng thật trong DB, mật khẩu là `TEST_PASSWORD`.

    `user_id` để chỉ định trước id: nhiều file test khai sẵn một hằng chủ sở hữu ở tầng module,
    mà khoá ngoại đòi hàng `users` đó **tồn tại thật**.

    `connected`: mặc định người dùng đã có credential CLIProxy riêng, vì mọi lời gọi LLM đi bằng
    tên model có tiền tố của đúng người đó.

    **Mỗi người dùng mới đi kèm một không gian làm việc riêng** — không thuộc không gian nào thì
    mọi endpoint trả `409` và gần như mọi test sẽ đỏ vì một lý do chẳng liên quan.
    """
    user = User(
        id=user_id or uuid.uuid4(),
        email=email,
        password_hash=test_password_hash(),
        display_name=display_name,
        cliproxy_auth_file=f"antigravity-{email}.json" if connected else None,
    )
    db.add(user)
    await db.flush()
    if workspace_id is None:
        await make_workspace(db, display_name or email, owner=user)
    elif await db.get(Workspace, workspace_id) is None:
        # Nhiều file test khai sẵn một hằng id ở tầng module rồi dựng object ORM quanh nó. Tạo hộ
        # không gian ấy nếu chưa có, để mỗi file chỉ phải thêm đúng một tham số.
        await make_workspace(db, display_name or email, owner=user, workspace_id=workspace_id)
    else:
        await join_workspace(db, workspace_id, user, role=role)
    await db.refresh(user)
    return user


async def make_workspace(
    db: AsyncSession, name: str, *, owner: User, workspace_id: uuid.UUID | None = None
) -> Workspace:
    """Một không gian làm việc thật, `owner` là quản trị và nó thành không gian đang mở của họ."""
    workspace = Workspace(id=workspace_id or uuid.uuid4(), name=name)
    db.add(workspace)
    await db.flush()
    await join_workspace(db, workspace.id, owner, role=Role.ADMIN)
    return workspace


async def join_workspace(
    db: AsyncSession, workspace_id: uuid.UUID, user: User, *, role: str = Role.MEMBER
) -> WorkspaceMember:
    member = WorkspaceMember(workspace_id=workspace_id, user_id=user.id, role=role)
    db.add(member)
    user.active_workspace_id = workspace_id
    await db.flush()
    return member


async def workspace_id_of(db: AsyncSession, user: User) -> uuid.UUID:
    """Không gian đang mở của một người dùng — thứ mọi test cần để dựng dữ liệu."""
    await db.refresh(user)
    assert user.active_workspace_id is not None
    return user.active_workspace_id


async def active_workspace_of(db: AsyncSession, user: User) -> ActiveWorkspace:
    """`ActiveWorkspace` thật của một người dùng, cho test gọi thẳng hàm router.

    Đi qua `workspace.resolve()` chứ không tự dựng dataclass, để test không vô tình chạy với
    quyền mà DB không cho.
    """
    await db.refresh(user)
    active = await workspace_dep.resolve(db, user)
    assert active is not None, "Người dùng test chưa ở không gian nào"
    return active


@pytest.fixture
async def user_a(db_session: AsyncSession) -> User:
    """Người dùng A — chủ sở hữu mặc định của dữ liệu trong test một-người-dùng."""
    return await make_user(db_session, "a@example.com", "Người dùng A")


@pytest.fixture
async def user_b(db_session: AsyncSession) -> User:
    """Người dùng B — **không gian riêng**, dùng cho vế *cách nhau* của A9′ (`NEXT-05`)."""
    return await make_user(db_session, "b@example.com", "Người dùng B")


@pytest.fixture
async def workspace_a(db_session: AsyncSession, user_a: User) -> uuid.UUID:
    """Không gian của A — khoá dựng dữ liệu cho phần lớn test."""
    return await workspace_id_of(db_session, user_a)


@pytest.fixture
async def workspace_b(db_session: AsyncSession, user_b: User) -> uuid.UUID:
    return await workspace_id_of(db_session, user_b)


@pytest.fixture
async def active_a(db_session: AsyncSession, user_a: User) -> ActiveWorkspace:
    """Không gian của A dưới dạng `ActiveWorkspace` — cho test gọi thẳng hàm router."""
    return await active_workspace_of(db_session, user_a)


@pytest.fixture
async def teammate(db_session: AsyncSession, user_a: User) -> User:
    """Người thứ hai **trong cùng không gian với A**, vai trò `member`.

    Luật tách dữ liệu theo không gian sai được theo **cả hai** chiều, nên phải kiểm cả *cách
    nhau* lẫn *cùng nhau*.
    """
    return await make_user(
        db_session,
        "teammate@example.com",
        "Đồng nghiệp",
        workspace_id=await workspace_id_of(db_session, user_a),
        role=Role.MEMBER,
    )


@pytest.fixture
async def viewer(db_session: AsyncSession, user_a: User) -> User:
    """Người *chỉ xem* trong cùng không gian với A."""
    return await make_user(
        db_session,
        "viewer@example.com",
        "Chỉ xem",
        workspace_id=await workspace_id_of(db_session, user_a),
        role=Role.VIEWER,
    )


class _SessionHandle:
    """`async with` trả về đúng session của test và **không đóng** nó.

    `RequireLoginMiddleware` tự mở session bằng `SessionLocal()` vì nó chạy trước khi FastAPI giải
    dependency, nên `dependency_overrides[get_db]` không với tới. Để nguyên thì middleware không
    thấy hàng `users` đang nằm trong transaction chưa commit và **mọi** request bị đá về đăng nhập.
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

    Dùng `app.main.app` chứ không dựng một `FastAPI()` rỗng: phần đáng kiểm nhất nằm ở dây nối —
    middleware chặn, thứ tự middleware, context processor — mà app rỗng thì không có gì trong đó.

    Cookie ký bằng đúng `services/auth.sign_session()`, không dựng tay: phiên phải hết hiệu lực
    khi người dùng đổi mật khẩu.

    `follow_redirects=False`: bị chặn thì test phải **thấy** đúng cái `303`.
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

    Có cả hai dạng là cố ý: fixture cho test viết mới, hàm `api_client` cho helper module-level.
    """

    def factory(user: User | None = None) -> AbstractAsyncContextManager[httpx.AsyncClient]:
        return api_client(db_session, user)

    yield factory


# --------------------------------------------------------------------------- mock CLIProxy


@dataclass
class CliProxyStub:
    """Điều khiển CLIProxy giả lập trong một test.

    Mock ở tầng **HTTP** chứ không monkeypatch `services/llm.py`: lỗi hay gặp nhất không phải ở
    chỗ ta gọi sai hàm mà ở hình dạng response (bọc ```json — I-15; báo lỗi bằng HTTP 200 — I-02).
    """

    router: respx.MockRouter
    #: Nội dung `auth-files` hiện tại. Mặc định rỗng = chưa ai kết nối.
    files: list[dict[str, Any]] = field(default_factory=list)

    def reply(self, *texts: str) -> None:
        """Model trả lần lượt từng chuỗi cho từng lời gọi `generateContent`.

        Truyền nhiều chuỗi để dựng kịch bản thử lại.
        """
        self.router.post(path__regex=GENERATE_PATTERN).mock(
            side_effect=[httpx.Response(200, json=gemini_payload(text)) for text in texts]
        )

    def reply_payload(self, *payloads: dict[str, Any]) -> None:
        """Trả nguyên văn JSON của Gemini — dùng khi cần `finishReason`, `groundingMetadata`…"""
        self.router.post(path__regex=GENERATE_PATTERN).mock(
            side_effect=[httpx.Response(200, json=payload) for payload in payloads]
        )

    def fail(self, status_code: int, json: Any = None) -> None:
        """CLIProxy trả lỗi HTTP (400 `unknown provider`, 503 `auth_unavailable`…)."""
        self.router.post(path__regex=GENERATE_PATTERN).mock(
            return_value=httpx.Response(status_code, json=json or {"error": "lỗi giả lập"})
        )

    def auth_files(self, *names: str, cooldowns: Sequence[dict[str, Any]] = ()) -> None:
        """`GET /v0/management/auth-files` — nguồn sự thật của badge kết nối (I-02).

        Ghi vào `files` chứ **không** đăng ký thêm route: respx giữ route **đầu tiên**, nên đăng
        ký lần hai sẽ lặng lẽ không có tác dụng gì.
        """
        self.files[:] = [
            {
                "name": name,
                "provider": settings.cliproxy_auth_provider,
                "label": name,
                "status": "active",
                "disabled": False,
                "cooldowns": list(cooldowns),
            }
            for name in names
        ]

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
    """CLIProxy giả lập tại đúng `CLIPROXY_BASE_URL` mà `services/llm.py` sẽ gọi.

    `auth-files` cắm sẵn ngay từ đầu vì nó nằm trên **đường lỗi**: gặp `503 auth_unavailable`,
    `llm._explain_no_credential()` hỏi lại nó để biết credential có bị nhà cung cấp chặn không.
    """
    with respx.mock(base_url=settings.cliproxy_base_url, assert_all_called=False) as router:
        stub = CliProxyStub(router)
        router.get("/v0/management/auth-files").mock(
            side_effect=lambda request: httpx.Response(200, json={"files": stub.files})
        )
        yield stub


# --------------------------------------------------------------------------- mock embedder


@dataclass
class EmbedderStub:
    """Service `embedder` giả lập (hợp đồng ở Plan.md mục 2.6).

    Vector sinh theo hàm băm của chính đoạn văn: cùng một đoạn luôn ra cùng một vector. Đủ để
    kiểm thứ tự / số lượng / ghi đúng vào DB mà không cần đụng tới `torch`.
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
