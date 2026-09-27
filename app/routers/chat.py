"""F3 — `POST /api/chat`, trả answer + citations.

Chủ sở hữu: Q | Task: 8.2, 8.3 (lịch sử), 8.4 (trang `/assistant`), 8.5 (bộ lọc), 12.5 (tách
theo người dùng), EX-09 (gỡ trang, chỉ còn bong bóng) | xem Task.md

Router là lớp HTTP mỏng: nghiệp vụ nằm ở `services/assistant.py`, câu SQL ở
`repositories/chat.py`. Cùng lối `cards.py` → `services/ocr.py`, và nhờ thế phần đáng test nhất
của D8 (rút trích dẫn, ghép câu hỏi nhiều lượt) test được mà không cần dựng request nào.

**Thứ tự ghi DB là có chủ đích:** hỏi model **trước**, ghi hội thoại **sau**. Ghi câu hỏi trước
rồi model hỏng thì lịch sử còn lại một câu hỏi không có câu trả lời — và lượt hỏi kế tiếp sẽ
nhét đúng câu cụt đó vào prompt như bối cảnh thật. Vì vậy phiên mới cũng chỉ được tạo sau khi đã
có câu trả lời: model chết thì không để lại phiên rỗng nào trong danh sách.

⚠️ Một lượt hỏi giữ connection DB suốt thời gian gọi model (~2-5 giây). Chấp nhận được với bản
demo một người dùng; nếu về sau nhiều người hỏi cùng lúc thì phải cắt transaction đọc trước khi
gọi LLM, như luồng enrich của T đã phải làm ở 5.4.
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import CurrentUser
from app.core.workspace import CurrentWorkspace
from app.models.chat import ChatRole
from app.repositories import chat as chat_repo
from app.schemas.chat import (
    ChatIn,
    ChatMessageOut,
    ChatOut,
    ChatSessionOut,
    Citation,
)
from app.services import assistant, llm
from app.services.assistant import Turn
from app.services.embeddings import EmbedderUnavailableError, EmbeddingError

logger = logging.getLogger(__name__)

router = APIRouter()

# --------------------------------------------------------------------------- trang HTML


@router.get("/assistant", tags=["ui"])
async def assistant_page(session: Annotated[uuid.UUID | None, Query()] = None) -> RedirectResponse:
    """`/assistant` đã gỡ — trợ lý chỉ còn ở bong bóng chat (EX-09). Chuyển **301** về trang chủ.

    Giữ lại đúng route này thay vì xoá thẳng, theo **QĐ-3**: `?session=<uuid>` là đường **chia sẻ
    hội thoại** dựng ở 14.5, và `/assistant` còn nằm trong `docs/demo-runbook.md`,
    `docs/test-scenarios.md`, `docs/api.md` cùng hai dòng gợi ý của `scripts/seed.py`. Xoá là để
    người trình bày bấm vào một link `404` ngay giữa buổi demo.

    `?session=` đổi tên thành `?chat=` vì bên nhận nay là bong bóng chứ không phải trang: tham số
    của `base.html` đọc, không phải của route này. `_assistant_widget.html` mở panel đúng hội thoại
    rồi **dọn tham số** — để nguyên thì mỗi lần tải lại trang chủ là một lần panel tự bật lên.

    301 chứ không 302: chuyển nhà vĩnh viễn, để trình duyệt và trang đánh dấu của người dùng cập
    nhật luôn — cùng lối `/dashboard` → `/` ở 14.6.
    """
    target = f"/?chat={session}" if session else "/"
    return RedirectResponse(target, status_code=status.HTTP_301_MOVED_PERMANENTLY)


# --------------------------------------------------------------------------- API


@router.post("/api/chat", response_model=ChatOut, tags=["chat"])
async def chat(
    payload: ChatIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: CurrentUser,
    workspace: CurrentWorkspace,
) -> ChatOut:
    """Hỏi trợ lý: truy hồi KB → dựng ngữ cảnh → gọi Gemini Flash → trả lời kèm trích dẫn.

    Không tìm được chunk nào thì **không gọi model** và trả thẳng câu "không có thông tin"
    (`context_chunks = 0`). Tìm được nhưng ngữ cảnh không chứa đáp án thì chính model phải nói
    không biết — ngưỡng điểm không làm được việc đó, xem `prompts/assistant.py`.
    """
    history: list[Turn] = []
    session = None

    if payload.session_id is not None:
        session = await chat_repo.get_session(db, payload.session_id, workspace_id=workspace.id)
        if session is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail=f"Không có phiên hội thoại {payload.session_id}.",
            )
        rows = await chat_repo.recent_messages(
            db, session.id, workspace_id=workspace.id, limit=assistant.HISTORY_TURNS
        )
        history = [Turn(role=row.role, content=row.content) for row in rows]

    try:
        result = await assistant.answer(
            db,
            payload.question,
            workspace_id=workspace.id,
            user_id=user.id,
            history=history,
            source_type=payload.filters.source_type,
            company_id=payload.filters.company_id,
        )
    except EmbedderUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except EmbeddingError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except llm.LLMNotConnectedError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except llm.LLMError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    # Có câu trả lời rồi mới đụng tới DB — xem ghi chú thứ tự ghi ở đầu file.
    if session is None:
        session = await chat_repo.create_session(
            db, workspace_id=workspace.id, user_id=user.id, title=payload.question
        )

    # `dataclasses.asdict` chứ không `vars()`: `assistant.Citation` khai `slots=True` nên nó
    # không có `__dict__`, và `vars()` ném `TypeError` ngay lượt hỏi đầu tiên.
    citations = [Citation(**dataclasses.asdict(citation)) for citation in result.citations]
    await chat_repo.add_message(
        db, session_id=session.id, role=ChatRole.USER, content=payload.question
    )
    await chat_repo.add_message(
        db,
        session_id=session.id,
        role=ChatRole.ASSISTANT,
        content=result.text,
        citations=[citation.model_dump(mode="json") for citation in citations],
    )
    await db.commit()

    logger.info(
        "Chat %s: %d chunk ngữ cảnh → %d trích dẫn trong %dms",
        session.id,
        result.context_chunks,
        len(citations),
        result.elapsed_ms,
    )
    return ChatOut(
        session_id=session.id,
        answer=result.text,
        citations=citations,
        context_chunks=result.context_chunks,
        model=result.model,
        elapsed_ms=result.elapsed_ms,
    )


@router.get("/api/chat/{session_id}", response_model=ChatSessionOut, tags=["chat"])
async def get_chat_session(
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: CurrentUser,
    workspace: CurrentWorkspace,
) -> ChatSessionOut:
    """Đọc lại toàn bộ một phiên hội thoại (task 8.3) — dùng khi mở lại trang bằng `?session=`."""
    session = await chat_repo.get_session(db, session_id, workspace_id=workspace.id)
    if session is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Không có phiên hội thoại {session_id}."
        )

    messages = await chat_repo.list_messages(db, session_id, workspace_id=workspace.id)
    return ChatSessionOut(
        session_id=session.id,
        title=session.title,
        created_at=session.created_at,
        messages=[
            ChatMessageOut(
                role=message.role,
                content=message.content,
                citations=(
                    [Citation.model_validate(item) for item in message.citations]
                    if message.citations is not None
                    else None
                ),
                created_at=message.created_at,
            )
            for message in messages
        ],
    )
