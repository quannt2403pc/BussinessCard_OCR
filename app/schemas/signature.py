"""Schema cho luồng nhập liên hệ từ chữ ký email.

Chủ sở hữu: T | Task: NEXT-08 | xem Task.md
"""

from pydantic import BaseModel, Field, field_validator

from app.prompts.signature import MAX_SIGNATURE_CHARS
from app.schemas.card import CardOut
from app.services.signature import MIN_SIGNATURE_CHARS


class SignatureIn(BaseModel):
    text: str = Field(min_length=MIN_SIGNATURE_CHARS, max_length=MAX_SIGNATURE_CHARS)

    @field_validator("text")
    @classmethod
    def strip_text(cls, value: str) -> str:
        text = value.strip()
        if len(text) < MIN_SIGNATURE_CHARS:
            raise ValueError("Khối chữ ký quá ngắn.")
        return text


class SignatureOut(BaseModel):
    """Cùng hình dạng với `CardUploadOut` của `3.1`, và cố ý như vậy.

    Giao diện xử lý một liên hệ dán từ chữ ký y hệt một tấm thẻ vừa quét: chuyển sang màn hình
    review. `duplicate=True` nghĩa là khối chữ ký này đã dán trước đó, trả lại đúng bản ghi cũ.
    """

    card: CardOut
    duplicate: bool = False
    elapsed_ms: int = 0
