"""Bảng integration_status — cache trạng thái OAuth CLIProxy.

Chủ sở hữu: Q | Task: 1.6 | xem Task.md

Chỉ là **cache** để UI hiện badge ngay mà không phải gọi CLIProxy mỗi lần tải trang.
Nguồn sự thật vẫn là `GET /v0/management/get-auth-status` (Plan.md mục 2.4, task 2.4).
"""

from datetime import datetime

from sqlalchemy import Boolean, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class IntegrationStatus(Base):
    """Một dòng cho mỗi provider (hiện chỉ dùng provider OAuth của CLIProxy)."""

    __tablename__ = "integration_status"

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
        return f"<IntegrationStatus {self.provider} connected={self.connected}>"
