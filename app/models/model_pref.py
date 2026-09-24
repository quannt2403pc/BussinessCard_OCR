"""Bảng user_model_prefs — mỗi người dùng chọn model riêng cho từng chức năng.

Chủ sở hữu: Q | Task: EX-13 | Quyết định: `docs/adr-model-per-feature.md` mục 4 | xem Task.md

**Bảng riêng chứ không thêm cột vào `users`** vì hai lý do, lý do thứ hai mới là lý do thật:

1. `models/user.py` là **file của T** (bảng sở hữu đầu `Task.md`), mà đây là việc thuần của Q.
   Luật nới từ D12 cho phép chạm, nhưng chạm mà không cần thì vẫn là tạo việc review cho người kia.
2. Ba cột này là **cấu hình tuỳ chọn**, không phải thuộc tính của một con người. Phần lớn người dùng
   sẽ không bao giờ chọn gì và không có dòng nào ở đây — `users` thì mỗi người luôn có đúng một dòng.

**`NULL` nghĩa là "dùng `LLM_MODEL` của hệ thống"**, không phải "chưa chọn model nào". Cố ý *không*
chép giá trị mặc định vào DB lúc tạo dòng: chép rồi thì đổi `LLM_MODEL` trong `.env` chỉ ảnh hưởng
người dùng mới, còn người cũ mắc kẹt với model của ngày họ bấm nút — mà họ không hề chọn nó.
"""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class UserModelPref(Base):
    """Một dòng cho mỗi người dùng **đã từng chọn** ít nhất một model.

    Khoá chính chính là `user_id`: ba chức năng nằm trên cùng một dòng chứ không phải ba dòng
    `(user_id, feature)`. Số chức năng là hằng số của sản phẩm (ba, chốt ở ADR mục 4 — M2), không
    phải dữ liệu người dùng sinh ra; dựng bảng khoá kép cho ba giá trị cố định chỉ đổi lấy việc
    mỗi lần đọc phải gom ba dòng.
    """

    __tablename__ = "user_model_prefs"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    #: Quét danh thiếp — **dùng chung cho cả bước Việt hoá sau khi quét** (QĐ-5, ADR mục 4 M3).
    ocr_model: Mapped[str | None] = mapped_column(String(128))
    #: Lập hồ sơ doanh nghiệp.
    enrich_model: Mapped[str | None] = mapped_column(String(128))
    #: Trợ lý AI.
    chat_model: Mapped[str | None] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<UserModelPref user={self.user_id} ocr={self.ocr_model!r} "
            f"enrich={self.enrich_model!r} chat={self.chat_model!r}>"
        )
