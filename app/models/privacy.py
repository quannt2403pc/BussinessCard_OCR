"""Bảng privacy_logs — ai làm gì với dữ liệu cá nhân, lúc nào.

Chủ sở hữu: T | Task: NEXT-07 | revision `0012`

Dữ liệu trong hệ thống này **là dữ liệu cá nhân của người khác**: tên, số điện thoại, email,
địa chỉ của người đưa danh thiếp. Nghị định 13/2023 gọi họ là *chủ thể dữ liệu*, và câu đầu
tiên pháp chế doanh nghiệp sẽ hỏi là "ai đã lấy dữ liệu đó ra khỏi hệ thống, lúc nào".

**Không có khoá ngoại tới `business_cards`**, và đó là điểm mấu chốt: dòng nhật ký phải sống
lâu hơn dữ liệu nó nói về. Ghi "đã xoá 12 liên hệ theo yêu cầu" mà dòng ấy biến mất cùng lúc
với 12 liên hệ đó thì nhật ký vô nghĩa. Nên `detail` lưu dữ liệu chết, không tham chiếu.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class PrivacyAction(StrEnum):
    """Ba việc chạm tới dữ liệu cá nhân theo cách đáng ghi lại."""

    EXPORT = "export"  # đưa dữ liệu ra khỏi hệ thống dưới dạng file
    ERASE = "erase"  # xoá theo yêu cầu của chủ thể dữ liệu
    RETENTION = "retention"  # xoá vì quá hạn lưu trữ đã đặt


class PrivacyLog(Base):
    __tablename__ = "privacy_logs"
    __table_args__ = (
        # Màn hình chỉ hỏi "gần đây tôi đã làm gì với dữ liệu" — tên và thứ tự cột khớp từng
        # chữ với revision `0012`, xem cảnh báo I-16 ở đầu `models/card.py`.
        Index(
            "ix_privacy_logs_workspace_id_created_at",
            "workspace_id",
            text("created_at DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    #: **Khoá tách dữ liệu** từ `NEXT-05` (revision `0015`). Mọi câu `WHERE` lọc dữ liệu đi
    #: qua cột này, không còn qua `user_id`. `CASCADE`: xoá một không gian là xoá sạch dữ liệu
    #: của nó — không để lại bản ghi mồ côi mà không ai truy cập được nữa.
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: **Người tạo**, không còn là khoá tách dữ liệu (`NEXT-05`). Giữ lại vì nó vẫn trả
    #: lời được "ai nhập bản ghi này" và là giá trị mặc định hợp lý cho người phụ trách.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Dữ liệu chết mô tả việc đã làm: định dạng file và bộ lọc với `export`, chuỗi tìm kiếm đã
    #: chuẩn hoá với `erase`, số ngày với `retention`. **Không** chứa id của bản ghi nào —
    #: xem lý do ở docstring đầu file.
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        server_default=text("clock_timestamp()"), nullable=False
    )

    def __repr__(self) -> str:
        return f"<PrivacyLog {self.id} {self.action} n={self.record_count}>"
