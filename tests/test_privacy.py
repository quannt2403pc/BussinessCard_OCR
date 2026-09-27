"""Nghị định 13/2023: nhật ký truy xuất, hạn lưu trữ, xoá theo yêu cầu.

Chủ sở hữu: T | Task: NEXT-07 | xem Task.md

Dữ liệu ở đây **là dữ liệu cá nhân của người khác**. Bốn thứ đáng kiểm nhất:

1. **Nhật ký phải sống lâu hơn dữ liệu nó nói về.** Ghi "đã xoá 12 liên hệ theo yêu cầu" mà
   dòng ấy biến mất cùng lúc với 12 liên hệ thì nhật ký vô nghĩa.
2. **Xoá ở đây là xoá thật**, khác hẳn `merged_into_id` của `NEXT-04`: chủ thể dữ liệu *yêu
   cầu* xoá, quyền ấy không được phục vụ bằng một cờ ẩn — và chunk Knowledge Base phải đi theo,
   nếu không trợ lý AI vẫn trích dẫn người đã yêu cầu xoá.
3. **Không có gì tự xoá.** Đặt hạn lưu trữ chỉ đếm ra phần quá hạn; xoá là một cú bấm riêng.
4. **Khớp chính xác, không khớp mờ.** Đây là câu quyết định xoá vĩnh viễn dữ liệu của ai đó.
"""

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.card import BusinessCard, CardStatus
from app.models.kb import KBChunk, KBSourceType
from app.models.privacy import PrivacyLog
from app.models.user import User
from tests.conftest import workspace_id_of

ClientFactory = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]

EMAIL = "an.nguyen@vidu.vn"
PHONE = "+84912345678"


async def make_card(
    db: AsyncSession,
    user: User,
    full_name: str = "Nguyễn Văn An",
    *,
    email: str | None = None,
    phone: str | None = None,
    days_ago: int = 0,
) -> BusinessCard:
    card = BusinessCard(
        id=uuid.uuid4(),
        workspace_id=await workspace_id_of(db, user),
        user_id=user.id,
        image_path=f"uploads/{uuid.uuid4().hex}.jpg",
        image_hash=uuid.uuid4().hex * 2,
        full_name=full_name,
        email=email,
        phone=phone,
        status=CardStatus.CONFIRMED,
        uploaded_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days_ago),
    )
    db.add(card)
    await db.flush()
    return card


async def make_chunk(db: AsyncSession, user: User, card: BusinessCard) -> KBChunk:
    chunk = KBChunk(
        id=uuid.uuid4(),
        workspace_id=await workspace_id_of(db, user),
        source_type=str(KBSourceType.CARD),
        source_id=card.id,
        content="Nguyễn Văn An — Công ty Ví Dụ",
        embedding=[0.0] * 384,
    )
    db.add(chunk)
    await db.flush()
    return chunk


# --------------------------------------------------------------------------- hạn lưu trữ


async def test_retention_is_unlimited_by_default(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    """Tự đặt một hạn rồi tự xoá dữ liệu của người dùng là việc không ai cho phép."""
    await make_card(db_session, user_a, days_ago=5000)

    async with app_client(user_a) as http:
        body = (await http.get("/api/privacy/retention")).json()

    assert body["days"] is None
    assert body["expired"] == 0
    assert body["total_contacts"] == 1


async def test_setting_a_retention_counts_but_does_not_delete(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    """**Đặt hạn không xoá gì cả.** Xoá là một cú bấm riêng."""
    await make_card(db_session, user_a, "Cũ", days_ago=400)
    await make_card(db_session, user_a, "Mới", days_ago=1)

    async with app_client(user_a) as http:
        body = (await http.put("/api/privacy/retention", json={"days": 90})).json()
        cards = (await http.get("/api/cards?size=50")).json()

    assert body["days"] == 90
    assert body["expired"] == 1
    assert cards["total"] == 2  # chưa xoá gì


async def test_purge_removes_only_the_expired_ones(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    await make_card(db_session, user_a, "Cũ", days_ago=400)
    await make_card(db_session, user_a, "Mới", days_ago=1)

    async with app_client(user_a) as http:
        await http.put("/api/privacy/retention", json={"days": 90})
        purge = (await http.post("/api/privacy/purge")).json()
        cards = (await http.get("/api/cards?size=50")).json()
        logs = (await http.get("/api/privacy/logs")).json()

    assert purge["erased"] == 1
    assert [item["full_name"] for item in cards["items"]] == ["Mới"]
    assert logs["items"][0]["action"] == "retention"
    assert logs["items"][0]["record_count"] == 1


async def test_purge_without_a_retention_does_nothing(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    await make_card(db_session, user_a, days_ago=5000)

    async with app_client(user_a) as http:
        purge = (await http.post("/api/privacy/purge")).json()
        cards = (await http.get("/api/cards?size=50")).json()

    assert purge["erased"] == 0
    assert cards["total"] == 1


async def test_retention_below_the_floor_is_rejected(
    app_client: ClientFactory, user_a: User
) -> None:
    """Sàn 30 ngày để một cú gõ nhầm số `1` không xoá sạch dữ liệu vừa quét hôm qua."""
    async with app_client(user_a) as http:
        res = await http.put("/api/privacy/retention", json={"days": 1})

    assert res.status_code == 422


async def test_retention_can_be_cleared(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    await make_card(db_session, user_a, days_ago=400)

    async with app_client(user_a) as http:
        await http.put("/api/privacy/retention", json={"days": 90})
        body = (await http.put("/api/privacy/retention", json={"days": None})).json()

    assert body["days"] is None
    assert body["expired"] == 0


# --------------------------------------------------------------------------- xoá theo yêu cầu


async def test_subject_search_finds_by_email_and_phone(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    await make_card(db_session, user_a, "Qua email", email=EMAIL)
    await make_card(db_session, user_a, "Qua số", phone=PHONE)
    await make_card(db_session, user_a, "Người khác", email="khac@vidu.vn")

    async with app_client(user_a) as http:
        by_email = (await http.post("/api/privacy/subject", json={"term": EMAIL})).json()
        by_phone = (await http.post("/api/privacy/subject", json={"term": PHONE})).json()

    assert [item["full_name"] for item in by_email["items"]] == ["Qua email"]
    assert [item["full_name"] for item in by_phone["items"]] == ["Qua số"]


async def test_subject_search_does_not_match_loosely(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    """Khớp mờ ở đây nghĩa là một cú bấm có thể xoá nhầm người thứ hai."""
    await make_card(db_session, user_a, email=EMAIL)

    async with app_client(user_a) as http:
        body = (await http.post("/api/privacy/subject", json={"term": "an.nguyen"})).json()

    assert body["total"] == 0


async def test_subject_search_does_not_delete_anything(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    """Bước tìm và bước xoá tách nhau: người dùng phải thấy cái gì sắp mất."""
    await make_card(db_session, user_a, email=EMAIL)

    async with app_client(user_a) as http:
        await http.post("/api/privacy/subject", json={"term": EMAIL})
        cards = (await http.get("/api/cards?size=50")).json()
        logs = (await http.get("/api/privacy/logs")).json()

    assert cards["total"] == 1
    assert logs["total"] == 0


async def test_erase_removes_the_card_the_notes_and_the_kb_chunk(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    """Bỏ chunk KB lại thì trợ lý AI vẫn trích dẫn người đã yêu cầu xoá dữ liệu."""
    card = await make_card(db_session, user_a, email=EMAIL)
    await make_chunk(db_session, user_a, card)

    async with app_client(user_a) as http:
        await http.post(f"/api/contacts/{card.id}/notes", json={"body": "Gọi hôm qua"})
        erased = (await http.post("/api/privacy/erase", json={"term": EMAIL})).json()
        cards = (await http.get("/api/cards?size=50")).json()

    assert erased["erased"] == 1
    assert cards["total"] == 0
    assert await db_session.get(BusinessCard, card.id) is None
    chunks = await db_session.scalars(select(KBChunk).where(KBChunk.source_id == card.id))
    assert list(chunks.all()) == []


async def test_erase_writes_a_log_that_outlives_the_data(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    """Dòng nhật ký **không** có khoá ngoại tới danh thiếp, nên nó ở lại sau khi thẻ đã mất."""
    await make_card(db_session, user_a, email=EMAIL)
    await make_card(db_session, user_a, "Cùng người", email=EMAIL)

    async with app_client(user_a) as http:
        await http.post("/api/privacy/erase", json={"term": EMAIL})
        logs = (await http.get("/api/privacy/logs")).json()

    entry = logs["items"][0]
    assert entry["action"] == "erase"
    assert entry["record_count"] == 2
    assert entry["detail"]["term"] == EMAIL

    rows = await db_session.scalars(select(PrivacyLog).where(PrivacyLog.user_id == user_a.id))
    assert len(list(rows.all())) == 1


async def test_erase_never_touches_another_users_data(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User, user_b: User
) -> None:
    """Hai người cùng có danh thiếp của một người là chuyện thường."""
    theirs = await make_card(db_session, user_b, "Của B", email=EMAIL)
    await make_card(db_session, user_a, "Của A", email=EMAIL)

    async with app_client(user_a) as http:
        erased = (await http.post("/api/privacy/erase", json={"term": EMAIL})).json()

    assert erased["erased"] == 1
    assert await db_session.get(BusinessCard, theirs.id) is not None


async def test_erasing_nothing_is_still_logged(app_client: ClientFactory, user_a: User) -> None:
    """Một yêu cầu xoá không khớp ai cũng là một yêu cầu đã được xử lý — vẫn phải ghi lại."""
    async with app_client(user_a) as http:
        erased = (await http.post("/api/privacy/erase", json={"term": "khong-co@vidu.vn"})).json()
        logs = (await http.get("/api/privacy/logs")).json()

    assert erased["erased"] == 0
    assert logs["total"] == 1
    assert logs["items"][0]["record_count"] == 0


async def test_a_too_short_term_is_rejected(app_client: ClientFactory, user_a: User) -> None:
    async with app_client(user_a) as http:
        res = await http.post("/api/privacy/erase", json={"term": "a"})

    assert res.status_code == 422


# --------------------------------------------------------------------------- trang mục đích


async def test_the_privacy_page_says_what_is_collected(
    app_client: ClientFactory, user_a: User
) -> None:
    """Phần mục đích render thẳng từ HTML, đọc được kể cả khi mọi lời gọi API đều hỏng."""
    async with app_client(user_a) as http:
        res = await http.get("/privacy")

    assert res.status_code == 200
    assert "Thu thập để làm gì" in res.text
    assert "không bán" in res.text


async def test_the_privacy_page_needs_a_login(app_client: ClientFactory) -> None:
    """Trình duyệt bị đưa về trang đăng nhập; client API nhận `401` (Plan.md mục 4).

    Phân biệt bằng header `Accept` chứ không bằng đường dẫn — `wants_html()` của `12.4`. Gửi
    `*/*` mà nhận `303` thì một lời gọi `fetch()` hết phiên sẽ đi theo chuyển hướng và nhận về
    HTML trang đăng nhập ở chỗ nó đang chờ JSON.
    """
    async with app_client() as http:
        browser = await http.get("/privacy", headers={"Accept": "text/html"})
        api = await http.get("/privacy")

    assert browser.status_code == 303
    assert "/auth/login" in browser.headers["location"]
    assert api.status_code == 401
