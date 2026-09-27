"""Repository `chat_sessions` / `chat_messages` — lưu hội thoại với trợ lý AI (F3).

Chủ sở hữu: Q | Task: 8.3, 12.5 | xem Task.md

Bảng đã có sẵn từ revision khởi tạo `0001` (task 1.6) nên task này **không cần Alembic revision
mới** — đúng như quy ước số 5 dự tính khi khai đủ bảng ngay từ D1.

Lớp mỏng, cùng lối với `repositories/card.py`: router lo HTTP, file này lo câu SQL. Hai điểm
đáng nêu:

1. **Không hàm nào commit.** Một lượt hỏi ghi *hai* dòng (câu hỏi và câu trả lời) và chúng chỉ
   có nghĩa khi đi cùng nhau: commit riêng câu hỏi rồi model hỏng giữa chừng thì lịch sử còn lại
   một câu hỏi không bao giờ được trả lời, và lượt sau sẽ nhét nó vào prompt như bối cảnh thật.
   `routers/chat.py` commit đúng một lần ở cuối.

2. **Lịch sử đọc theo thứ tự thời gian tăng dần, nhưng chỉ lấy N lượt gần nhất.** Muốn "N lượt
   *cuối*" thì phải `ORDER BY created_at DESC LIMIT N` rồi đảo lại trong Python — sắp tăng dần
   rồi `LIMIT` sẽ cho N lượt **đầu tiên** của hội thoại, tức càng chat lâu prompt càng chỉ nhớ
   phần mở đầu. Lỗi này không báo gì cả, chỉ làm trợ lý ngày một lú.

3. **`created_at` một mình KHÔNG sắp được hai lượt của cùng một lượt hỏi** — xem `_CHRONOLOGICAL`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatMessage, ChatRole, ChatSession

#: Trần độ dài tiêu đề phiên, sinh từ câu hỏi đầu tiên. Cột `title` là `String(255)`; cắt ở 120
#: để tiêu đề còn vừa một dòng trên danh sách phiên.
TITLE_CHARS = 120

#: Khoá phụ tách hai lượt có **cùng** `created_at`: câu hỏi trước, câu trả lời sau.
#:
#: ⚠️ Lỗi này bắt được bằng test, và nó là loại hỏng im lặng đúng nghĩa. `chat_messages.created_at`
#: mặc định `now()`, mà `now()` của Postgres là **thời điểm bắt đầu transaction**, không phải thời
#: điểm chạy câu lệnh. Một lượt hỏi ghi hai dòng trong cùng một transaction nên hai dòng có dấu
#: thời gian **bằng nhau tuyệt đối**, không phải xấp xỉ. Lúc đó `ORDER BY created_at` để Postgres
#: tự quyết thứ tự, và `id` cũng không cứu được vì UUIDv4 là ngẫu nhiên — không mang thông tin
#: thời gian nào. Hậu quả: `GET /api/chat/{id}` trả câu trả lời đứng **trước** câu hỏi của chính
#: nó, và phần lịch sử nhét vào prompt cũng đảo ngược theo.
#:
#: Sắp theo vai trò là lời giải đúng với cách ứng dụng ghi: mỗi transaction ghi đúng **một cặp**
#: hỏi–đáp (xem `routers/chat.py`). Ghi hai lượt cùng vai trò trong một transaction thì khoá này
#: hết tác dụng — lúc đó phải thêm cột thứ tự, tức là một Alembic revision.
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
    # `user_id` là **người mở phiên**, không phải khoá lọc (task NEXT-05): trong một không gian
    # nhiều người, lịch sử hỏi đáp phải biết ai đã hỏi.
    session = ChatSession(workspace_id=workspace_id, user_id=user_id, title=_title(title))
    db.add(session)
    await db.flush()
    return session


async def get_session(
    db: AsyncSession, session_id: uuid.UUID, *, workspace_id: uuid.UUID
) -> ChatSession | None:
    """Một phiên **của đúng người này**, hoặc `None` (task 12.5).

    Không `db.get()` nữa: tra khoá chính không nhận thêm điều kiện, mà nhớ so `session.workspace_id`
    ở từng chỗ gọi là việc sẽ có người quên. Phiên của người khác trả `None` → router trả **404**,
    giống hệt khi id không tồn tại: không có cách nào dò xem một `session_id` có thật hay không.
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
    "không áp dụng", mảng rỗng nói "đã trả lời mà không có nguồn nào", và hai thứ đó khác nhau
    khi đọc lại lịch sử để tìm xem lượt nào bị trượt trích dẫn.
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
    """`limit` lượt **gần nhất**, trả về theo thứ tự cũ → mới để nhét vào prompt (task 8.3).

    Lấy ngược rồi đảo lại — xem ghi chú 2 ở đầu file về lý do không `LIMIT` trên thứ tự tăng dần.
    Thứ tự ngược phải đảo **cả khoá phụ vai trò** (`_ROLE_ORDER`), nếu không lượt cuối cùng của
    hội thoại bị cắt nhầm đầu này rồi ghép lại sai đầu kia — xem ghi chú 3.
    """
    rows = await db.execute(
        _owned_messages(session_id, workspace_id)
        .order_by(*(column.desc() for column in _CHRONOLOGICAL))
        .limit(limit)
    )
    return list(reversed(list(rows.scalars().all())))


def _owned_messages(session_id: uuid.UUID, workspace_id: uuid.UUID) -> Select[tuple[ChatMessage]]:
    """Các lượt của một phiên, **kèm điều kiện phiên đó thuộc về `workspace_id`** (task 12.5).

    `chat_messages` không có cột `workspace_id` (xem `models/chat.py`) nên chủ sở hữu phải lấy qua
    `JOIN` tới phiên. Router vốn đã kiểm quyền bằng `get_session()` trước khi gọi hai hàm đọc ở
    đây, nhưng điều kiện này vẫn nằm trong SQL: một đường gọi mới quên bước kiểm kia sẽ nhận về
    danh sách rỗng, chứ không đọc được hội thoại của người khác.
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
