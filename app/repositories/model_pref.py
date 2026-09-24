"""Repository `user_model_prefs` — đọc/ghi lựa chọn model của từng người dùng.

Chủ sở hữu: Q | Task: EX-13 | xem Task.md, `docs/adr-model-per-feature.md`

Lớp mỏng, cùng lối `repositories/chat.py`: router lo HTTP, file này lo câu SQL.

Hai điều đáng nêu:

1. **`get()` trả `None` là chuyện thường, không phải lỗi.** Phần lớn người dùng không bao giờ mở
   `/settings` để chọn model, nên họ **không có dòng nào** ở bảng này. Chỗ gọi phải hiểu "không có
   dòng" = "cả ba chức năng dùng `LLM_MODEL`", y hệt "có dòng nhưng ba cột đều `NULL`".

2. **`save()` xoá hẳn dòng khi cả ba lựa chọn về `None`.** Giữ lại một dòng toàn `NULL` thì không
   sai, nhưng nó là rác mang nghĩa *"người này đã từng chọn gì đó rồi bỏ"* — mà nghĩa ấy không ai
   dùng, còn `ON DELETE CASCADE` và bản backup thì phải mang nó theo mãi.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.model_pref import UserModelPref

#: Ba khoá chức năng ↔ ba cột. Một chỗ duy nhất biết ánh xạ này; `services/model_catalog.py` giữ
#: định nghĩa chức năng, còn ở đây chỉ là cầu nối sang tên cột.
COLUMN_BY_FEATURE: dict[str, str] = {
    "ocr": "ocr_model",
    "enrich": "enrich_model",
    "chat": "chat_model",
}


async def get(db: AsyncSession, user_id: uuid.UUID) -> UserModelPref | None:
    """Dòng lựa chọn của một người, hoặc `None` nếu người đó chưa chọn gì bao giờ."""
    return await db.scalar(select(UserModelPref).where(UserModelPref.user_id == user_id))


async def as_dict(db: AsyncSession, user_id: uuid.UUID) -> dict[str, str | None]:
    """Lựa chọn dưới dạng `{feature: model | None}` — luôn đủ ba khoá, kể cả khi chưa có dòng nào.

    Trả đủ ba khoá là có chủ đích: chỗ gọi không phải phân biệt "chưa có dòng" với "có dòng nhưng
    cột rỗng", vì hai thứ đó **cùng một nghĩa** (xem ghi chú đầu file).
    """
    row = await get(db, user_id)
    return {
        feature: getattr(row, column) if row is not None else None
        for feature, column in COLUMN_BY_FEATURE.items()
    }


async def save(db: AsyncSession, user_id: uuid.UUID, choices: dict[str, str | None]) -> None:
    """Ghi lựa chọn cho các chức năng có trong `choices`. **Không commit** — chỗ gọi commit.

    `choices` chỉ cần chứa chức năng muốn đổi; chức năng không nhắc tới giữ nguyên giá trị cũ.
    Nhờ vậy `PUT /api/integration/models` gửi một ô cũng được mà gửi cả ba cũng được, không cần
    hai endpoint.
    """
    unknown = set(choices) - set(COLUMN_BY_FEATURE)
    if unknown:
        raise ValueError(f"Chức năng không có thật: {sorted(unknown)}")

    row = await get(db, user_id)
    if row is None:
        if not any(choices.values()):
            return  # chưa có dòng mà cũng không chọn gì — không dựng dòng rỗng làm gì
        row = UserModelPref(user_id=user_id)
        db.add(row)

    for feature, model in choices.items():
        setattr(row, COLUMN_BY_FEATURE[feature], model)

    if not any(getattr(row, column) for column in COLUMN_BY_FEATURE.values()):
        await db.delete(row)


async def clear(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Bỏ hết lựa chọn của một người — về dùng `LLM_MODEL` cho cả ba. **Không commit.**"""
    await db.execute(delete(UserModelPref).where(UserModelPref.user_id == user_id))
