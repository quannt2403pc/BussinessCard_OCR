"""Schema cho API Knowledge Base (F3).

Chủ sở hữu: Q | Task: 6.4 | xem Task.md
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class ReindexScope(StrEnum):
    """Phạm vi một lượt reindex — trùng tên với `kb_chunks.source_type`."""

    CARD = "card"
    COMPANY_PROFILE = "company_profile"


class ReindexOut(BaseModel):
    """Kết quả `POST /api/kb/reindex`."""

    cards: int = Field(description="Số danh thiếp đã xử lý (chỉ tính bản đã xác nhận)")
    profiles: int = Field(description="Số hồ sơ DN đã xử lý (generated/verified)")
    chunks: int = Field(description="Tổng số chunk đã ghi vào kb_chunks")
    skipped: int = Field(description="Số nguồn bỏ qua vì không còn trường nào có nội dung")
    total_chunks: int = Field(description="Tổng số chunk đang có trong KB sau lượt chạy")
    elapsed_ms: int
    model: str = Field(description="Model embedding mà service embedder đang chạy")
