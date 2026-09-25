"""Nhật ký, hạn lưu trữ và xoá theo yêu cầu của chủ thể dữ liệu.

Chủ sở hữu: T | Task: NEXT-07 | xem Task.md

**Xoá ở đây là xoá thật, khác hẳn `merged_into_id` của `NEXT-04`.** Ở đó máy chỉ *đoán* hai thẻ
là một người nên gộp mềm mới đúng; ở đây chủ thể dữ liệu **yêu cầu** xoá, và quyền đó không
được phục vụ bằng một cờ ẩn. Xoá cả hàng, ảnh, ghi chú và chunk Knowledge Base.

**Mọi hàm bắt buộc `user_id`** — cùng lối đã chốt ở 12.6.
"""

import logging
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.card import BusinessCard
from app.models.kb import KBChunk, KBSourceType
from app.models.privacy import PrivacyAction, PrivacyLog
from app.models.user import User

logger = logging.getLogger(__name__)

#: Trần số dòng nhật ký trả về. Đây là màn hình "gần đây tôi đã làm gì", không phải kho lưu trữ.
LOG_LIMIT = 100

#: Hạn lưu trữ ngắn nhất và dài nhất đặt được. Sàn 30 ngày để một cú gõ nhầm số `1` không xoá
#: sạch dữ liệu vừa quét hôm qua; trần 10 năm vì dài hơn thế thì "có hạn" chỉ là hình thức.
MIN_RETENTION_DAYS = 30
MAX_RETENTION_DAYS = 3650


async def log(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    action: PrivacyAction,
    detail: dict[str, Any],
    record_count: int,
) -> PrivacyLog:
    entry = PrivacyLog(
        user_id=user_id,
        action=action.value,
        detail=detail,
        record_count=record_count,
    )
    db.add(entry)
    await db.flush()
    return entry


async def recent_logs(
    db: AsyncSession, *, user_id: uuid.UUID, limit: int = LOG_LIMIT
) -> Sequence[PrivacyLog]:
    rows = await db.scalars(
        select(PrivacyLog)
        .where(PrivacyLog.user_id == user_id)
        .order_by(PrivacyLog.created_at.desc(), PrivacyLog.id.desc())
        .limit(limit)
    )
    return list(rows.all())


# --------------------------------------------------------------- xoá theo yêu cầu


def subject_filter(term: str) -> Any:
    """Khớp một chủ thể dữ liệu theo email hoặc số điện thoại **đã chuẩn hoá**.

    So thẳng giá trị trong cột, không so mờ: đây là câu quyết định **xoá vĩnh viễn** dữ liệu
    của ai đó. Khớp mờ ở chỗ này nghĩa là một cú bấm có thể xoá nhầm người thứ hai, và không
    có đường nào lấy lại. Ai muốn tìm theo tên thì dùng ô tìm kiếm của `/cards` rồi xoá từng
    thẻ — chậm hơn, nhưng nhìn thấy mình đang xoá gì.
    """
    return or_(
        BusinessCard.email == term,
        BusinessCard.phone == term,
        BusinessCard.phone_alt == term,
    )


async def find_subject_cards(
    db: AsyncSession, term: str, *, user_id: uuid.UUID
) -> Sequence[BusinessCard]:
    rows = await db.scalars(
        select(BusinessCard)
        .where(BusinessCard.user_id == user_id, subject_filter(term))
        .order_by(BusinessCard.uploaded_at)
    )
    return list(rows.all())


async def erase_cards(
    db: AsyncSession, cards: Sequence[BusinessCard], *, user_id: uuid.UUID
) -> list[str]:
    """Xoá hẳn một loạt danh thiếp. Trả về đường dẫn ảnh để router xoá file.

    Gỡ chunk Knowledge Base **trong cùng transaction**: bỏ bước này thì trợ lý AI vẫn trích dẫn
    một người đã yêu cầu xoá dữ liệu — đúng thứ mà cả tính năng này sinh ra để tránh. Cùng lỗi
    mà `4.2` của Q đã chặn khi xoá một thẻ.

    `contact_notes` đi theo khoá ngoại `CASCADE`, không phải xoá tay. Thẻ nào đang bị gộp vào
    (`NEXT-04`) cũng đi theo, vì `merged_into_id` là `SET NULL` chứ không `CASCADE` — nên bản
    trùng của một người đã yêu cầu xoá phải nằm trong danh sách ngay từ đầu, và nó nằm thật:
    `find_subject_cards()` không lọc `merged_into_id`.
    """
    if not cards:
        return []

    card_ids = [card.id for card in cards]
    await db.execute(
        delete(KBChunk).where(
            KBChunk.user_id == user_id,
            KBChunk.source_type == str(KBSourceType.CARD),
            KBChunk.source_id.in_(card_ids),
        )
    )
    await db.execute(
        delete(BusinessCard).where(BusinessCard.user_id == user_id, BusinessCard.id.in_(card_ids))
    )
    return [card.image_path for card in cards]


# --------------------------------------------------------------- hạn lưu trữ


async def set_retention(db: AsyncSession, user: User, days: int | None) -> None:
    user.retention_days = days
    await db.flush()


async def expired_cards(
    db: AsyncSession, *, user_id: uuid.UUID, days: int, now: datetime
) -> Sequence[BusinessCard]:
    """Danh thiếp quét trước mốc `now - days`.

    Tính theo `uploaded_at` chứ không `updated_at`: hạn lưu trữ đếm từ lúc **thu thập** dữ liệu
    của người ta, và một lần sửa chính tả không làm cái đồng hồ ấy chạy lại từ đầu.
    """
    rows = await db.scalars(
        select(BusinessCard)
        .where(
            BusinessCard.user_id == user_id,
            BusinessCard.uploaded_at < now.replace(tzinfo=None) - timedelta(days=days),
        )
        .order_by(BusinessCard.uploaded_at)
    )
    return list(rows.all())


async def count_expired(db: AsyncSession, *, user_id: uuid.UUID, days: int, now: datetime) -> int:
    total = await db.scalar(
        select(func.count())
        .select_from(BusinessCard)
        .where(
            BusinessCard.user_id == user_id,
            BusinessCard.uploaded_at < now.replace(tzinfo=None) - timedelta(days=days),
        )
    )
    return int(total or 0)


async def count_contacts(db: AsyncSession, *, user_id: uuid.UUID) -> int:
    total = await db.scalar(
        select(func.count()).select_from(BusinessCard).where(BusinessCard.user_id == user_id)
    )
    return int(total or 0)


async def oldest_upload(db: AsyncSession, *, user_id: uuid.UUID) -> datetime | None:
    return await db.scalar(
        select(func.min(BusinessCard.uploaded_at)).where(BusinessCard.user_id == user_id)
    )
