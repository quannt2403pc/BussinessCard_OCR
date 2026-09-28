"""F5 — không gian làm việc: danh sách, tạo, đổi tên, chuyển, và quản lý thành viên.

Chủ sở hữu: T | Task: NEXT-05 | xem Task.md

Đây là màn hình duy nhất của `NEXT-05` mà người dùng *nhìn thấy*; phần còn lại của task là đổi
khoá tách dữ liệu bên dưới. Bốn quyết định đáng ghi lại:

1. **Mời bằng email của tài khoản đã có, không gửi thư mời.** Bản demo chưa có xác thực email
   (`Plan.md` mục 1.3 — nằm ngoài phạm vi), nên một lời mời gửi tới địa chỉ gõ nhầm sẽ mở dữ
   liệu của cả tổ chức cho người lạ. Email chưa có tài khoản thì trả `404` kèm lời nhắc họ đăng
   ký trước.
2. **Không gian luôn còn ít nhất một quản trị.** `repositories/workspace.py` chặn, router chỉ
   dịch câu trả lời `False` sang `409`. Không có nút nào đưa được tổ chức về trạng thái không
   ai mời được ai.
3. **Gỡ người thì dữ liệu ở lại**, chỉ *người phụ trách* được trả về trống — xem `handover()`.
4. **Không có nút xoá không gian trên giao diện.** `delete_workspace()` có sẵn cho người vận
   hành, nhưng một nút xoá sạch dữ liệu của cả tổ chức sau hai cú bấm là thứ không đáng có
   trong bản demo. Ai thật sự cần thì gọi thẳng repository.
"""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import CurrentUser
from app.core.templates import templates
from app.core.workspace import AdminWorkspace, CurrentWorkspace, membership
from app.models.user import User
from app.models.workspace import Role
from app.repositories import user as user_repo
from app.repositories import workspace as workspace_repo
from app.schemas.workspace import (
    InviteIn,
    MemberListOut,
    MemberOut,
    RoleIn,
    WorkspaceIn,
    WorkspaceListOut,
    WorkspaceOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["workspaces"])

Session = Annotated[AsyncSession, Depends(get_db)]

LAST_ADMIN_DETAIL = "Workspace phải còn ít nhất một quản trị."
NOT_A_MEMBER_DETAIL = "Người này không ở trong workspace."
SELF_ROLE_DETAIL = "Không tự đổi vai trò của mình được. Nhờ một quản trị khác đổi giúp."


@router.get("/workspaces", response_class=HTMLResponse, tags=["ui"])
async def workspaces_page(request: Request, user: CurrentUser) -> HTMLResponse:
    """Trang quản lý không gian làm việc và thành viên. Dữ liệu do JS gọi API bên dưới."""
    return templates.TemplateResponse(request, "workspaces.html", {"user": user})


# --------------------------------------------------------------------------- không gian


@router.get("/api/workspaces", response_model=WorkspaceListOut)
async def list_workspaces(db: Session, user: CurrentUser) -> WorkspaceListOut:
    """Mọi không gian người này có chân, kèm vai trò của chính họ trong từng cái.

    Không đòi `CurrentWorkspace`: người vừa bị gỡ khỏi không gian cuối cùng phải mở được màn
    hình này để tạo cái mới, mà `require_workspace` thì chặn họ bằng `409`.
    """
    rows = await workspace_repo.list_for_user(db, user.id)
    return WorkspaceListOut(
        active_id=user.active_workspace_id,
        items=[
            WorkspaceOut(
                id=workspace.id,
                name=workspace.name,
                role=Role(role),
                member_count=member_count,
                is_active=workspace.id == user.active_workspace_id,
                created_at=workspace.created_at,
            )
            for workspace, role, member_count in rows
        ],
    )


@router.post("/api/workspaces", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
async def create_workspace(payload: WorkspaceIn, db: Session, user: CurrentUser) -> WorkspaceOut:
    """Tạo không gian mới và **chuyển sang nó luôn**.

    Chuyển luôn vì người vừa bấm "Tạo" gần như chắc chắn định làm việc trong đó ngay; bắt họ
    bấm thêm một nút *Chuyển* nữa chỉ để đúng lý thuyết thì chẳng phục vụ ai.
    """
    workspace = await workspace_repo.create(db, name=payload.name, owner_id=user.id)
    await db.commit()
    logger.info("Người dùng %s tạo không gian %s (%r)", user.id, workspace.id, workspace.name)
    return WorkspaceOut(
        id=workspace.id,
        name=workspace.name,
        role=Role.ADMIN,
        member_count=1,
        is_active=True,
        created_at=workspace.created_at,
    )


@router.patch("/api/workspaces/{workspace_id}", response_model=WorkspaceOut)
async def rename_workspace(
    workspace_id: uuid.UUID,
    payload: WorkspaceIn,
    db: Session,
    user: CurrentUser,
    workspace: AdminWorkspace,
) -> WorkspaceOut:
    """Đổi tên **không gian đang mở**, và chỉ quản trị mới đổi được."""
    _require_active(workspace_id, workspace.id)
    renamed = await workspace_repo.rename(db, workspace_id, payload.name)
    if renamed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không có workspace này.")
    await db.commit()
    return WorkspaceOut(
        id=renamed.id,
        name=renamed.name,
        role=Role(workspace.role),
        member_count=len(await workspace_repo.members(db, workspace_id)),
        is_active=True,
        created_at=renamed.created_at,
    )


@router.post("/api/workspaces/{workspace_id}/activate", response_model=WorkspaceOut)
async def activate_workspace(
    workspace_id: uuid.UUID, db: Session, user: CurrentUser
) -> WorkspaceOut:
    """Chuyển sang một không gian khác.

    Tra lại bảng thành viên trước khi ghi: `active_workspace_id` là thứ mọi request sau đó đọc,
    nên đây là chỗ duy nhất một id lạ có thể chui vào. `404` chứ không `403` — cùng lý lẽ với
    danh thiếp của tổ chức khác (`Plan.md` mục 4): `403` là tự khai rằng nó có tồn tại.
    """
    member = await membership(db, user_id=user.id, workspace_id=workspace_id)
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không có workspace này.")

    await workspace_repo.set_active(db, user_id=user.id, workspace_id=workspace_id)
    await db.commit()
    rows = {row[0].id: row for row in await workspace_repo.list_for_user(db, user.id)}
    workspace, role, member_count = rows[workspace_id]
    return WorkspaceOut(
        id=workspace.id,
        name=workspace.name,
        role=Role(role),
        member_count=member_count,
        is_active=True,
        created_at=workspace.created_at,
    )


# --------------------------------------------------------------------------- thành viên


@router.get("/api/workspaces/{workspace_id}/members", response_model=MemberListOut)
async def list_members(
    workspace_id: uuid.UUID, db: Session, user: CurrentUser, workspace: CurrentWorkspace
) -> MemberListOut:
    """Thành viên của **không gian đang mở**. Ai cũng xem được, kể cả vai trò chỉ xem.

    Biết mình đang làm chung với ai không phải là quyền riêng của quản trị — mà giấu đi thì
    người dùng không hiểu nổi vì sao dữ liệu của mình có người khác sửa.
    """
    _require_active(workspace_id, workspace.id)
    rows = await workspace_repo.members(db, workspace_id)
    return MemberListOut(
        workspace_id=workspace.id,
        name=workspace.name,
        my_role=Role(workspace.role),
        items=[
            MemberOut(
                user_id=member.id,
                email=member.email,
                display_name=member.display_name,
                role=Role(role),
                is_me=member.id == user.id,
            )
            for member, role in rows
        ],
    )


@router.post(
    "/api/workspaces/{workspace_id}/members",
    response_model=MemberOut,
    status_code=status.HTTP_201_CREATED,
)
async def invite_member(
    workspace_id: uuid.UUID,
    payload: InviteIn,
    db: Session,
    user: CurrentUser,
    workspace: AdminWorkspace,
) -> MemberOut:
    """Thêm một **tài khoản đã có** vào không gian đang mở — xem ghi chú 1 ở đầu file."""
    _require_active(workspace_id, workspace.id)
    invited = await user_repo.get_by_email(db, payload.email)
    if invited is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Chưa có tài khoản nào dùng {payload.email}. Nhờ họ đăng ký trước rồi mời lại.",
        )
    if await membership(db, user_id=invited.id, workspace_id=workspace_id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Người này đã ở trong workspace rồi.")

    await workspace_repo.add_member(
        db, workspace_id=workspace_id, user_id=invited.id, role=payload.role
    )
    await db.commit()
    logger.info(
        "Quản trị %s thêm %s vào không gian %s với vai trò %s",
        user.id,
        invited.id,
        workspace_id,
        payload.role,
    )
    return MemberOut(
        user_id=invited.id,
        email=invited.email,
        display_name=invited.display_name,
        role=payload.role,
        is_me=invited.id == user.id,
    )


@router.patch("/api/workspaces/{workspace_id}/members/{member_id}", response_model=MemberOut)
async def change_role(
    workspace_id: uuid.UUID,
    member_id: uuid.UUID,
    payload: RoleIn,
    db: Session,
    user: CurrentUser,
    workspace: AdminWorkspace,
) -> MemberOut:
    """Đổi vai trò một thành viên — **người khác**, không phải chính mình (`I-37`).

    Quản trị tự hạ vai mình xuống *thành viên* là một cú bấm **không có đường lùi**: ngay sau đó
    họ mất luôn quyền tự nâng lại, và nếu là quản trị duy nhất thì cả tổ chức không còn ai mời
    được ai. Luật "còn ít nhất một quản trị" chỉ chặn được trường hợp cuối cùng ấy, không chặn
    được một tổ chức hai quản trị mà một người bấm nhầm.

    Rời hẳn khỏi workspace thì vẫn làm được bằng `DELETE .../members/{id}` — đó là một việc
    khác, người bấm biết rõ mình đang đi ra, và vẫn được mời lại.
    """
    _require_active(workspace_id, workspace.id)
    if member_id == user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, SELF_ROLE_DETAIL)
    target = await _member_or_404(db, workspace_id, member_id)
    if not await workspace_repo.set_role(
        db, workspace_id=workspace_id, user_id=member_id, role=payload.role
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, LAST_ADMIN_DETAIL)
    await db.commit()
    return MemberOut(
        user_id=target.id,
        email=target.email,
        display_name=target.display_name,
        role=payload.role,
        is_me=target.id == user.id,
    )


@router.delete(
    "/api/workspaces/{workspace_id}/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_member(
    workspace_id: uuid.UUID,
    member_id: uuid.UUID,
    db: Session,
    user: CurrentUser,
    workspace: AdminWorkspace,
) -> None:
    """Gỡ một người khỏi không gian. **Dữ liệu họ đã nhập ở lại** — chỉ người phụ trách trống ra.

    Quản trị tự gỡ chính mình cũng được: đó là đường *rời khỏi tổ chức*, và luật "còn ít nhất
    một quản trị" vẫn chặn người cuối cùng.
    """
    _require_active(workspace_id, workspace.id)
    await _member_or_404(db, workspace_id, member_id)
    if not await workspace_repo.remove_member(db, workspace_id=workspace_id, user_id=member_id):
        raise HTTPException(status.HTTP_409_CONFLICT, LAST_ADMIN_DETAIL)
    await db.commit()
    logger.info("Quản trị %s gỡ %s khỏi không gian %s", user.id, member_id, workspace_id)


# --------------------------------------------------------------------------- dùng chung


def _require_active(asked: uuid.UUID, active: uuid.UUID) -> None:
    """Mọi endpoint dưới `/{workspace_id}` chỉ làm việc trên **không gian đang mở**.

    `CurrentWorkspace` đã phân giải không gian từ tài khoản rồi, nên `workspace_id` trên URL chỉ
    là thứ để đối chiếu. Không nhận id khác: nhận thì mỗi endpoint lại phải tự kiểm tư cách
    thành viên một lần nữa, và chỉ cần một chỗ quên là rò. Muốn thao tác trên không gian khác
    thì `POST /activate` trước — một bước rõ ràng, một chỗ kiểm.
    """
    if asked != active:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Chỉ thao tác được trên workspace đang mở. Chuyển sang nó trước.",
        )


async def _member_or_404(db: AsyncSession, workspace_id: uuid.UUID, member_id: uuid.UUID) -> User:
    member = await membership(db, user_id=member_id, workspace_id=workspace_id)
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_A_MEMBER_DETAIL)
    target = await user_repo.get_by_id(db, member_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_A_MEMBER_DETAIL)
    return target
