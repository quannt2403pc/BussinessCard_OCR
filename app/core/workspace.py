"""Không gian làm việc đang mở của request, và kiểm vai trò.

Chủ sở hữu: T | Task: NEXT-05 | xem Task.md

**Đây là chỗ duy nhất quyết định một request được đọc/ghi dữ liệu của không gian nào.** Từ
`NEXT-05`, mọi câu lọc dữ liệu đi qua `workspace_id` lấy từ đây, chứ không còn qua `user_id`.

Ba luật, mỗi luật chặn một đường hỏng đã thấy trước:

1. **Không gian đang mở phải được xác nhận là người đó thật sự có chân trong đó**, mỗi request.
   `users.active_workspace_id` chỉ là *chỗ ghi nhớ*, không phải bằng chứng — bị gỡ khỏi không
   gian xong mà cột ấy còn trỏ vào đó thì người vừa bị gỡ vẫn đọc được dữ liệu cho tới khi họ
   tự đổi. Nên mỗi lượt đều tra lại bảng thành viên.
2. **Người chưa ở không gian nào thì `RequireWorkspace` ném `409`, không phải `403`.** Đây
   không phải thiếu quyền mà là thiếu một bước khởi tạo, và giao diện cần phân biệt để mời họ
   tạo không gian thay vì báo "bạn không có quyền".
3. **Quyền ghi kiểm bằng tập hợp vai trò**, không so chuỗi rải rác — xem `WRITER_ROLES`.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import CurrentUser
from app.models.user import User
from app.models.workspace import WRITER_ROLES, Role, Workspace, WorkspaceMember

logger = logging.getLogger(__name__)

NO_WORKSPACE_DETAIL = "Bạn chưa ở không gian làm việc nào."
READ_ONLY_DETAIL = "Vai trò chỉ xem không sửa được dữ liệu."
ADMIN_ONLY_DETAIL = "Chỉ quản trị không gian làm việc mới làm được việc này."


@dataclass(frozen=True, slots=True)
class ActiveWorkspace:
    """Không gian đang mở của request, **đã xác nhận tư cách thành viên**."""

    id: uuid.UUID
    name: str
    role: str

    @property
    def can_write(self) -> bool:
        return self.role in WRITER_ROLES

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN


async def membership(
    db: AsyncSession, *, user_id: uuid.UUID, workspace_id: uuid.UUID
) -> WorkspaceMember | None:
    return await db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )


async def resolve(db: AsyncSession, user: User) -> ActiveWorkspace | None:
    """Không gian đang mở, hoặc `None` nếu người dùng chưa ở không gian nào.

    `active_workspace_id` trỏ vào một không gian họ **không còn** là thành viên thì rơi về
    không gian bất kỳ mà họ còn chân, thay vì trả về nó: cột ghi nhớ không phải bằng chứng.
    """
    if user.active_workspace_id is not None:
        found = await db.execute(
            select(Workspace, WorkspaceMember.role)
            .join(
                WorkspaceMember,
                (WorkspaceMember.workspace_id == Workspace.id)
                & (WorkspaceMember.user_id == user.id),
            )
            .where(Workspace.id == user.active_workspace_id)
        )
        row = found.first()
        if row is not None:
            workspace, role = row
            return ActiveWorkspace(id=workspace.id, name=workspace.name, role=role)
        logger.info(
            "Không gian đang mở %s không còn thuộc về %s — rơi về không gian khác",
            user.active_workspace_id,
            user.id,
        )

    fallback = await db.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
        .order_by(WorkspaceMember.joined_at, Workspace.id)
        .limit(1)
    )
    row = fallback.first()
    if row is None:
        return None
    workspace, role = row
    return ActiveWorkspace(id=workspace.id, name=workspace.name, role=role)


async def require_workspace(
    user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]
) -> ActiveWorkspace:
    active = await resolve(db, user)
    if active is None:
        raise HTTPException(status.HTTP_409_CONFLICT, NO_WORKSPACE_DETAIL)
    return active


async def require_writer(
    workspace: Annotated[ActiveWorkspace, Depends(require_workspace)],
) -> ActiveWorkspace:
    """Chặn vai trò *chỉ xem* ở mọi endpoint ghi.

    Đặt ở tầng dependency chứ không rải `if` trong từng hàm: một endpoint ghi mới quên kiểm là
    một lỗ hổng, mà quên khai một dependency thì lộ ra ngay lần chạy test đầu tiên.
    """
    if not workspace.can_write:
        raise HTTPException(status.HTTP_403_FORBIDDEN, READ_ONLY_DETAIL)
    return workspace


async def require_admin(
    workspace: Annotated[ActiveWorkspace, Depends(require_workspace)],
) -> ActiveWorkspace:
    if not workspace.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, ADMIN_ONLY_DETAIL)
    return workspace


#: Kiểu dùng lại trong khai báo route.
CurrentWorkspace = Annotated[ActiveWorkspace, Depends(require_workspace)]
WriterWorkspace = Annotated[ActiveWorkspace, Depends(require_writer)]
AdminWorkspace = Annotated[ActiveWorkspace, Depends(require_admin)]
