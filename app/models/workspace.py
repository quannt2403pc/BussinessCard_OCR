"""Bảng workspaces + workspace_members — dữ liệu thuộc tổ chức, không thuộc cá nhân.

Chủ sở hữu: T | Task: NEXT-05 | revision `0015`

Từ D12 tới 2026-09-26, `user_id` gánh hai vai: vừa là *người tạo* vừa là *khoá tách dữ liệu*.
`NEXT-05` tách hai vai ấy ra — `workspace_id` là khoá tách, `user_id` chỉ còn là người tạo.

Ba vai trò, đúng ba, và đây là ranh giới cố ý: thêm quyền theo từng màn hình hay từng trường là
biến sản phẩm thành một hệ phân quyền mà không ai trong nhóm đủ người để bảo trì.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

WORKSPACE_NAME_MAX_LENGTH = 120


class Role(StrEnum):
    """Vai trò của một người trong một không gian làm việc."""

    ADMIN = "admin"  # mời, gỡ, đổi vai trò — và mọi thứ `member` làm được
    MEMBER = "member"  # đọc và ghi dữ liệu
    VIEWER = "viewer"  # chỉ đọc


#: Vai trò được phép ghi. Tra cứu bằng tập hợp chứ không so chuỗi rải rác: thêm một vai trò mới
#: sau này chỉ phải sửa đúng một chỗ, và không có chỗ nào bị bỏ sót âm thầm.
WRITER_ROLES: frozenset[str] = frozenset({Role.ADMIN, Role.MEMBER})


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(WORKSPACE_NAME_MAX_LENGTH), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<Workspace {self.id} {self.name!r}>"


class WorkspaceMember(Base):
    """Một người trong một không gian, kèm vai trò.

    Khoá chính là cặp `(workspace_id, user_id)` — không cần cột `id` riêng, và cặp ấy tự nó
    chặn việc thêm cùng một người hai lần vào cùng một không gian.
    """

    __tablename__ = "workspace_members"
    __table_args__ = (Index("ix_workspace_members_user_id", "user_id"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default=Role.MEMBER, server_default=Role.MEMBER
    )
    joined_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<WorkspaceMember ws={self.workspace_id} user={self.user_id} {self.role}>"
