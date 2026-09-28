"""Repository `chat_sessions` / `chat_messages` — lưu hội thoại với trợ lý AI (F3).

Chủ sở hữu: Q | Task: 8.3, 12.5

Lớp mỏng, cùng lối với `repositories/card.py`. Ba điểm đáng nêu:

1. **Không hàm nào commit.** Một lượt hỏi ghi *hai* dòng và chúng chỉ có nghĩa khi đi cùng nhau:
   commit riêng câu hỏi rồi model hỏng giữa chừng thì lượt sau nhét một câu hỏi chưa từng được
   trả lời vào prompt như bối cảnh thật.
2. **Lịch sử đọc theo thứ tự tăng dần, nhưng chỉ lấy N lượt gần nhất** — sắp tăng dần rồi `LIMIT`
   sẽ cho N lượt **đầu tiên** của hội thoại, tức càng chat lâu prompt càng chỉ nhớ phần mở đầu.
3. **`created_at` một mình KHÔNG sắp được hai lượt của cùng một lượt hỏi** — xem `_CHRONOLOGICAL`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatMessage, ChatRole, ChatSession

#: Trần độ dài tiêu đề phiên, sinh từ câu hỏi đầu tiên; cắt ở 120 để vừa một dòng.
TITLE_CHARS = 120

#: Khoá phụ tách hai lượt có **cùng** `created_at`: câu hỏi trước, câu trả lời sau.
#:
#: ⚠️ `now()` của Postgres là **thời điểm bắt đầu transaction**, nên hai dòng ghi trong cùng một
#: transaction có dấu thời gian bằng nhau tuyệt đối; `id` cũng không cứu được vì UUIDv4 là ngẫu
#: nhiên. Hậu quả nếu thiếu khoá này: câu trả lời đứng **trước** câu hỏi của chính nó.
#:
#: Sắp theo vai trò đúng với cách ứng dụng ghi — mỗi transaction ghi đúng **một cặp** hỏi–đáp.
#: Ghi hai lượt cùng vai trò trong một transaction thì phải thêm cột thứ tự.
_ROLE_ORDER = case((ChatMessage.role == ChatRole.USER.value, 0), else_=1)

#: Thứ tự thời gian đầy đủ, dùng chung cho cả hai hàm đọc.
_CHRONOLOGICAL = (ChatMessage.created_at, _ROLE_ORDER, ChatMessage.id)


async def create_session(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    title: str | None = None,
) -> ChatSession:
    """Mở một phiên hỏi–đáp mới. Chưa commit — xem ghi chú 1 ở đầu file."""
    # `user_id` là **người mở phiên**, không phải khoá lọc: trong một không gian nhiều người,
    # lịch sử hỏi đáp phải biết ai đã hỏi.
    session = ChatSession(workspace_id=workspace_id, user_id=user_id, title=_title(title))
    db.add(session)
    await db.flush()
    return session


async def get_session(
    db: AsyncSession, session_id: uuid.UUID, *, workspace_id: uuid.UUID
) -> ChatSession | None:
    """Một phiên **của đúng không gian này**, hoặc `None`.

    Không `db.get()`: tra khoá chính không nhận thêm điều kiện, mà nhớ so `session.workspace_id`
    ở từng chỗ gọi là việc sẽ có người quên. Phiên của người khác trả `None` → router trả **404**.
    """
    result = await db.execute(
        select(ChatSession).where(
            ChatSession.id == session_id, ChatSession.workspace_id == workspace_id
        )
    )
    return result.scalar_one_or_none()


async def add_message(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    role: ChatRole | str,
    content: str,
    citations: list[dict[str, Any]] | None = None,
) -> ChatMessage:
    """Ghi một lượt vào hội thoại. Chưa commit.

    `citations` chỉ có ở lượt của trợ lý; lượt người dùng để `None` chứ không `[]` — `NULL` nói
    "không áp dụng", mảng rỗng nói "đã trả lời mà không có nguồn nào".
    """
    message = ChatMessage(
        session_id=session_id,
        role=str(role),
        content=content,
        citations=citations,
    )
    db.add(message)
    await db.flush()
    return message


async def list_messages(
    db: AsyncSession, session_id: uuid.UUID, *, workspace_id: uuid.UUID
) -> Sequence[ChatMessage]:
    """Toàn bộ lượt của một phiên, cũ → mới (dùng cho `GET /api/chat/{session_id}`)."""
    rows = await db.execute(_owned_messages(session_id, workspace_id).order_by(*_CHRONOLOGICAL))
    return list(rows.scalars().all())


async def recent_messages(
    db: AsyncSession,
    session_id: uuid.UUID,
    *,
    workspace_id: uuid.UUID,
    limit: int,
) -> Sequence[ChatMessage]:
    """`limit` lượt **gần nhất**, trả về theo thứ tự cũ → mới để nhét vào prompt.

    Lấy ngược rồi đảo lại — xem ghi chú 2 ở đầu file. Thứ tự ngược phải đảo **cả khoá phụ vai
    trò**, nếu không lượt cuối bị cắt nhầm đầu này rồi ghép lại sai đầu kia.
    """
    rows = await db.execute(
        _owned_messages(session_id, workspace_id)
        .order_by(*(column.desc() for column in _CHRONOLOGICAL))
        .limit(limit)
    )
    return list(reversed(list(rows.scalars().all())))


def _owned_messages(session_id: uuid.UUID, workspace_id: uuid.UUID) -> Select[tuple[ChatMessage]]:
    """Các lượt của một phiên, **kèm điều kiện phiên đó thuộc về `workspace_id`**.

    `chat_messages` không có cột `workspace_id` nên chủ sở hữu phải lấy qua `JOIN` tới phiên. Một
    đường gọi mới quên bước kiểm quyền sẽ nhận về danh sách rỗng, chứ không đọc được hội thoại
    của người khác.
    """
    return (
        select(ChatMessage)
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .where(ChatMessage.session_id == session_id, ChatSession.workspace_id == workspace_id)
    )


def _title(raw: str | None) -> str | None:
    """Tiêu đề phiên: câu hỏi đầu tiên đã rút gọn, hoặc `None` khi không có gì để đặt tên."""
    text = " ".join((raw or "").split())
    if not text:
        return None
    return text if len(text) <= TITLE_CHARS else f"{text[: TITLE_CHARS - 1].rstrip()}…"
