"""Câu SQL cho không gian làm việc và danh sách thành viên.

Chủ sở hữu: T | Task: NEXT-05 | xem Task.md

`core/workspace.py` quyết định request **được** chạm vào không gian nào; file này là chỗ duy
nhất **đọc ghi** hai bảng ấy. Ba điều đáng nhớ:

1. **Không gian nào cũng phải còn ít nhất một quản trị.** Gỡ hay hạ vai trò người cuối cùng là
   tạo ra một tổ chức không ai mời được ai nữa, và không đường nào sửa từ trong ứng dụng. Hai
   hàm `remove_member()` / `set_role()` cùng gọi `admin_count()` trước khi ghi.
2. **Gỡ người thì dữ liệu ở lại.** Đó là toàn bộ điểm của `NEXT-05`: danh thiếp thuộc tổ chức.
   Việc duy nhất phải dọn là *người phụ trách* — xem `handover()`.
3. **`users.active_workspace_id` chỉ là chỗ ghi nhớ.** Gỡ một người thì trỏ nó sang không gian
   khác họ còn chân, hoặc `NULL`; `core/workspace.py` vẫn tra lại bảng thành viên mỗi request
   nên bước này là để giao diện không mở nhầm màn hình, không phải để chặn truy cập.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.card import BusinessCard
from app.models.user import User
from app.models.workspace import Role, Workspace, WorkspaceMember


async def create(
    db: AsyncSession, *, name: str, owner_id: uuid.UUID, activate: bool = True
) -> Workspace:
    """Không gian mới, người tạo làm quản trị."""
    workspace = Workspace(id=uuid.uuid4(), name=name)
    db.add(workspace)
    await db.flush()
    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=owner_id, role=Role.ADMIN))
    if activate:
        await set_active(db, user_id=owner_id, workspace_id=workspace.id)
    await db.flush()
    return workspace


async def rename(db: AsyncSession, workspace_id: uuid.UUID, name: str) -> Workspace | None:
    workspace = await db.get(Workspace, workspace_id)
    if workspace is None:
        return None
    workspace.name = name
    await db.flush()
    return workspace


async def list_for_user(
    db: AsyncSession, user_id: uuid.UUID
) -> Sequence[tuple[Workspace, str, int]]:
    """Mọi không gian của một người, kèm vai trò và số thành viên, cũ nhất trước.

    Đếm thành viên bằng truy vấn con chứ không `JOIN` rồi `GROUP BY`: `JOIN` nhân dòng lên theo
    số thành viên, và bất kỳ cột nào thêm vào sau này cũng phải nhét vào `GROUP BY` theo.
    """
    # Bí danh riêng cho truy vấn con, **không** dùng lại `WorkspaceMember` của câu `JOIN` bên
    # ngoài: dùng lại thì SQLAlchemy tự tương quan luôn cả bảng ấy, truy vấn con mất sạch mệnh đề
    # `FROM` và câu lệnh không biên dịch nổi (`InvalidRequestError: returned no FROM clauses`).
    counted = aliased(WorkspaceMember)
    member_count = (
        select(func.count())
        .select_from(counted)
        .where(counted.workspace_id == Workspace.id)
        .scalar_subquery()
    )
    rows = await db.execute(
        select(Workspace, WorkspaceMember.role, member_count)
        .join(
            WorkspaceMember,
            (WorkspaceMember.workspace_id == Workspace.id) & (WorkspaceMember.user_id == user_id),
        )
        .order_by(WorkspaceMember.joined_at, Workspace.id)
    )
    return list(rows.tuples().all())


async def members(db: AsyncSession, workspace_id: uuid.UUID) -> Sequence[tuple[User, str]]:
    """Thành viên của một không gian, quản trị trước rồi tới thứ tự vào."""
    rows = await db.execute(
        select(User, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
        .where(WorkspaceMember.workspace_id == workspace_id)
        .order_by((WorkspaceMember.role != Role.ADMIN), WorkspaceMember.joined_at)
    )
    return list(rows.tuples().all())


async def add_member(
    db: AsyncSession, *, workspace_id: uuid.UUID, user_id: uuid.UUID, role: str
) -> WorkspaceMember:
    member = WorkspaceMember(workspace_id=workspace_id, user_id=user_id, role=role)
    db.add(member)
    await db.flush()
    return member


async def admin_count(db: AsyncSession, workspace_id: uuid.UUID) -> int:
    count = await db.scalar(
        select(func.count())
        .select_from(WorkspaceMember)
        .where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.role == Role.ADMIN,
        )
    )
    return int(count or 0)


async def set_role(
    db: AsyncSession, *, workspace_id: uuid.UUID, user_id: uuid.UUID, role: str
) -> bool:
    """Đổi vai trò. Trả `False` nếu việc đó bỏ lại một workspace không còn quản trị nào.

    Từ `I-37` router chặn **mọi** lượt tự đổi vai của chính mình, nên qua HTTP thì nhánh
    `False` ở đây không còn với tới được: người gọi luôn là quản trị, và họ chỉ hạ vai được
    người khác — tức là đã có sẵn hai quản trị. Giữ lại vì đây là hàng rào của **tầng
    repository**: `scripts/` hay một màn hình quản trị sau này gọi thẳng vào đây thì vẫn
    không đưa được tổ chức về trạng thái không ai mời được ai.
    """
    member = await db.get(WorkspaceMember, (workspace_id, user_id))
    if member is None:
        return False
    if member.role == Role.ADMIN and role != Role.ADMIN and await admin_count(db, workspace_id) < 2:
        return False
    member.role = role
    await db.flush()
    return True


async def remove_member(db: AsyncSession, *, workspace_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    """Gỡ một người khỏi không gian. Trả `False` nếu đó là quản trị cuối cùng.

    **Dữ liệu họ đã nhập ở lại nguyên vẹn** — `business_cards.user_id` vẫn trỏ vào họ để câu
    "ai nhập bản ghi này" còn trả lời được. Chỉ *người phụ trách* được trả về trống, vì một
    người đã rời tổ chức thì không phụ trách được liên hệ nào nữa.
    """
    member = await db.get(WorkspaceMember, (workspace_id, user_id))
    if member is None:
        return False
    if member.role == Role.ADMIN and await admin_count(db, workspace_id) < 2:
        return False

    await handover(db, workspace_id=workspace_id, user_id=user_id)
    await db.delete(member)
    await db.flush()
    await _drop_active(db, user_id=user_id, workspace_id=workspace_id)
    return True


async def handover(db: AsyncSession, *, workspace_id: uuid.UUID, user_id: uuid.UUID) -> int:
    """Trả về trống mọi liên hệ đang giao cho `user_id` trong không gian này.

    Trả trống chứ không giao sang quản trị: một liên hệ *không có người phụ trách* hiện lên
    trong bộ lọc "chưa giao" để ai đó nhận, còn một liên hệ bị giao âm thầm cho quản trị thì
    nằm im trong danh sách của người không biết mình đang giữ nó.
    """
    result = await db.execute(
        update(BusinessCard)
        .where(
            BusinessCard.workspace_id == workspace_id,
            BusinessCard.assigned_to_user_id == user_id,
        )
        .values(assigned_to_user_id=None)
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def set_active(
    db: AsyncSession, *, user_id: uuid.UUID, workspace_id: uuid.UUID | None
) -> None:
    await db.execute(
        update(User).where(User.id == user_id).values(active_workspace_id=workspace_id)
    )


async def _drop_active(db: AsyncSession, *, user_id: uuid.UUID, workspace_id: uuid.UUID) -> None:
    """Người vừa bị gỡ đang mở đúng không gian đó thì chuyển họ sang nơi khác, hoặc `NULL`."""
    user = await db.get(User, user_id)
    if user is None or user.active_workspace_id != workspace_id:
        return
    fallback = await db.scalar(
        select(WorkspaceMember.workspace_id)
        .where(WorkspaceMember.user_id == user_id)
        .order_by(WorkspaceMember.joined_at)
        .limit(1)
    )
    user.active_workspace_id = fallback
    await db.flush()


async def delete_workspace(db: AsyncSession, workspace_id: uuid.UUID) -> None:
    """Xoá cả không gian và **toàn bộ dữ liệu trong đó** (`ON DELETE CASCADE` của `0015`)."""
    await db.execute(delete(Workspace).where(Workspace.id == workspace_id))
    await db.flush()
