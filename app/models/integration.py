"""Bảng integration_status — cache trạng thái OAuth CLIProxy.

Chủ sở hữu: Q | Task: 1.6 | xem Task.md

Chỉ là **cache** để UI hiện badge ngay mà không phải gọi CLIProxy mỗi lần tải trang.
Nguồn sự thật là `GET /v0/management/auth-files` — mảng rỗng nghĩa là chưa kết nối
(Plan.md mục 2.4, task 2.4). **Không** dùng `get-auth-status`: thiếu tham số `state` thì nó trả
`{"status":"ok"}` kể cả khi chưa đăng nhập bao giờ, badge sẽ xanh vĩnh viễn (I-02 trong Task.md).
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class IntegrationStatus(Base):
    """Một dòng cho mỗi **(người dùng, provider)** — từ D12 mỗi người có kết nối riêng.

    Khoá chính đổi từ `(provider)` sang `(user_id, provider)` ở revision `0005` (task 12.3):
    A bấm "Ngắt kết nối" không được hạ cờ của B (tiêu chí A10, D13).
    """

    __tablename__ = "integration_status"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    connected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    account_label: Mapped[str | None] = mapped_column(String(255))
    last_checked_at: Mapped[datetime | None] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<IntegrationStatus {self.provider} user={self.user_id} connected={self.connected}>"
