"""Sự kiện thu thập: đóng dấu lúc quét, gắn lại hàng loạt, báo cáo chuyển đổi.

Chủ sở hữu: T | Task: NEXT-03 | xem Task.md

Ba nhóm ca đáng kiểm nhất, cả ba đều **hỏng âm thầm**:

1. **Nhiều nhất một sự kiện đang diễn ra.** Hai dòng cùng bật thì thẻ vừa quét đóng dấu vào đâu
   trở thành ngẫu nhiên theo thứ tự Postgres trả về — không có thông báo lỗi nào.
2. **Tỉ lệ chốt tính trên số đã ngã ngũ**, không trên tổng số thẻ. Chia cho tổng thì hội chợ vừa
   diễn ra luôn trông tệ hơn hội chợ năm ngoái, và con số đó vẫn trông rất hợp lý.
3. **Giá trị cũ `closed` không bị gán bừa sang `lost`** — đó là bịa dữ liệu, ngay vào con số mà
   cả báo cáo dựa lên.
"""

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import date

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.card import BusinessCard, CardStatus, RelationshipStatus
from app.models.user import User
from app.repositories import card as card_repo
from app.repositories import event as event_repo
from app.services.normalize_company import normalize_label

ClientFactory = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]

EXPO = "VietnamExpo 2026"


async def make_card(
    db: AsyncSession,
    user: User,
    full_name: str,
    *,
    relationship_status: RelationshipStatus = RelationshipStatus.NEW,
    event_id: uuid.UUID | None = None,
) -> BusinessCard:
    card = BusinessCard(
        id=uuid.uuid4(),
        user_id=user.id,
        image_path=f"uploads/{uuid.uuid4().hex}.jpg",
        image_hash=uuid.uuid4().hex * 2,
        full_name=full_name,
        status=CardStatus.CONFIRMED,
        relationship_status=relationship_status,
        event_id=event_id,
    )
    db.add(card)
    await db.flush()
    return card


async def create_event(
    db: AsyncSession, user: User, name: str = EXPO, *, activate: bool = False
) -> uuid.UUID:
    event = await event_repo.create(db, user_id=user.id, name=name, activate=activate)
    return event.id


# --------------------------------------------------------------------------- tên & trùng lặp


def test_normalize_label_keeps_legal_forms() -> None:
    """Khác `normalize_company_name()`: tên sự kiện không bị gỡ hình thức pháp lý.

    Gỡ đi thì "Hội thảo Co., Ltd" và "Hội thảo" đụng nhau ở ràng buộc unique.
    """
    assert normalize_label("VietnamExpo 2026") == normalize_label("vietnamexpo  2026")
    assert normalize_label("Hội chợ Cần Thơ") == normalize_label("Hoi cho Can Tho")
    assert normalize_label("Expo Co., Ltd") != normalize_label("Expo")


def test_normalize_label_rejects_empty() -> None:
    with pytest.raises(ValueError):
        normalize_label("   ...   ")


async def test_duplicate_name_is_409(app_client: ClientFactory, user_a: User) -> None:
    async with app_client(user_a) as http:
        first = await http.post("/api/events", json={"name": EXPO})
        second = await http.post("/api/events", json={"name": "vietnamexpo 2026"})

    assert first.status_code == 201
    assert second.status_code == 409


async def test_two_users_may_share_an_event_name(
    app_client: ClientFactory, user_a: User, user_b: User
) -> None:
    """Hai người cùng đi một hội chợ thì ai cũng có sự kiện của mình (cùng luật với công ty)."""
    async with app_client(user_a) as http:
        mine = await http.post("/api/events", json={"name": EXPO})
    async with app_client(user_b) as http:
        theirs = await http.post("/api/events", json={"name": EXPO})

    assert (mine.status_code, theirs.status_code) == (201, 201)


async def test_end_date_before_start_is_rejected(app_client: ClientFactory, user_a: User) -> None:
    async with app_client(user_a) as http:
        res = await http.post(
            "/api/events",
            json={"name": EXPO, "starts_on": "2026-09-20", "ends_on": "2026-09-18"},
        )
    assert res.status_code == 422


# --------------------------------------------------------------------------- sự kiện đang diễn ra


async def test_only_one_event_can_be_active(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    """Bật cái thứ hai phải tắt cái thứ nhất, không phải để cả hai cùng bật."""
    first = await create_event(db_session, user_a, "Hội chợ tháng 8", activate=True)
    second = await create_event(db_session, user_a, EXPO)

    async with app_client(user_a) as http:
        res = await http.patch(f"/api/events/{second}", json={"activate": True})
        listing = await http.get("/api/events")

    assert res.status_code == 200
    body = listing.json()
    assert body["active_event_id"] == str(second)
    active = [item["event_id"] for item in body["items"] if item["is_active"]]
    assert active == [str(second)]
    assert str(first) not in active


async def test_new_card_is_stamped_with_the_active_event(
    db_session: AsyncSession, user_a: User
) -> None:
    """Đóng dấu ngay trong `create_card()` — chỗ **duy nhất** sinh ra một danh thiếp."""
    event_id = await create_event(db_session, user_a, EXPO, activate=True)

    card = await card_repo.create_card(
        db_session,
        user_id=user_a.id,
        image_path="uploads/a.jpg",
        image_hash=uuid.uuid4().hex * 2,
    )

    assert card.event_id == event_id


async def test_new_card_has_no_event_when_none_is_active(
    db_session: AsyncSession, user_a: User
) -> None:
    await create_event(db_session, user_a, EXPO)  # có sự kiện nhưng không bật

    card = await card_repo.create_card(
        db_session,
        user_id=user_a.id,
        image_path="uploads/b.jpg",
        image_hash=uuid.uuid4().hex * 2,
    )

    assert card.event_id is None


async def test_active_event_of_another_user_does_not_leak(
    db_session: AsyncSession, user_a: User, user_b: User
) -> None:
    """B bật sự kiện của B thì thẻ của A vẫn không nhãn — không phải nhãn của B."""
    await create_event(db_session, user_b, EXPO, activate=True)

    card = await card_repo.create_card(
        db_session,
        user_id=user_a.id,
        image_path="uploads/c.jpg",
        image_hash=uuid.uuid4().hex * 2,
    )

    assert card.event_id is None


# --------------------------------------------------------------------------- gắn lại hàng loạt


async def test_assign_and_unassign_cards(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    event_id = await create_event(db_session, user_a, EXPO)
    cards = [await make_card(db_session, user_a, f"Người {index}") for index in range(3)]
    ids = [str(card.id) for card in cards]

    async with app_client(user_a) as http:
        assigned = await http.post(f"/api/events/{event_id}/cards", json={"card_ids": ids})
        removed = await http.post("/api/events/unassign", json={"card_ids": ids[:1]})
        listing = await http.get("/api/events")

    assert assigned.json()["updated"] == 3
    assert removed.json()["updated"] == 1
    assert listing.json()["items"][0]["total_cards"] == 2


async def test_assign_skips_cards_of_another_user(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User, user_b: User
) -> None:
    """Id của người khác lọt vào danh sách thì đơn giản là không khớp dòng nào."""
    event_id = await create_event(db_session, user_a, EXPO)
    mine = await make_card(db_session, user_a, "Của A")
    theirs = await make_card(db_session, user_b, "Của B")

    async with app_client(user_a) as http:
        res = await http.post(
            f"/api/events/{event_id}/cards",
            json={"card_ids": [str(mine.id), str(theirs.id)]},
        )

    assert res.json()["updated"] == 1
    await db_session.refresh(theirs)
    assert theirs.event_id is None


async def test_deleting_an_event_keeps_the_cards(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    """Xoá nhãn một hội chợ **không được** kéo theo mấy trăm tấm thẻ thu về từ đó."""
    event_id = await create_event(db_session, user_a, EXPO, activate=True)
    card = await make_card(db_session, user_a, "Nguyễn Văn A", event_id=event_id)

    async with app_client(user_a) as http:
        res = await http.delete(f"/api/events/{event_id}")

    assert res.status_code == 204
    await db_session.refresh(card)
    assert card.event_id is None


# --------------------------------------------------------------------------- báo cáo


async def test_conversion_rate_counts_only_decided_contacts(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    """3 chốt / 1 trượt / 6 còn đang trao đổi → **75%**, không phải 30%."""
    event_id = await create_event(db_session, user_a, EXPO)
    stages = (
        [RelationshipStatus.WON] * 3 + [RelationshipStatus.LOST] + [RelationshipStatus.TALKING] * 6
    )
    for index, stage in enumerate(stages):
        await make_card(
            db_session, user_a, f"Người {index}", relationship_status=stage, event_id=event_id
        )

    async with app_client(user_a) as http:
        body = (await http.get("/api/events")).json()

    row = body["items"][0]
    assert (row["total_cards"], row["won"], row["lost"], row["in_progress"]) == (10, 3, 1, 6)
    assert row["conversion_rate"] == 0.75


async def test_conversion_rate_is_null_when_nothing_is_decided(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    """Chưa ai ngã ngũ thì trả `null`, **không phải `0`** — hai thứ đó khác hẳn nhau."""
    event_id = await create_event(db_session, user_a, EXPO)
    await make_card(db_session, user_a, "Mới quét", event_id=event_id)

    async with app_client(user_a) as http:
        body = (await http.get("/api/events")).json()

    assert body["items"][0]["conversion_rate"] is None


async def test_legacy_closed_is_counted_apart(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    """`closed` (giá trị trước NEXT-03) không được gộp vào `won` hay `lost`."""
    event_id = await create_event(db_session, user_a, EXPO)
    await make_card(
        db_session,
        user_a,
        "Dừng từ hôm qua",
        relationship_status=RelationshipStatus.CLOSED,
        event_id=event_id,
    )
    await make_card(
        db_session, user_a, "Đã chốt", relationship_status=RelationshipStatus.WON, event_id=event_id
    )

    async with app_client(user_a) as http:
        row = (await http.get("/api/events")).json()["items"][0]

    assert (row["won"], row["lost"], row["closed_unknown"]) == (1, 0, 1)
    assert row["conversion_rate"] == 1.0  # chỉ 1 thẻ ngã ngũ rõ ràng, và nó là thẻ chốt


async def test_unassigned_cards_are_reported_separately(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    """Giấu nhóm chưa gắn nhãn đi thì tổng các sự kiện không bằng tổng số thẻ."""
    event_id = await create_event(db_session, user_a, EXPO)
    await make_card(db_session, user_a, "Trong hội chợ", event_id=event_id)
    await make_card(db_session, user_a, "Ngoài hội chợ")

    async with app_client(user_a) as http:
        body = (await http.get("/api/events")).json()

    assert body["items"][0]["total_cards"] == 1
    assert body["unassigned"]["event_id"] is None
    assert body["unassigned"]["total_cards"] == 1


async def test_report_never_counts_another_users_cards(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User, user_b: User
) -> None:
    await create_event(db_session, user_a, EXPO)
    await make_card(db_session, user_b, "Của B")

    async with app_client(user_a) as http:
        body = (await http.get("/api/events")).json()

    assert body["items"][0]["total_cards"] == 0
    assert body["unassigned"]["total_cards"] == 0


# --------------------------------------------------------------------------- tách dữ liệu (A9)


async def test_event_of_another_user_is_404_everywhere(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User, user_b: User
) -> None:
    foreign = await create_event(db_session, user_b, EXPO)
    card = await make_card(db_session, user_a, "Của A")

    async with app_client(user_a) as http:
        calls = [
            await http.patch(f"/api/events/{foreign}", json={"activate": True}),
            await http.delete(f"/api/events/{foreign}"),
            await http.post(f"/api/events/{foreign}/cards", json={"card_ids": [str(card.id)]}),
        ]

    assert [res.status_code for res in calls] == [404, 404, 404]


async def test_patch_with_empty_body_is_rejected(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    event_id = await create_event(db_session, user_a, EXPO)
    async with app_client(user_a) as http:
        res = await http.patch(f"/api/events/{event_id}", json={})
    assert res.status_code == 400


async def test_patch_name_only_keeps_the_dates(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    """Không gửi `starts_on` thì ngày cũ còn nguyên — cùng luật với `NEXT-01`."""
    event = await event_repo.create(
        db_session,
        user_id=user_a.id,
        name=EXPO,
        starts_on=date(2026, 9, 20),
        ends_on=date(2026, 9, 23),
    )

    async with app_client(user_a) as http:
        res = await http.patch(f"/api/events/{event.id}", json={"name": "VietnamExpo 2026 (HN)"})

    body = res.json()
    assert body["name"] == "VietnamExpo 2026 (HN)"
    assert body["starts_on"] == "2026-09-20"
    assert body["ends_on"] == "2026-09-23"


async def test_patch_null_date_clears_it(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    event = await event_repo.create(
        db_session, user_id=user_a.id, name=EXPO, starts_on=date(2026, 9, 20)
    )

    async with app_client(user_a) as http:
        res = await http.patch(f"/api/events/{event.id}", json={"starts_on": None})

    assert res.json()["starts_on"] is None
