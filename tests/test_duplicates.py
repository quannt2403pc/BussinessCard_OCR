"""Phát hiện và gộp liên hệ trùng.

Chủ sở hữu: T | Task: NEXT-04 | xem Task.md

Ba nhóm ca đáng kiểm nhất:

1. **Gộp không được làm mất gì.** Bản trùng vẫn nằm trong DB, ghi chú của nó theo sang thẻ
   chính, và trạng thái quan hệ xa nhất thắng — mất một quan hệ *đã chốt* vì thao tác gộp là
   lỗi nặng hơn hẳn việc giữ nhầm một trạng thái lạc quan.
2. **Thẻ đã gộp phải biến khỏi ĐỦ sáu chỗ liệt kê.** Sót một chỗ là một con số thổi phồng mà
   không ai nhận ra, vì mọi chỗ còn lại đều đúng.
3. **Không có đường nào tự gộp**, và gợi ý phải nói rõ khi giá trị trùng trông giống số tổng
   đài hơn là một người.
"""

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.card import BusinessCard, CardStatus, RelationshipStatus
from app.models.contact_note import ContactNote
from app.models.user import User

ClientFactory = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]

EMAIL = "an.nguyen@fpt.com.vn"
PHONE = "+84912345678"
TODAY = date(2026, 9, 24)
BASE = datetime(2026, 9, 20, 8, 0)


async def make_card(
    db: AsyncSession,
    user: User,
    full_name: str | None = "Nguyễn Văn An",
    *,
    email: str | None = None,
    phone: str | None = None,
    minutes: int = 0,
    relationship_status: RelationshipStatus = RelationshipStatus.NEW,
    **extra: object,
) -> BusinessCard:
    card = BusinessCard(
        id=uuid.uuid4(),
        user_id=user.id,
        image_path=f"uploads/{uuid.uuid4().hex}.jpg",
        image_hash=uuid.uuid4().hex * 2,
        full_name=full_name,
        email=email,
        phone=phone,
        status=CardStatus.CONFIRMED,
        relationship_status=relationship_status,
        uploaded_at=BASE + timedelta(minutes=minutes),
        **extra,
    )
    db.add(card)
    await db.flush()
    return card


async def merge(
    http: httpx.AsyncClient, primary: BusinessCard, *duplicates: BusinessCard
) -> httpx.Response:
    return await http.post(
        "/api/duplicates/merge",
        json={
            "primary_id": str(primary.id),
            "duplicate_ids": [str(card.id) for card in duplicates],
        },
    )


# --------------------------------------------------------------------------- phát hiện


async def test_finds_cards_sharing_an_email(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    await make_card(db_session, user_a, email=EMAIL, minutes=0)
    await make_card(db_session, user_a, email=EMAIL, minutes=5)
    await make_card(db_session, user_a, "Người khác", email="khac@fpt.com.vn")

    async with app_client(user_a) as http:
        body = (await http.get("/api/duplicates")).json()

    assert body["total_groups"] == 1
    group = body["items"][0]
    assert [(r["kind"], r["value"]) for r in group["reasons"]] == [("email", EMAIL)]
    assert len(group["cards"]) == 2
    assert group["same_name"] is True


async def test_different_names_are_not_marked_same_name(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    """Cùng số mà khác tên là tín hiệu *cả phòng dùng chung một số*, không phải một người."""
    await make_card(db_session, user_a, "Nguyễn Văn An", phone=PHONE, minutes=0)
    await make_card(db_session, user_a, "Trần Thị Bình", phone=PHONE, minutes=5)

    async with app_client(user_a) as http:
        group = (await http.get("/api/duplicates")).json()["items"][0]

    assert [r["kind"] for r in group["reasons"]] == ["phone"]
    assert group["same_name"] is False


async def test_same_cards_on_two_values_become_one_group(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    """Trùng **cả** email lẫn số điện thoại là một khối có hai lý do, không phải hai khối.

    Tách đôi thì màn hình hiện hai lần cùng một cặp thẻ, mà lại đánh mất đúng cái tín hiệu
    mạnh nhất: chúng trùng nhau ở hai chỗ độc lập.
    """
    await make_card(db_session, user_a, email=EMAIL, phone=PHONE, minutes=0)
    await make_card(db_session, user_a, email=EMAIL, phone=PHONE, minutes=5)

    async with app_client(user_a) as http:
        body = (await http.get("/api/duplicates")).json()

    assert body["total_groups"] == 1
    assert sorted(r["kind"] for r in body["items"][0]["reasons"]) == ["email", "phone"]


async def test_many_cards_on_one_value_are_flagged_as_shared(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    """Bốn thẻ cùng một số gần như chắc chắn là tổng đài — cảnh báo trước khi người dùng bấm."""
    for index in range(4):
        await make_card(db_session, user_a, f"Người {index}", phone=PHONE, minutes=index)

    async with app_client(user_a) as http:
        group = (await http.get("/api/duplicates")).json()["items"][0]

    assert group["likely_shared"] is True


async def test_a_single_card_is_not_a_group(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    await make_card(db_session, user_a, email=EMAIL)

    async with app_client(user_a) as http:
        body = (await http.get("/api/duplicates")).json()

    assert body == {"total_groups": 0, "items": []}


async def test_never_groups_across_users(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User, user_b: User
) -> None:
    await make_card(db_session, user_a, email=EMAIL)
    await make_card(db_session, user_b, email=EMAIL)

    async with app_client(user_a) as http:
        body = (await http.get("/api/duplicates")).json()

    assert body["total_groups"] == 0


# --------------------------------------------------------------------------- gộp


async def test_merge_fills_empty_fields_and_keeps_the_primary_values(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    """Ô trống được lấp; ô đã có **không bị đụng tới** — thẻ chính là thứ người dùng chọn giữ."""
    primary = await make_card(
        db_session, user_a, email=EMAIL, minutes=0, job_title="Giám đốc", website=None
    )
    other = await make_card(
        db_session,
        user_a,
        email=EMAIL,
        minutes=5,
        job_title="Nhân viên",
        website="https://fpt.com.vn",
    )

    async with app_client(user_a) as http:
        res = await merge(http, primary, other)

    assert res.status_code == 200
    assert res.json()["merged"] == 1
    await db_session.refresh(primary)
    assert primary.job_title == "Giám đốc"
    assert primary.website == "https://fpt.com.vn"


async def test_merge_keeps_the_furthest_stage(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    """Thẻ chính còn `new`, bản trùng đã `won` → thẻ chính thành `won`."""
    primary = await make_card(db_session, user_a, email=EMAIL, minutes=0)
    other = await make_card(
        db_session,
        user_a,
        email=EMAIL,
        minutes=5,
        relationship_status=RelationshipStatus.WON,
    )

    async with app_client(user_a) as http:
        await merge(http, primary, other)

    await db_session.refresh(primary)
    assert primary.relationship_status == RelationshipStatus.WON


async def test_merge_keeps_the_earliest_follow_up(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    """Bỏ sót một lời nhắc tệ hơn là nhắc sớm một ngày."""
    primary = await make_card(
        db_session, user_a, email=EMAIL, minutes=0, follow_up_at=TODAY + timedelta(days=7)
    )
    other = await make_card(db_session, user_a, email=EMAIL, minutes=5, follow_up_at=TODAY)

    async with app_client(user_a) as http:
        await merge(http, primary, other)

    await db_session.refresh(primary)
    assert primary.follow_up_at == TODAY


async def test_merge_moves_the_notes_to_the_primary(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    primary = await make_card(db_session, user_a, email=EMAIL, minutes=0)
    other = await make_card(db_session, user_a, email=EMAIL, minutes=5)

    async with app_client(user_a) as http:
        await http.post(f"/api/contacts/{other.id}/notes", json={"body": "Gọi hôm thứ ba"})
        await merge(http, primary, other)
        body = (await http.get(f"/api/contacts/{primary.id}")).json()

    assert [note["body"] for note in body["notes"]] == ["Gọi hôm thứ ba"]


async def test_merge_keeps_the_duplicate_row(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    """Gộp mềm: bản trùng **vẫn nằm trong DB**, chỉ mang cờ."""
    primary = await make_card(db_session, user_a, email=EMAIL, minutes=0)
    other = await make_card(db_session, user_a, email=EMAIL, minutes=5)

    async with app_client(user_a) as http:
        await merge(http, primary, other)

    still_there = await db_session.scalar(select(BusinessCard).where(BusinessCard.id == other.id))
    assert still_there is not None
    assert still_there.merged_into_id == primary.id
    assert still_there.image_path  # tấm ảnh gốc không mất


async def test_merged_group_disappears_from_the_suggestions(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    primary = await make_card(db_session, user_a, email=EMAIL, minutes=0)
    other = await make_card(db_session, user_a, email=EMAIL, minutes=5)

    async with app_client(user_a) as http:
        await merge(http, primary, other)
        body = (await http.get("/api/duplicates")).json()

    assert body["total_groups"] == 0


async def test_cannot_merge_a_card_twice(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    primary = await make_card(db_session, user_a, email=EMAIL, minutes=0)
    other = await make_card(db_session, user_a, email=EMAIL, minutes=5)

    async with app_client(user_a) as http:
        first = await merge(http, primary, other)
        second = await merge(http, primary, other)

    assert first.status_code == 200
    assert second.status_code == 404


async def test_cannot_merge_a_card_of_another_user(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User, user_b: User
) -> None:
    mine = await make_card(db_session, user_a, email=EMAIL)
    theirs = await make_card(db_session, user_b, email=EMAIL)

    async with app_client(user_a) as http:
        res = await merge(http, mine, theirs)

    assert res.status_code == 404
    await db_session.refresh(theirs)
    assert theirs.merged_into_id is None


async def test_primary_cannot_be_its_own_duplicate(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    card = await make_card(db_session, user_a, email=EMAIL)

    async with app_client(user_a) as http:
        res = await merge(http, card, card)

    assert res.status_code == 422


# --------------------------------------------------------------------------- gỡ gộp


async def test_unmerge_brings_the_card_back(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    primary = await make_card(db_session, user_a, email=EMAIL, minutes=0)
    other = await make_card(db_session, user_a, email=EMAIL, minutes=5)

    async with app_client(user_a) as http:
        await merge(http, primary, other)
        res = await http.post("/api/duplicates/unmerge", json={"card_ids": [str(other.id)]})
        listing = await http.get("/api/cards?size=50")

    assert res.status_code == 200
    assert res.json()["restored"] == 1
    assert str(other.id) in [item["id"] for item in listing.json()["items"]]


async def test_unmerging_a_card_that_was_never_merged_is_404(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    card = await make_card(db_session, user_a, email=EMAIL)

    async with app_client(user_a) as http:
        res = await http.post("/api/duplicates/unmerge", json={"card_ids": [str(card.id)]})

    assert res.status_code == 404


# ------------------------------------------------- thẻ đã gộp biến khỏi đủ sáu chỗ liệt kê


async def test_merged_card_disappears_from_every_listing(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    """Sót một chỗ là một con số thổi phồng mà không ai nhận ra.

    Ba chỗ ở đây; hai chỗ còn lại của export nằm ở `tests/test_export.py`. Chỗ thứ sáu là báo
    cáo theo sự kiện của `NEXT-03`, đã cắt khỏi phạm vi 2026-09-25.
    """
    primary = await make_card(db_session, user_a, email=EMAIL, minutes=0, follow_up_at=TODAY)
    other = await make_card(db_session, user_a, email=EMAIL, minutes=5, follow_up_at=TODAY)

    async with app_client(user_a) as http:
        before_stats = (await http.get("/api/stats")).json()["total_cards"]
        await merge(http, primary, other)

        cards = (await http.get("/api/cards?size=50")).json()
        stats = (await http.get("/api/stats")).json()
        due = (await http.get("/api/contacts/due", params={"on": TODAY.isoformat()})).json()

    assert before_stats == 2
    assert cards["total"] == 1  # 1. danh sách danh thiếp
    assert stats["total_cards"] == 1  # 2. số liệu trang chủ
    assert due["total"] == 1  # 3. hàng chờ nhắc liên hệ
    assert [item["card_id"] for item in due["items"]] == [str(primary.id)]
    # Hai chỗ còn lại — câu đếm và câu lấy dòng của export — kiểm ở `tests/test_export.py`:
    # router export tự mở `SessionLocal()`, chỉ file kia mới có bộ vá cho nó.


async def test_merged_card_is_still_readable(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    """Biến khỏi danh sách, nhưng mở thẳng bằng id thì vẫn đọc được — nó chưa bị xoá."""
    primary = await make_card(db_session, user_a, email=EMAIL, minutes=0)
    other = await make_card(db_session, user_a, email=EMAIL, minutes=5)

    async with app_client(user_a) as http:
        await merge(http, primary, other)
        res = await http.get(f"/api/cards/{other.id}")

    assert res.status_code == 200


async def test_notes_of_a_merged_card_are_not_counted_twice(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    primary = await make_card(db_session, user_a, email=EMAIL, minutes=0)
    other = await make_card(db_session, user_a, email=EMAIL, minutes=5)

    async with app_client(user_a) as http:
        await http.post(f"/api/contacts/{other.id}/notes", json={"body": "Ghi chú"})
        await merge(http, primary, other)

    rows = await db_session.scalars(select(ContactNote).where(ContactNote.user_id == user_a.id))
    notes = list(rows.all())
    assert len(notes) == 1
    assert notes[0].card_id == primary.id
