"""Model ORM — TÁCH THEO FILE, không gộp vào models.py chung.

Chủ sở hữu: Q | Task: 1.6 | xem Task.md

Import ở đây để `Base.metadata` có đủ bảng khi Alembic autogenerate (`alembic/env.py`) và khi
test dựng schema (task 6.1). Thêm model mới → thêm import vào đây.

`company` (bảng `companies` + `company_profiles`) do **T** khai ở task 3.8 (bảng sở hữu đầu
Task.md). Import module là đủ để hai bảng vào `Base.metadata`. Bỏ dòng import ấy đi thì
`ForeignKey("companies.id")` trong `card.py` không phân giải được (`NoReferencedTableError`)
— SQLAlchemy phân giải theo tên bảng lúc dùng nên thứ tự import không quan trọng, nhưng
thiếu thì hỏng.
"""

from app.models import company  # noqa: F401  — model của T, nạp `companies` vào metadata
from app.models.card import BusinessCard, CardStatus
from app.models.chat import ChatMessage, ChatRole, ChatSession
from app.models.integration import IntegrationStatus
from app.models.kb import KBChunk, KBSourceType
from app.models.user import User

__all__ = [
    "BusinessCard",
    "CardStatus",
    "ChatMessage",
    "ChatRole",
    "ChatSession",
    "IntegrationStatus",
    "KBChunk",
    "KBSourceType",
    "User",
    "company",
]
