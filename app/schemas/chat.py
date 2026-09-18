"""Schema cho API trợ lý AI (F3) — hợp đồng `POST /api/chat` chốt ở họp đầu D2.

Chủ sở hữu: Q | Task: 8.2, 8.3 | xem Task.md

Bản chốt ban đầu nằm ở `docs/api.md` mục 5. Ba trường được **thêm** vào `Citation` so với bản
đó (`title`, `url`, `source_urls`) vì thiếu chúng thì UI không vẽ nổi thẻ trích dẫn: `source_id`
là UUID trần, không nói được nó là ai và bấm vào đi đâu. Thêm trường là thay đổi tương thích
ngược, và theo quy ước số 9 của Task.md thì Swagger `/docs` mới là hợp đồng thật sau D1.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.kb import ReindexScope

#: Trần độ dài câu hỏi. Không phải giới hạn kỹ thuật (Gemini nhận dài hơn nhiều) mà là chặn dán
#: nhầm cả một tài liệu vào ô chat: 2000 ký tự đã dài hơn mọi câu hỏi thật, còn dán cả file vào
#: thì phần ngữ cảnh thật bị đẩy xuống cuối prompt và model trả lời theo tài liệu dán vào.
MAX_QUESTION_CHARS = 2000


class ChatFilters(BaseModel):
    """Thu hẹp phạm vi tìm kiếm trước khi trả lời (task 8.5)."""

    source_type: ReindexScope | None = Field(
        default=None,
        description="Chỉ tìm trong danh thiếp (`card`) hoặc chỉ hồ sơ DN (`company_profile`).",
    )
    company_id: uuid.UUID | None = Field(
        default=None,
        description="Chỉ tìm dữ liệu thuộc một công ty (cả danh thiếp lẫn hồ sơ của công ty đó).",
    )


class ChatIn(BaseModel):
    """Body của `POST /api/chat`."""

    question: str = Field(
        description="Câu hỏi bằng ngôn ngữ tự nhiên", max_length=MAX_QUESTION_CHARS
    )
    session_id: uuid.UUID | None = Field(
        default=None,
        description="Phiên đang có để hỏi tiếp. Bỏ trống = mở phiên mới.",
    )
    filters: ChatFilters = Field(default_factory=ChatFilters)

    @field_validator("question")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        """Chặn câu hỏi toàn khoảng trắng ở tầng schema.

        `min_length=1` không bắt được `"   "`, mà một câu hỏi rỗng đi tới tận tầng truy hồi thì
        nó nhúng một chuỗi trắng và trả về top-5 chunk gần như ngẫu nhiên — trợ lý trả lời một
        câu hỏi không ai hỏi.
        """
        text = value.strip()
        if not text:
            raise ValueError("Câu hỏi không được để trống")
        return text


class Citation(BaseModel):
    """Một nguồn mà câu trả lời trích dẫn. Số thứ tự khớp với dấu `[n]` trong `answer`."""

    source_type: str = Field(description="`card` hoặc `company_profile`")
    source_id: uuid.UUID = Field(
        description="Id danh thiếp, hoặc id **công ty** với nguồn là hồ sơ DN"
    )
    title: str = Field(description="Dòng tiêu đề của nguồn, ví dụ `Danh thiếp — Nguyễn Văn An`")
    snippet: str = Field(description="Đoạn trích của chunk được dùng")
    url: str = Field(description="Đường dẫn trang chi tiết: `/cards/{id}` hoặc `/companies/{id}`")
    score: float | None = Field(
        default=None,
        description=(
            "Tương đồng cosine của chunk. `null` khi chunk chỉ khớp ở nhánh full-text — "
            "lúc đó không có điểm cosine nào để báo."
        ),
    )
    source_urls: list[str] = Field(
        default_factory=list,
        description="URL nguồn ngoài của hồ sơ DN (khối 'Nguồn tham khảo'); rỗng với danh thiếp.",
    )


class ChatOut(BaseModel):
    """Kết quả `POST /api/chat`."""

    session_id: uuid.UUID
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    context_chunks: int = Field(
        default=0,
        description="Số chunk đưa vào ngữ cảnh. 0 = không tìm được gì nên không gọi model.",
    )
    model: str = Field(default="", description="Model đã sinh câu trả lời; rỗng khi không gọi.")
    elapsed_ms: int = 0


class ChatMessageOut(BaseModel):
    """Một lượt trong lịch sử hội thoại."""

    role: str
    content: str
    citations: list[Citation] | None = None
    created_at: datetime


class ChatSessionOut(BaseModel):
    """Kết quả `GET /api/chat/{session_id}`."""

    session_id: uuid.UUID
    title: str | None = None
    created_at: datetime
    messages: list[ChatMessageOut] = Field(default_factory=list)
