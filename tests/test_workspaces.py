"""F5 — không gian làm việc, vai trò, và vế *cùng nhau* của tiêu chí A9′.

Chủ sở hữu: T | Task: NEXT-05 | xem Task.md

`tests/test_isolation.py` canh vế *cách nhau*: dữ liệu của hai tổ chức không bao giờ gặp nhau.
File này canh nửa còn lại, nửa mà A9 cũ không thể có và vì thế chưa có một ca nào:

- hai người **cùng một không gian** thấy cùng một dữ liệu, và sửa được của nhau;
- vai trò *chỉ xem* đọc được nhưng không ghi được;
- *thành viên* không mời, không gỡ, không đổi vai trò được ai;
- không gian **luôn còn ít nhất một quản trị**;
- gỡ một người thì **dữ liệu ở lại**, chỉ *người phụ trách* trống ra.

Bốn thứ đầu sai được theo hai chiều ngược nhau — hoặc chặn nhầm người trong nhà, hoặc mở cửa
cho người ngoài — nên mỗi ca đều kiểm cả chiều *được phép* lẫn chiều *bị từ chối*.
"""

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.card import BusinessCard, CardStatus
from app.models.company import Company
from app.models.user import User
from app.models.workspace import Role, WorkspaceMember
from app.services.normalize_company import normalize_company_name
from tests.conftest import make_user, workspace_id_of

ClientFactory = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]

NOW = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)


async def seed_card(db: AsyncSession, user: User, name: str) -> BusinessCard:
    """Một danh thiếp đã xác nhận trong không gian của `user`, do chính họ nhập."""
    workspace_id = await workspace_id_of(db, user)
    company = Company(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        user_id=user.id,
        display_name=f"Công ty của {name}",
        name_normalized=normalize_company_name(f"cong ty {uuid.uuid4().hex[:8]}"),
        aliases=[],
    )
    db.add(company)
    await db.flush()
    card = BusinessCard(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        user_id=user.id,
        image_path=f"ws/{uuid.uuid4().hex[:8]}.jpg",
        image_hash=uuid.uuid4().hex * 2,
        uploaded_at=NOW,
        status=CardStatus.CONFIRMED.value,
        company_id=company.id,
        full_name=name,
        language_detected="vi",
    )
    db.add(card)
    await db.flush()
    return card


@pytest.fixture
async def mine(db_session: AsyncSession, user_a: User) -> AsyncIterator[BusinessCard]:
    yield await seed_card(db_session, user_a, "Nguyễn Văn An")


# --------------------------------------------------------- vế *cùng nhau* của A9′


async def test_teammate_sees_and_edits_the_same_data(
    app_client: ClientFactory,
    db_session: AsyncSession,
    mine: BusinessCard,
    teammate: User,
    embedder: object,
) -> None:
    """Đây **là** `NEXT-05`: người thứ hai trong cùng không gian đọc và sửa được thẻ của A.

    Trước `NEXT-05` cả hai lời gọi này trả `404`, và đó từng là hành vi *đúng*. Ca này khoá lại
    chiều ngược: một bộ lọc sót lại theo `user_id` ở bất kỳ đâu sẽ làm nó đỏ.
    """
    async with app_client(teammate) as http:
        seen = await http.get(f"/api/cards/{mine.id}")
        edited = await http.patch(f"/api/cards/{mine.id}", json={"job_title": "Giám đốc"})

    assert (seen.status_code, edited.status_code) == (200, 200)
    assert seen.json()["full_name"] == "Nguyễn Văn An"
    await db_session.refresh(mine)
    assert mine.job_title == "Giám đốc"
    # Người *nhập* không đổi khi người khác sửa: `user_id` là lịch sử, không phải quyền.
    assert mine.user_id != teammate.id


async def test_teammate_sees_the_same_lists_and_stats(
    app_client: ClientFactory, mine: BusinessCard, teammate: User
) -> None:
    async with app_client(teammate) as http:
        cards = (await http.get("/api/cards")).json()
        companies = (await http.get("/api/companies")).json()
        stats = (await http.get("/api/stats")).json()

    assert [item["id"] for item in cards["items"]] == [str(mine.id)]
    assert companies["total"] == 1
    assert stats["total_cards"] == 1


async def test_a_second_person_cannot_rescan_the_same_image(
    db_session: AsyncSession, user_a: User, mine: BusinessCard, teammate: User
) -> None:
    """Trùng ảnh tính trong **phạm vi không gian**, nên đồng nghiệp quét lại là bị chặn.

    Đây là mặt trái của ca cùng tên ở `test_security.py`: ở đó *tổ chức khác* quét trùng thì
    **được**. Hai ca phải cùng đúng, và index unique `(workspace_id, image_hash)` của revision
    `0015` là thứ giữ cả hai.
    """
    from app.repositories import card as card_repo

    workspace_id = await workspace_id_of(db_session, teammate)
    assert workspace_id == await workspace_id_of(db_session, user_a)

    found = await card_repo.get_by_hash(db_session, mine.image_hash, workspace_id=workspace_id)
    assert found is not None and found.id == mine.id


# --------------------------------------------------------- vai trò *chỉ xem*


async def test_viewer_reads_but_cannot_write(
    app_client: ClientFactory, db_session: AsyncSession, mine: BusinessCard, viewer: User
) -> None:
    """Chỉ xem: `GET` được `200`, mọi đường ghi được `403` — và dữ liệu không suy suyển."""
    async with app_client(viewer) as http:
        read = await http.get(f"/api/cards/{mine.id}")
        patched = await http.patch(f"/api/cards/{mine.id}", json={"job_title": "Bị sửa"})
        confirmed = await http.post(f"/api/cards/{mine.id}/confirm")
        removed = await http.delete(f"/api/cards/{mine.id}")

    assert read.status_code == 200
    assert (patched.status_code, confirmed.status_code, removed.status_code) == (403, 403, 403)
    await db_session.refresh(mine)
    assert mine.job_title is None


async def test_viewer_cannot_write_through_the_other_routers(
    app_client: ClientFactory, mine: BusinessCard, viewer: User
) -> None:
    """403 phải đến từ **tầng dependency**, nếu không mỗi router mới lại là một lỗ mới.

    Gom nhiều router vào một ca là có chủ đích: điều đang kiểm không phải nghiệp vụ của từng
    endpoint mà là *không endpoint ghi nào quên khai `WriterWorkspace`*.
    """
    async with app_client(viewer) as http:
        responses = {
            "contacts": await http.patch(
                f"/api/contacts/{mine.id}", json={"relationship_status": "contacted"}
            ),
            "notes": await http.post(f"/api/contacts/{mine.id}/notes", json={"body": "thử"}),
            "duplicates": await http.post(
                "/api/duplicates/merge",
                json={"keep_id": str(mine.id), "merge_ids": [str(uuid.uuid4())]},
            ),
            "reindex": await http.post("/api/kb/reindex"),
            "retention": await http.put("/api/privacy/retention", json={"days": 90}),
        }

    assert {name: res.status_code for name, res in responses.items()} == {
        "contacts": 403,
        "notes": 403,
        "duplicates": 403,
        "reindex": 403,
        "retention": 403,
    }


async def test_viewer_can_still_read_everything(
    app_client: ClientFactory,
    db_session: AsyncSession,
    mine: BusinessCard,
    viewer: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chỉ xem vẫn **đọc** được, kể cả qua export. Chặn cả đọc là hiểu sai vai trò này.

    `export.py` tự mở session bằng `SessionLocal()` vì nó trả `StreamingResponse` — thân response
    chạy sau khi dependency đã đóng session. Test phải trỏ nó về đúng transaction đang chạy, cùng
    cách `test_isolation.py` làm.
    """
    from app.routers import export

    class SameSession:
        def __call__(self) -> "SameSession":
            return self

        async def __aenter__(self) -> AsyncSession:
            return db_session

        async def __aexit__(self, *exc: object) -> bool:
            return False

    monkeypatch.setattr(export, "SessionLocal", SameSession())

    async with app_client(viewer) as http:
        exported = await http.get("/api/export/cards.json")
        stats = await http.get("/api/stats")

    assert (exported.status_code, stats.status_code) == (200, 200)
    assert str(mine.id) in exported.text
    assert stats.json()["total_cards"] == 1


# --------------------------------------------------------- quyền quản trị


async def test_member_cannot_invite_remove_or_change_roles(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User, teammate: User
) -> None:
    workspace_id = await workspace_id_of(db_session, teammate)
    async with app_client(teammate) as http:
        invited = await http.post(
            f"/api/workspaces/{workspace_id}/members",
            json={"email": "ai-do@vidu.vn", "role": "member"},
        )
        changed = await http.patch(
            f"/api/workspaces/{workspace_id}/members/{user_a.id}", json={"role": "viewer"}
        )
        removed = await http.delete(f"/api/workspaces/{workspace_id}/members/{user_a.id}")
        renamed = await http.patch(
            f"/api/workspaces/{workspace_id}", json={"name": "Tên do thành viên đặt"}
        )

    assert [res.status_code for res in (invited, changed, removed, renamed)] == [403, 403, 403, 403]
    assert await db_session.get(WorkspaceMember, (workspace_id, user_a.id)) is not None


async def test_member_still_sees_who_else_is_in_the_workspace(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User, teammate: User
) -> None:
    """Xem danh sách thành viên **không** phải quyền riêng của quản trị — xem ghi chú ở router."""
    workspace_id = await workspace_id_of(db_session, teammate)
    async with app_client(teammate) as http:
        response = await http.get(f"/api/workspaces/{workspace_id}/members")

    body = response.json()
    assert response.status_code == 200
    assert body["my_role"] == Role.MEMBER
    assert {item["user_id"] for item in body["items"]} == {str(user_a.id), str(teammate.id)}


async def test_admin_invites_an_existing_account_and_it_can_read_at_once(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User, mine: BusinessCard
) -> None:
    outsider = await make_user(db_session, "nguoi-moi@example.com", "Người mới")
    workspace_id = await workspace_id_of(db_session, user_a)

    async with app_client(user_a) as http:
        invited = await http.post(
            f"/api/workspaces/{workspace_id}/members",
            json={"email": "NGUOI-MOI@example.com", "role": "member"},
        )
    assert invited.status_code == 201

    # Chưa chuyển không gian thì vẫn ở chỗ cũ — lời mời không được tự kéo người ta đi nơi khác.
    async with app_client(outsider) as http:
        before = (await http.get("/api/cards")).json()
        await http.post(f"/api/workspaces/{workspace_id}/activate")
        after = (await http.get("/api/cards")).json()

    assert before["total"] == 0
    assert [item["id"] for item in after["items"]] == [str(mine.id)]


async def test_inviting_an_unknown_email_is_404_and_says_what_to_do(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    workspace_id = await workspace_id_of(db_session, user_a)
    async with app_client(user_a) as http:
        response = await http.post(
            f"/api/workspaces/{workspace_id}/members",
            json={"email": "chua-dang-ky@vidu.vn", "role": "member"},
        )
    assert response.status_code == 404
    assert "đăng ký" in response.json()["detail"]


async def test_inviting_someone_already_inside_is_409(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User, teammate: User
) -> None:
    workspace_id = await workspace_id_of(db_session, user_a)
    async with app_client(user_a) as http:
        response = await http.post(
            f"/api/workspaces/{workspace_id}/members",
            json={"email": teammate.email, "role": "viewer"},
        )
    assert response.status_code == 409
    member = await db_session.get(WorkspaceMember, (workspace_id, teammate.id))
    assert member is not None and member.role == Role.MEMBER


# --------------------------------------------------------- quản trị cuối cùng


async def test_the_last_admin_cannot_leave_or_demote_themselves(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User, teammate: User
) -> None:
    """Không đường nào đưa một tổ chức về trạng thái không ai mời được ai nữa."""
    workspace_id = await workspace_id_of(db_session, user_a)
    async with app_client(user_a) as http:
        demoted = await http.patch(
            f"/api/workspaces/{workspace_id}/members/{user_a.id}", json={"role": "member"}
        )
        left = await http.delete(f"/api/workspaces/{workspace_id}/members/{user_a.id}")

    assert (demoted.status_code, left.status_code) == (409, 409)
    member = await db_session.get(WorkspaceMember, (workspace_id, user_a.id))
    assert member is not None and member.role == Role.ADMIN


async def test_a_second_admin_unlocks_leaving(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User, teammate: User
) -> None:
    workspace_id = await workspace_id_of(db_session, user_a)
    async with app_client(user_a) as http:
        promoted = await http.patch(
            f"/api/workspaces/{workspace_id}/members/{teammate.id}", json={"role": "admin"}
        )
        left = await http.delete(f"/api/workspaces/{workspace_id}/members/{user_a.id}")

    assert (promoted.status_code, left.status_code) == (200, 204)
    assert await db_session.get(WorkspaceMember, (workspace_id, user_a.id)) is None


# --------------------------------------------------------- người phụ trách


async def test_assigning_a_contact_to_a_teammate(
    app_client: ClientFactory, db_session: AsyncSession, mine: BusinessCard, teammate: User
) -> None:
    async with app_client(teammate) as http:
        assigned = await http.patch(
            f"/api/contacts/{mine.id}", json={"assigned_to_user_id": str(teammate.id)}
        )

    assert assigned.status_code == 200
    assert assigned.json()["assigned_to_name"] == "Đồng nghiệp"
    await db_session.refresh(mine)
    assert mine.assigned_to_user_id == teammate.id


async def test_cannot_assign_to_someone_outside_the_workspace(
    app_client: ClientFactory, db_session: AsyncSession, mine: BusinessCard, user_b: User
) -> None:
    """Không kiểm thì cột ấy nhận mọi UUID, và liên hệ hiện ra là đang giao cho người không mở nổi nó."""
    async with app_client(mine.user_id and await _owner(db_session, mine)) as http:
        response = await http.patch(
            f"/api/contacts/{mine.id}", json={"assigned_to_user_id": str(user_b.id)}
        )

    assert response.status_code == 404
    await db_session.refresh(mine)
    assert mine.assigned_to_user_id is None


async def test_removing_a_member_keeps_their_data_and_clears_the_assignment(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User, teammate: User
) -> None:
    """Gỡ người: danh thiếp **họ nhập** ở lại với tổ chức, chỉ *người phụ trách* trống ra.

    Trả về trống chứ không giao sang quản trị — liên hệ chưa giao thì có người nhận, còn liên hệ
    bị giao âm thầm thì nằm im trong danh sách của người không biết mình đang giữ nó.
    """
    theirs = await seed_card(db_session, teammate, "Do đồng nghiệp nhập")
    theirs.assigned_to_user_id = teammate.id
    await db_session.flush()
    workspace_id = await workspace_id_of(db_session, user_a)

    async with app_client(user_a) as http:
        removed = await http.delete(f"/api/workspaces/{workspace_id}/members/{teammate.id}")
        remaining = (await http.get("/api/cards")).json()

    assert removed.status_code == 204
    await db_session.refresh(theirs)
    assert theirs.workspace_id == workspace_id
    assert theirs.user_id == teammate.id  # lịch sử "ai nhập" không bị xoá
    assert theirs.assigned_to_user_id is None
    assert str(theirs.id) in {item["id"] for item in remaining["items"]}


async def test_a_removed_member_loses_access_immediately(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User, mine: BusinessCard
) -> None:
    """`active_workspace_id` còn trỏ vào đó cũng không đủ — mỗi request tra lại bảng thành viên.

    Đây là luật 1 của `core/workspace.py`. Bỏ bước tra lại thì người vừa bị gỡ vẫn đọc được dữ
    liệu cho tới khi họ tự đổi không gian, và không ai trong tổ chức biết.
    """
    ejected = await make_user(
        db_session,
        "bi-go@example.com",
        "Bị gỡ",
        workspace_id=await workspace_id_of(db_session, user_a),
    )
    workspace_id = await workspace_id_of(db_session, user_a)

    async with app_client(ejected) as http:
        before = await http.get(f"/api/cards/{mine.id}")
    async with app_client(user_a) as http:
        await http.delete(f"/api/workspaces/{workspace_id}/members/{ejected.id}")
    async with app_client(ejected) as http:
        after = await http.get(f"/api/cards/{mine.id}")
        listed = await http.get("/api/cards")

    assert before.status_code == 200
    # Không còn không gian nào → `409`, không phải `403`: thiếu một bước khởi tạo, không phải
    # thiếu quyền. Giao diện cần phân biệt để mời họ tạo không gian mới.
    assert (after.status_code, listed.status_code) == (409, 409)


# --------------------------------------------------------- không gian riêng


async def test_creating_a_workspace_switches_to_it_and_it_starts_empty(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User, mine: BusinessCard
) -> None:
    async with app_client(user_a) as http:
        created = (await http.post("/api/workspaces", json={"name": "  Phòng  kinh doanh "})).json()
        cards = (await http.get("/api/cards")).json()
        listed = (await http.get("/api/workspaces")).json()

    assert created["name"] == "Phòng kinh doanh"  # khoảng trắng gộp lại
    assert (created["role"], created["is_active"]) == (Role.ADMIN, True)
    assert cards["total"] == 0
    assert listed["active_id"] == created["id"]
    assert len(listed["items"]) == 2


async def test_cannot_activate_a_workspace_you_are_not_in(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User, workspace_b: uuid.UUID
) -> None:
    """`404`, không phải `403` — `403` là tự khai rằng không gian đó có tồn tại."""
    async with app_client(user_a) as http:
        stranger = await http.post(f"/api/workspaces/{workspace_b}/activate")
        missing = await http.post(f"/api/workspaces/{uuid.uuid4()}/activate")

    assert (stranger.status_code, missing.status_code) == (404, 404)
    assert stranger.json()["detail"] == missing.json()["detail"]
    await db_session.refresh(user_a)
    assert user_a.active_workspace_id != workspace_b


async def test_member_endpoints_only_touch_the_active_workspace(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User, workspace_b: uuid.UUID
) -> None:
    """Truyền id của một không gian *khác* thì `409`, kể cả khi mình là quản trị ở đó.

    Nhận id bất kỳ nghĩa là mỗi endpoint phải tự kiểm tư cách thành viên một lần nữa, và chỉ cần
    một chỗ quên là rò — xem `routers/workspaces.py::_require_active`.
    """
    async with app_client(user_a) as http:
        second = (await http.post("/api/workspaces", json={"name": "Không gian hai"})).json()
        first = await db_session.scalar(
            select(WorkspaceMember.workspace_id)
            .where(WorkspaceMember.user_id == user_a.id)
            .order_by(WorkspaceMember.joined_at)
            .limit(1)
        )
        # Đang mở `second`, hỏi về `first` — là không gian của chính mình, và vẫn bị từ chối.
        response = await http.get(f"/api/workspaces/{first}/members")

    assert second["is_active"] is True
    assert response.status_code == 409


async def _owner(db: AsyncSession, card: BusinessCard) -> User:
    user = await db.get(User, card.user_id)
    assert user is not None
    return user
