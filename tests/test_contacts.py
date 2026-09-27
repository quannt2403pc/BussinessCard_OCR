"""Vòng đời quan hệ: trạng thái, ngày hẹn, ghi chú theo dõi, khối *Cần liên hệ hôm nay*.

Chủ sở hữu: T | Task: NEXT-01 | xem Task.md

Hai nhóm ca đáng kiểm nhất, vì cả hai đều **hỏng âm thầm**:

1. *Không gửi trường* khác *gửi null*. Gộp hai thứ đó thì mỗi lần đổi trạng thái là một lần
   xoá mất ngày hẹn người dùng đặt hôm trước, mà không có thông báo lỗi nào.
2. Thẻ của người khác trả **404**, không phải 403 — 403 là tự khai rằng bản ghi ấy tồn tại.
"""

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.card import BusinessCard, CardStatus, RelationshipStatus
from app.models.user import User
from tests.conftest import workspace_id_of

ClientFactory = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]

TODAY = date(2026, 9, 24)
YESTERDAY = TODAY - timedelta(days=1)
LAST_WEEK = TODAY - timedelta(days=7)
NEXT_WEEK = TODAY + timedelta(days=7)


async def make_card(
    db: AsyncSession,
    user: User,
    full_name: str,
    *,
    relationship_status: RelationshipStatus = RelationshipStatus.NEW,
    follow_up_at: date | None = None,
) -> BusinessCard:
    card = BusinessCard(
        id=uuid.uuid4(),
        workspace_id=await workspace_id_of(db, user),
        user_id=user.id,
        image_path=f"uploads/{uuid.uuid4().hex}.jpg",
        image_hash=uuid.uuid4().hex * 2,
        full_name=full_name,
        status=CardStatus.CONFIRMED,
        relationship_status=relationship_status,
        follow_up_at=follow_up_at,
    )
    db.add(card)
    await db.flush()
    return card


@pytest.fixture
async def card(db_session: AsyncSession, user_a: User) -> AsyncIterator[BusinessCard]:
    yield await make_card(db_session, user_a, "Nguyễn Văn A")


# --------------------------------------------------------------------------- mặc định


async def test_new_card_starts_as_new_without_follow_up(
    app_client: ClientFactory, user_a: User, card: BusinessCard
) -> None:
    """Thẻ vừa quét: trạng thái `new`, **không** hẹn sẵn.

    Không tự đặt hẹn hộ người dùng: một hàng chờ đầy việc không ai hẹn là hàng chờ bị bỏ qua.
    """
    async with app_client(user_a) as http:
        res = await http.get(f"/api/contacts/{card.id}")
    assert res.status_code == 200
    body = res.json()
    assert body["relationship_status"] == "new"
    assert body["relationship_label"] == "Mới"
    assert body["follow_up_at"] is None
    assert body["notes"] == []


# --------------------------------------------------------------------------- PATCH


async def test_patch_sets_stage_and_date(
    app_client: ClientFactory, user_a: User, card: BusinessCard
) -> None:
    async with app_client(user_a) as http:
        res = await http.patch(
            f"/api/contacts/{card.id}",
            json={"relationship_status": "talking", "follow_up_at": NEXT_WEEK.isoformat()},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["relationship_status"] == "talking"
    assert body["relationship_label"] == "Đang trao đổi"
    assert body["follow_up_at"] == NEXT_WEEK.isoformat()


async def test_patch_stage_only_keeps_existing_date(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    """Đổi trạng thái mà **không** gửi `follow_up_at` thì ngày hẹn cũ còn nguyên."""
    card = await make_card(db_session, user_a, "Trần B", follow_up_at=NEXT_WEEK)
    async with app_client(user_a) as http:
        res = await http.patch(
            f"/api/contacts/{card.id}", json={"relationship_status": "contacted"}
        )
    assert res.status_code == 200
    assert res.json()["follow_up_at"] == NEXT_WEEK.isoformat()


async def test_patch_null_date_clears_appointment(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    """Gửi `follow_up_at: null` là **xoá hẹn** — khác hẳn ca không gửi trường ở trên."""
    card = await make_card(db_session, user_a, "Lê C", follow_up_at=NEXT_WEEK)
    async with app_client(user_a) as http:
        res = await http.patch(f"/api/contacts/{card.id}", json={"follow_up_at": None})
    assert res.status_code == 200
    body = res.json()
    assert body["follow_up_at"] is None
    assert body["relationship_status"] == "new"  # trạng thái không gửi thì giữ nguyên


async def test_patch_empty_body_is_rejected(
    app_client: ClientFactory, user_a: User, card: BusinessCard
) -> None:
    async with app_client(user_a) as http:
        res = await http.patch(f"/api/contacts/{card.id}", json={})
    assert res.status_code == 400


async def test_patch_rejects_unknown_stage(
    app_client: ClientFactory, user_a: User, card: BusinessCard
) -> None:
    async with app_client(user_a) as http:
        res = await http.patch(f"/api/contacts/{card.id}", json={"relationship_status": "co-le"})
    assert res.status_code == 422


# --------------------------------------------------------------------------- ghi chú


async def test_notes_are_listed_newest_first(
    app_client: ClientFactory, user_a: User, card: BusinessCard
) -> None:
    async with app_client(user_a) as http:
        for body in ("Gọi lần đầu", "Gửi báo giá"):
            res = await http.post(f"/api/contacts/{card.id}/notes", json={"body": body})
            assert res.status_code == 201
        res = await http.get(f"/api/contacts/{card.id}")

    bodies = [note["body"] for note in res.json()["notes"]]
    assert bodies == ["Gửi báo giá", "Gọi lần đầu"]


async def test_blank_note_is_rejected(
    app_client: ClientFactory, user_a: User, card: BusinessCard
) -> None:
    async with app_client(user_a) as http:
        res = await http.post(f"/api/contacts/{card.id}/notes", json={"body": "   "})
    assert res.status_code == 422


async def test_delete_note_twice_gives_404(
    app_client: ClientFactory, user_a: User, card: BusinessCard
) -> None:
    async with app_client(user_a) as http:
        created = await http.post(f"/api/contacts/{card.id}/notes", json={"body": "Đã gọi"})
        note_id = created.json()["id"]
        first = await http.delete(f"/api/contacts/{card.id}/notes/{note_id}")
        second = await http.delete(f"/api/contacts/{card.id}/notes/{note_id}")

    assert first.status_code == 204
    assert second.status_code == 404


# --------------------------------------------------------------------------- hàng chờ nhắc việc


async def test_due_puts_overdue_first_and_counts_late_days(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    """Quá hạn nằm trên: thứ trượt lịch từ tuần trước mới là thứ dễ rơi mất."""
    await make_card(db_session, user_a, "Đến hạn hôm nay", follow_up_at=TODAY)
    await make_card(db_session, user_a, "Trễ một tuần", follow_up_at=LAST_WEEK)
    await make_card(db_session, user_a, "Trễ một ngày", follow_up_at=YESTERDAY)

    async with app_client(user_a) as http:
        res = await http.get("/api/contacts/due", params={"on": TODAY.isoformat()})

    body = res.json()
    assert body["on"] == TODAY.isoformat()
    assert body["total"] == 3
    assert [item["display_name"] for item in body["items"]] == [
        "Trễ một tuần",
        "Trễ một ngày",
        "Đến hạn hôm nay",
    ]
    assert [item["overdue_days"] for item in body["items"]] == [7, 1, 0]


async def test_due_skips_future_and_closed(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    """Hẹn tuần sau chưa tới lượt; quan hệ đã dừng thì lời nhắc chỉ là nhiễu."""
    await make_card(db_session, user_a, "Hẹn tuần sau", follow_up_at=NEXT_WEEK)
    await make_card(
        db_session,
        user_a,
        "Đã chốt",
        relationship_status=RelationshipStatus.CLOSED,
        follow_up_at=LAST_WEEK,
    )
    await make_card(db_session, user_a, "Cần gọi", follow_up_at=TODAY)

    async with app_client(user_a) as http:
        res = await http.get("/api/contacts/due", params={"on": TODAY.isoformat()})

    body = res.json()
    assert body["total"] == 1
    assert [item["display_name"] for item in body["items"]] == ["Cần gọi"]


async def test_due_never_shows_another_users_contacts(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User, user_b: User
) -> None:
    await make_card(db_session, user_b, "Liên hệ của B", follow_up_at=LAST_WEEK)

    async with app_client(user_a) as http:
        res = await http.get("/api/contacts/due", params={"on": TODAY.isoformat()})

    assert res.json() == {"on": TODAY.isoformat(), "total": 0, "items": []}


# --------------------------------------------------------------------------- tách dữ liệu (A9)


async def test_other_users_card_is_404_everywhere(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User, user_b: User
) -> None:
    """404 chứ không 403: 403 là tự khai rằng bản ghi đó tồn tại."""
    foreign = await make_card(db_session, user_b, "Của B")

    async with app_client(user_a) as http:
        calls = [
            await http.get(f"/api/contacts/{foreign.id}"),
            await http.patch(f"/api/contacts/{foreign.id}", json={"relationship_status": "closed"}),
            await http.post(f"/api/contacts/{foreign.id}/notes", json={"body": "Chen vào"}),
        ]

    assert [res.status_code for res in calls] == [404, 404, 404]


async def test_cannot_delete_a_note_of_another_user(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User, user_b: User
) -> None:
    foreign = await make_card(db_session, user_b, "Của B")
    mine = await make_card(db_session, user_a, "Của A")

    async with app_client(user_b) as http:
        created = await http.post(f"/api/contacts/{foreign.id}/notes", json={"body": "Riêng tư"})
    note_id = created.json()["id"]

    async with app_client(user_a) as http:
        res = await http.delete(f"/api/contacts/{mine.id}/notes/{note_id}")

    assert res.status_code == 404
