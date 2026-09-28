"""Repository business_cards.

Chủ sở hữu: Q | Task: 3.5, 12.5

Lớp mỏng giữa router và ORM: router lo HTTP, file này lo câu truy vấn.

**Khoá lọc là `workspace_id`** — `user_id` ở lại chỉ với vai trò *người tạo*.

**Khoá lọc là tham số BẮT BUỘC của mọi hàm ở đây** — keyword-only, không có giá trị mặc định.
Một `workspace_id: … | None = None` sẽ khiến "quên truyền" trở thành "đọc dữ liệu của tất cả mọi
người", tức mặc định MỞ; kiểu như hiện tại thì quên truyền là `TypeError` ngay lần chạy đầu.

Lọc đặt ở **mệnh đề WHERE của cùng câu truy vấn**, không sàng lại trong Python: sàng sau vừa rò
vừa làm phân trang sai số.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.card import BusinessCard, CardStatus

logger = logging.getLogger(__name__)

#: Cột được quét khi tìm kiếm tự do (`?q=`). Không quét `address`/`notes`: gõ "Hà Nội" sẽ ra toàn
#: bộ danh thiếp, ô tìm kiếm thành vô dụng.
#:
#: Hai cột `*_vi` là điều kiện để ô tìm kiếm còn dùng được với thẻ nước ngoài: gõ "Tanaka" mà chỉ
#: quét `full_name` thì không bao giờ khớp `田中 太郎`.
SEARCH_COLUMNS = (
    BusinessCard.full_name,
    BusinessCard.full_name_vi,
    BusinessCard.company_name_raw,
    BusinessCard.company_name_vi,
    BusinessCard.email,
)

#: Trần số bản ghi một trang. Người dùng sửa `?size=` trên thanh địa chỉ được, nên phải chặn ở
#: đây chứ không chỉ ở khai báo `Query()` của router.
MAX_PAGE_SIZE = 100

#: Cột được phép ghi từ kết quả OCR. Danh sách trắng, không phải `setattr` tuỳ ý: để model đặt
#: được `status` hay `company_id` là mở cửa cho chính nó quyết định vòng đời bản ghi.
OCR_COLUMNS: frozenset[str] = frozenset(
    {
        "full_name",
        "job_title",
        "company_name_raw",
        "email",
        "phone",
        "phone_alt",
        "address",
        "website",
        "language_detected",
        "confidence",
        # Việt hoá vẫn là dữ liệu do model sinh ra nên nằm trong đúng danh sách trắng này.
        "full_name_vi",
        "job_title_vi",
        "company_name_vi",
        "address_vi",
        "translation_meta",
    }
)


class DuplicateImageError(RuntimeError):
    """Ảnh này đã có trong DB (`image_hash` trùng). Router trả lại bản ghi cũ (task 3.1)."""

    def __init__(self, image_hash: str) -> None:
        super().__init__(f"Ảnh đã được quét trước đó (hash {image_hash[:12]}…).")
        self.image_hash = image_hash


async def get(
    db: AsyncSession, card_id: uuid.UUID, *, workspace_id: uuid.UUID
) -> BusinessCard | None:
    """Một danh thiếp **của đúng không gian này**, hoặc `None`.

    Không dùng `db.get()`: nó tra theo khoá chính nên không nhận thêm điều kiện, mà "lấy rồi so
    trong Python" thì chỉ cần một chỗ quên là rò. Router biến `None` thành **404** chứ không 403.
    """
    result = await db.execute(
        select(BusinessCard).where(
            BusinessCard.id == card_id, BusinessCard.workspace_id == workspace_id
        )
    )
    return result.scalar_one_or_none()


async def get_by_hash(
    db: AsyncSession, image_hash: str, *, workspace_id: uuid.UUID
) -> BusinessCard | None:
    """Tra theo SHA-256 của file gốc — chống upload trùng **trong phạm vi một không gian**.

    Tấm ảnh mà không gian khác đã có thì đây trả `None`, nên không lộ ra rằng họ có nó.
    """
    result = await db.execute(
        select(BusinessCard).where(
            BusinessCard.workspace_id == workspace_id, BusinessCard.image_hash == image_hash
        )
    )
    return result.scalar_one_or_none()


async def list_cards(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    q: str | None = None,
    status: str | None = None,
    company_id: uuid.UUID | None = None,
    language: str | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[Sequence[BusinessCard], int]:
    """Một trang danh thiếp + **tổng số bản ghi khớp bộ lọc**.

    Trả cả tổng chứ không chỉ danh sách: đếm ở tầng router phải dựng lại đúng bộ điều kiện này
    lần thứ hai, và hai bản sao của cùng một logic lọc chắc chắn sẽ lệch nhau.

    Sắp xếp `uploaded_at DESC, id DESC`: chỉ theo thời gian thì hai ảnh cùng một batch có thể
    trùng mốc tới từng micro giây, thứ tự giữa các trang sẽ nhảy.
    """
    conditions = _list_conditions(
        workspace_id=workspace_id, q=q, status=status, company_id=company_id, language=language
    )
    size = max(1, min(size, MAX_PAGE_SIZE))
    page = max(1, page)

    total = await db.scalar(select(func.count()).select_from(BusinessCard).where(*conditions))

    rows = await db.execute(
        select(BusinessCard)
        .where(*conditions)
        .order_by(BusinessCard.uploaded_at.desc(), BusinessCard.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    return rows.scalars().all(), int(total or 0)


async def delete_card(db: AsyncSession, card: BusinessCard) -> None:
    """Xoá bản ghi. **Không đụng tới file ảnh** — router lo.

    Xoá file là thao tác không hoàn tác được, còn xoá hàng thì rollback được. Gộp vào đây thì một
    transaction hỏng sẽ để lại bản ghi trỏ vào file đã bốc hơi.
    """
    await db.delete(card)
    await db.commit()


async def create_card(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    image_path: str | None,
    image_hash: str,
    fields: Mapping[str, Any] | None = None,
    ocr_raw_json: dict[str, Any] | None = None,
    status: CardStatus = CardStatus.PENDING,
    notes: str | None = None,
) -> BusinessCard:
    """Tạo một bản ghi danh thiếp và commit.

    Ném `DuplicateImageError` khi `image_hash` đã tồn tại. Router có kiểm trước rồi, nhưng hai
    lần upload cùng lúc lọt qua được bước đó — chỉ ràng buộc unique trong DB mới là thật.
    """
    card = BusinessCard(
        workspace_id=workspace_id,
        user_id=user_id,
        image_path=image_path,
        image_hash=image_hash,
        ocr_raw_json=ocr_raw_json,
        status=status,
        notes=notes,
    )
    for name, value in (fields or {}).items():
        if name in OCR_COLUMNS:
            setattr(card, name, value)

    db.add(card)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if _is_duplicate_hash(exc):
            raise DuplicateImageError(image_hash) from exc
        raise

    # Lấy về `uploaded_at`/`created_at` do PostgreSQL sinh (`server_default`).
    await db.refresh(card)
    return card


async def update_fields(
    db: AsyncSession,
    card: BusinessCard,
    fields: Mapping[str, Any],
    *,
    status: CardStatus | None = None,
    notes: str | None = None,
) -> BusinessCard:
    """Ghi đè các cột OCR của một bản ghi đã có (quét lại, hoặc người dùng sửa ở task 4.2)."""
    for name, value in fields.items():
        if name in OCR_COLUMNS:
            setattr(card, name, value)
    if status is not None:
        card.status = status
    if notes is not None:
        card.notes = notes

    await db.commit()
    await db.refresh(card)
    return card


def _list_conditions(
    *,
    workspace_id: uuid.UUID,
    q: str | None,
    status: str | None,
    company_id: uuid.UUID | None,
    language: str | None,
) -> list[ColumnElement[bool]]:
    """Điều kiện WHERE dùng chung cho cả câu đếm lẫn câu lấy trang (xem `list_cards`).

    `workspace_id` là điều kiện **đầu tiên và không thể tắt** — không ô nhập nào gỡ được nó ra.
    """
    conditions: list[ColumnElement[bool]] = [
        BusinessCard.workspace_id == workspace_id,
        # Thẻ đã gộp vào thẻ khác biến khỏi danh sách. Bản ghi vẫn còn và mở ra đọc được.
        BusinessCard.merged_into_id.is_(None),
    ]

    if q and (term := q.strip()):
        pattern = f"%{_escape_like(term)}%"
        conditions.append(or_(*(column.ilike(pattern, escape="\\") for column in SEARCH_COLUMNS)))
    if status:
        conditions.append(BusinessCard.status == status)
    if company_id is not None:
        conditions.append(BusinessCard.company_id == company_id)
    if language and (code := language.strip().lower()):
        conditions.append(BusinessCard.language_detected == code)
    return conditions


def _escape_like(term: str) -> str:
    r"""Vô hiệu hoá ký tự đại diện của `LIKE` trong chuỗi người dùng gõ.

    Gõ `%` mà không escape thì câu truy vấn thành `ILIKE '%%%'` — khớp mọi bản ghi và trông y như
    "bộ lọc không hoạt động". `_` còn tệ hơn: tìm `nguyen_van` lại ra `nguyen van`.
    """
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _is_duplicate_hash(exc: IntegrityError) -> bool:
    """Phân biệt vi phạm unique `image_hash` với mọi lỗi toàn vẹn khác.

    Nuốt nhầm một `IntegrityError` khác rồi báo "ảnh trùng" sẽ khiến người dùng đi tìm một bản
    ghi cũ không hề tồn tại. Kiểm cả chuỗi `image_hash` để không phụ thuộc vào một tên index.
    """
    return "ix_business_cards_workspace_id_image_hash" in str(exc.orig) or "image_hash" in str(
        exc.orig
    )
