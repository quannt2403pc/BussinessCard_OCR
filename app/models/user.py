import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    cliproxy_auth_file: Mapped[str | None] = mapped_column(Text)
    #: Hạn lưu trữ danh thiếp, tính bằng ngày (task NEXT-07, revision `0012`). `NULL` = giữ vô
    #: thời hạn, và đó là **mặc định**: tự đặt một hạn rồi tự xoá dữ liệu của người dùng là việc
    #: không ai cho phép. Quá hạn cũng không tự xoá — hệ thống chỉ đếm ra và chờ người bấm.
    retention_days: Mapped[int | None] = mapped_column(Integer)
    #: Không gian làm việc đang mở (task NEXT-05). `SET NULL` chứ không `CASCADE`: xoá một
    #: không gian không được xoá theo người dùng — họ còn ở những không gian khác.
    active_workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="SET NULL"),
    )
    last_login_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<User {self.id} {self.email!r}>"
