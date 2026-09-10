"""Model ORM — TÁCH THEO FILE, không gộp vào models.py chung.

Chủ sở hữu: Q | Task: 1.6 | xem Task.md

Import ở đây để `Base.metadata` có đủ bảng khi Alembic autogenerate (`alembic/env.py`) và khi
test dựng schema (task 6.1). Thêm model mới → thêm import vào đây.

`company` (bảng `companies` + `company_profiles`) hiện chỉ tồn tại trong migration khởi tạo,
model ORM còn là stub — `app/models/company.py` thuộc quyền sở hữu của **T** (xem bảng sở hữu
đầu Task.md). `alembic/env.py` đang bỏ qua hai bảng đó khi so sánh để autogenerate không sinh
lệnh `drop_table`; T khai model xong thì Q gỡ phần bỏ qua ấy.
"""

from app.models import company  # noqa: F401  — stub của T, import sẵn để khỏi phải sửa file này
from app.models.card import BusinessCard, CardStatus
from app.models.chat import ChatMessage, ChatRole, ChatSession
from app.models.integration import IntegrationStatus
from app.models.kb import KBChunk, KBSourceType

__all__ = [
    "BusinessCard",
    "CardStatus",
    "ChatMessage",
    "ChatRole",
    "ChatSession",
    "IntegrationStatus",
    "KBChunk",
    "KBSourceType",
    "company",
]
