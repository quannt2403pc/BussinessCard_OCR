"""Repository business_cards.

Chủ sở hữu: Q | Task: 3.5, 12.5 | xem Task.md

Lớp mỏng giữa router và ORM: router lo HTTP, file này lo câu truy vấn. Mục đích thật là để
`routers/cards.py` không phình ra khi thêm danh sách/lọc/phân trang ở task 4.1.

**Từ task 12.5, `user_id` là tham số BẮT BUỘC của mọi hàm ở đây** — keyword-only, không có giá
trị mặc định. Đó là chủ ý: một `user_id: uuid.UUID | None = None` sẽ khiến "quên truyền" trở
thành "đọc dữ liệu của tất cả mọi người", tức mặc định MỞ. Kiểu như hiện tại thì quên truyền là
`TypeError` ngay lần chạy đầu tiên.

Lọc đặt ở **mệnh đề WHERE của cùng câu truy vấn**, không sàng lại trong Python: sàng sau vừa rò
(câu đếm, `LIMIT`/`OFFSET` đã tính trên tập chưa lọc) vừa làm phân trang sai số.
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
from app.repositories import event as event_repo

logger = logging.getLogger(__name__)

#: Cột được quét khi tìm kiếm tự do (`?q=`) ở task 4.1 — ba cột `docs/api.md` đã chốt, cộng hai
#: cột Việt hoá tương ứng từ EX-06.
#: Không quét `address`/`notes`: gõ "Hà Nội" sẽ ra toàn bộ danh thiếp, ô tìm kiếm thành vô dụng.
#:
#: Hai cột `*_vi` là điều kiện để ô tìm kiếm còn dùng được với thẻ nước ngoài: gõ "Tanaka" mà
#: chỉ quét `full_name` thì không bao giờ khớp `田中 太郎` — người dùng phải gõ được chữ Nhật
#: mới tìm ra tấm thẻ của chính mình. Không quét `job_title_vi`/`address_vi` vì `job_title` và
#: `address` cũng không được quét, giữ nguyên một quy tắc cho cả hai bản.
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

#: Cột được phép ghi từ kết quả OCR. Danh sách trắng, không phải `setattr` tuỳ ý: dữ liệu này do
#: một model ngôn ngữ sinh ra, để nó đặt được `status` hay `company_id` là mở cửa cho chính nó
#: quyết định vòng đời bản ghi.
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
        # Việt hoá sau khi quét (EX-02). Vẫn là dữ liệu do model sinh ra nên nằm trong đúng danh
        # sách trắng này, không phải một lối ghi riêng đi vòng qua nó.
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


async def get(db: AsyncSession, card_id: uuid.UUID, *, user_id: uuid.UUID) -> BusinessCard | None:
    """Một danh thiếp **của đúng người này**, hoặc `None`.

    Không dùng `db.get()` nữa: nó tra theo khoá chính nên không nhận thêm điều kiện, mà "lấy rồi
    so `card.user_id` trong Python" là đúng cái bẫy đã nói ở đầu file — chỉ cần một chỗ gọi quên
    so là rò. Thẻ của người khác trả `None`, và router biến `None` thành **404** chứ không 403
    (Plan.md mục 4: 403 là tự khai rằng bản ghi đó tồn tại).
    """
    result = await db.execute(
        select(BusinessCard).where(BusinessCard.id == card_id, BusinessCard.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def get_by_hash(
    db: AsyncSession, image_hash: str, *, user_id: uuid.UUID
) -> BusinessCard | None:
    """Tra theo SHA-256 của file gốc — chống upload trùng **trong phạm vi một người** (12.3).

    B upload đúng tấm ảnh A đã có thì đây trả `None` → B quét được bình thường và không hề biết
    A có tấm thẻ ấy. Chính B upload lại lần hai mới bị chặn.
    """
    result = await db.execute(
        select(BusinessCard).where(
            BusinessCard.user_id == user_id, BusinessCard.image_hash == image_hash
        )
    )
    return result.scalar_one_or_none()


async def list_cards(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    q: str | None = None,
    status: str | None = None,
    company_id: uuid.UUID | None = None,
    language: str | None = None,
    event_id: uuid.UUID | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[Sequence[BusinessCard], int]:
    """Một trang danh thiếp + **tổng số bản ghi khớp bộ lọc** (task 4.1).

    Trả cả tổng chứ không chỉ danh sách: thiếu nó thì UI không vẽ được thanh phân trang, mà đếm
    ở tầng router lại phải dựng lại đúng bộ điều kiện này lần thứ hai — hai bản sao của cùng một
    logic lọc là chỗ chắc chắn sẽ lệch nhau về sau.

    Sắp xếp `uploaded_at DESC, id DESC`: chỉ theo thời gian thôi thì hai ảnh upload trong cùng
    một batch có thể trùng mốc thời gian tới từng micro giây, thứ tự giữa các trang sẽ nhảy và
    có bản ghi hiện hai lần / mất hẳn.
    """
    conditions = _list_conditions(
        user_id=user_id,
        q=q,
        status=status,
        company_id=company_id,
        language=language,
        event_id=event_id,
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
    """Xoá bản ghi. **Không đụng tới file ảnh** — router lo (task 4.2).

    Tách đôi có chủ đích: xoá file là thao tác không hoàn tác được trên hệ thống tệp, còn xoá
    hàng thì rollback được. Gộp vào đây thì một transaction hỏng sẽ để lại bản ghi trỏ vào file
    đã bốc hơi.
    """
    await db.delete(card)
    await db.commit()


async def create_card(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    image_path: str,
    image_hash: str,
    fields: Mapping[str, Any] | None = None,
    ocr_raw_json: dict[str, Any] | None = None,
    status: CardStatus = CardStatus.PENDING,
    notes: str | None = None,
) -> BusinessCard:
    """Tạo một bản ghi danh thiếp và commit.

    Ném `DuplicateImageError` khi `image_hash` đã tồn tại. Router có kiểm trước rồi, nhưng vẫn
    bắt ở đây vì hai lần upload cùng lúc (batch, task 5.2) lọt qua được bước kiểm đó — chỉ ràng
    buộc unique trong DB mới là thật.
    """
    card = BusinessCard(
        user_id=user_id,
        image_path=image_path,
        image_hash=image_hash,
        ocr_raw_json=ocr_raw_json,
        status=status,
        notes=notes,
        # Nhãn sự kiện đang diễn ra, đóng dấu ngay lúc tạo (task NEXT-03 của T; T sửa file của
        # Q, Q review PR). Đặt ở đây chứ không ở router vì **đây là chỗ duy nhất sinh ra một
        # `business_cards`** — upload một ảnh và upload hàng loạt đều đi qua đây, nên không có
        # đường nào quét được một tấm thẻ mà quên mất nhãn. `None` khi người dùng chưa bật sự
        # kiện nào, và đó là mặc định.
        event_id=await event_repo.active_event_id(db, user_id),
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
    user_id: uuid.UUID,
    q: str | None,
    status: str | None,
    company_id: uuid.UUID | None,
    language: str | None,
    event_id: uuid.UUID | None = None,
) -> list[ColumnElement[bool]]:
    """Điều kiện WHERE dùng chung cho cả câu đếm lẫn câu lấy trang (xem `list_cards`).

    `user_id` là điều kiện **đầu tiên và không thể tắt** — nó không đến từ tham số URL nào, nên
    không có ô nhập nào của người dùng gỡ được nó ra.
    """
    conditions: list[ColumnElement[bool]] = [
        BusinessCard.user_id == user_id,
        # Thẻ đã gộp vào thẻ khác biến khỏi danh sách (task NEXT-04 của T; T sửa file của Q, Q
        # review PR). Bản ghi vẫn còn và mở ra đọc được, nó chỉ không bị đếm hai lần nữa.
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
    if event_id is not None:
        conditions.append(BusinessCard.event_id == event_id)

    return conditions


def _escape_like(term: str) -> str:
    r"""Vô hiệu hoá ký tự đại diện của `LIKE` trong chuỗi người dùng gõ.

    Gõ `%` vào ô tìm kiếm mà không escape thì câu truy vấn thành `ILIKE '%%%'` — khớp mọi bản
    ghi và trông y như "bộ lọc không hoạt động". `_` còn tệ hơn: nó khớp một ký tự bất kỳ nên
    tìm `nguyen_van` lại ra `nguyen van`, sai một cách rất khó nhận ra.
    """
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _is_duplicate_hash(exc: IntegrityError) -> bool:
    """Phân biệt vi phạm unique `image_hash` với mọi lỗi toàn vẹn khác.

    Nuốt nhầm một `IntegrityError` khác (khoá ngoại `company_id` chẳng hạn) rồi báo "ảnh trùng"
    sẽ khiến người dùng đi tìm một bản ghi cũ không hề tồn tại.

    Tên index đổi ở revision `0005` (`…_user_id_image_hash`); vẫn kiểm cả chuỗi `image_hash` để
    hàm này không phụ thuộc vào đúng một tên index.
    """
    return "ix_business_cards_user_id_image_hash" in str(exc.orig) or "image_hash" in str(exc.orig)
