"""Schema cho không gian làm việc và thành viên.

Chủ sở hữu: T | Task: NEXT-05 | xem Task.md
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.workspace import WORKSPACE_NAME_MAX_LENGTH, Role
from app.schemas.user import normalize_email


class WorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    #: Vai trò **của người đang đăng nhập** trong không gian này, không phải thuộc tính của nó.
    role: Role
    member_count: int = 1
    is_active: bool = False
    created_at: datetime | None = None


class WorkspaceListOut(BaseModel):
    active_id: uuid.UUID | None = None
    items: list[WorkspaceOut] = Field(default_factory=list)


class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=WORKSPACE_NAME_MAX_LENGTH)

    @field_validator("name")
    @classmethod
    def collapse_spaces(cls, value: str) -> str:
        name = " ".join(value.split())
        if not name:
            raise ValueError("Tên workspace rỗng.")
        return name


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str | None = None
    role: Role
    #: Chính người đang đăng nhập — giao diện dùng để không vẽ nút *Gỡ* lên chính họ.
    is_me: bool = False


class MemberListOut(BaseModel):
    workspace_id: uuid.UUID
    name: str
    #: Vai trò của người đang đăng nhập; chỉ `admin` mới thấy được các nút mời / gỡ / đổi vai.
    my_role: Role
    items: list[MemberOut] = Field(default_factory=list)


class InviteIn(BaseModel):
    """Mời **một tài khoản đã có** vào không gian.

    Cố ý không gửi email mời và không tạo tài khoản hộ: trong phạm vi bản demo (`Plan.md` mục
    1.3) chưa có xác thực email, nên một lời mời gửi tới địa chỉ gõ nhầm sẽ mở dữ liệu của cả
    tổ chức cho người lạ. Người được mời tự đăng ký trước, rồi quản trị mời bằng đúng email đó.
    """

    email: str
    role: Role = Role.MEMBER

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        # Dùng lại `normalize_email()` của 12.2 thay vì `EmailStr`: cùng một quy tắc với lúc
        # đăng ký, nên email tra ra được chính tài khoản đã lưu. `EmailStr` chuẩn hơn nhưng nó
        # không hạ chữ hoa, và "An@Vidu.vn" sẽ không khớp hàng "an@vidu.vn" trong bảng.
        return normalize_email(value)


class RoleIn(BaseModel):
    role: Role
