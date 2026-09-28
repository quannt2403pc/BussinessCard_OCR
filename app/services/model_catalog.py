"""Ba chức năng ↔ model nào dùng được — và model nào **không** được phép chọn.

Chủ sở hữu: Q | Task: EX-12, EX-14 | xem `docs/adr-model-per-feature.md`

Hai câu hỏi: ô chọn của chức năng X hiện model nào (`allowed_for()`), và người dùng đã chọn model
M cho chức năng X thì gọi model nào (`resolve()`).

Hai đường hỏng mà nó tồn tại để chặn, **cả hai đều im lặng**:

* Model không đọc được ảnh vẫn trả lời bình thường với nội dung bịa — mọi tấm thẻ vào hệ thống
  đều rỗng mà không một dòng log đỏ nào.
* Model không tra cứu được Internet vẫn trả `200`, chỉ thiếu `groundingMetadata` — hồ sơ sinh ra
  **trống trơn** và người dùng chỉ thấy "hệ thống dở" chứ không thấy "tôi chọn nhầm".

**Hai bảng dưới đây là ảnh chụp một phép đo, không phải hằng số** — danh mục channel tự đổi theo
thời gian, nên danh sách cho phép **luôn giao với danh mục thật lúc chạy**.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Sequence
from typing import Literal, get_args

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.repositories import model_pref as pref_repo
from app.services.cliproxy_client import CliProxyClient, CliProxyError

logger = logging.getLogger(__name__)

Feature = Literal["ocr", "enrich", "chat"]

#: Ba chức năng chủ dự án nêu. Bước **Việt hoá sau khi quét** cố ý **không** có khoá riêng — nó
#: đi theo `ocr`: người dùng không nhìn thấy nó như một chức năng.
FEATURES: tuple[Feature, ...] = get_args(Feature)

FEATURE_LABELS: dict[Feature, str] = {
    "ocr": "Quét danh thiếp",
    "enrich": "Lập hồ sơ doanh nghiệp",
    "chat": "Trợ lý AI",
}

#: Model **đo được là KHÔNG đọc nổi ảnh**. Danh sách *cấm*, không phải danh sách *cho phép*:
#: 11/12 model nhận `inline_data`, và cái sai này lộ ra ngay — quét một tấm thẻ là biết.
NO_VISION: frozenset[str] = frozenset({"gpt-oss-120b-medium"})

#: Model **đo được là tra cứu Internet có nguồn**. Danh sách *cho phép*, ngược hẳn `NO_VISION`,
#: và đây là bất đối xứng có chủ đích: chỉ 7/12 model làm được, mà cái sai này **không lộ ra** —
#: hồ sơ vẫn sinh, chỉ trống trường, sau hàng phút chạy nền.
#:
#: Model mới xuất hiện thì chạy lại `scripts/spike_model_matrix.py` rồi thêm vào đây.
WITH_WEB_SEARCH: frozenset[str] = frozenset(
    {
        "gemini-3-flash",
        "gemini-3.1-flash-lite",
        "gemini-3.1-flash-image",
        "gemini-3.5-flash-lite",
        "gemini-3.6-flash-high",
        "gemini-3.7-flash-high",
        "gemini-3.8-flash-high",
    }
)

#: Danh mục model của channel đổi theo thời gian nhưng không theo từng giây. Nhớ lại trong tiến
#: trình để `resolve()` — chạy trên **mọi** lượt quét/enrich/hỏi — không kéo thêm một lời gọi HTTP.
CATALOGUE_TTL_SECONDS = 300.0

_catalogue_cache: tuple[float, list[str]] | None = None


def supports(model: str, feature: Feature) -> bool:
    """Model này dùng được cho chức năng kia không? Chỉ tra bảng, **không gọi mạng**.

    `chat` luôn `True`: quy tắc 1 của `prompts/assistant.py` **cấm** model dùng kiến thức ngoài
    Knowledge Base, nên model tra được Internet cũng không được phép dùng khả năng đó.
    """
    if feature == "ocr":
        return model not in NO_VISION
    if feature == "enrich":
        return model in WITH_WEB_SEARCH
    return True


def allowed_for(feature: Feature, catalogue: Sequence[str]) -> list[str]:
    """Danh sách model được phép chọn cho một chức năng = danh mục thật ∩ bảng năng lực.

    Giao với `catalogue` chứ không trả thẳng bảng năng lực: bảng là ảnh chụp của một phép đo, còn
    danh mục mới là sự thật lúc chạy.
    """
    return [model for model in catalogue if supports(model, feature)]


async def catalogue(client: CliProxyClient | None = None) -> list[str]:
    """Danh mục model thật của channel, nhớ trong `CATALOGUE_TTL_SECONDS` giây.

    Hỏng thì trả rỗng chứ không ném: không lấy được danh mục là chuyện của CLIProxy, không đáng
    làm hỏng lượt quét của người dùng.
    """
    global _catalogue_cache
    now = time.monotonic()
    if _catalogue_cache is not None and now - _catalogue_cache[0] < CATALOGUE_TTL_SECONDS:
        return _catalogue_cache[1]

    try:
        if client is not None:
            models = await client.model_ids()
        else:
            async with CliProxyClient() as proxy:
                models = await proxy.model_ids()
    except CliProxyError as exc:
        logger.info("Không lấy được danh mục model: %s", exc)
        return []

    _catalogue_cache = (now, models)
    return models


def forget_catalogue() -> None:
    """Xoá bộ nhớ danh mục. Gọi sau khi người dùng vừa kết nối/ngắt OAuth, và trong test."""
    global _catalogue_cache
    _catalogue_cache = None


async def resolve(db: AsyncSession, user_id: uuid.UUID, feature: Feature) -> str:
    """Tên model **chưa có tiền tố** sẽ dùng cho `feature` của người dùng này.

    Thứ tự: lựa chọn của người dùng → `LLM_MODEL` nếu họ chưa chọn → `LLM_MODEL` nếu lựa chọn của
    họ nay không dùng được nữa.

    Nhánh thứ ba **rơi về mặc định kèm cảnh báo, không ném lỗi**: người dùng không làm gì sai,
    model tự biến mất dưới chân họ.
    """
    choices = await pref_repo.as_dict(db, user_id)
    chosen = choices.get(feature)
    if not chosen:
        return settings.llm_model

    if not supports(chosen, feature):
        logger.warning(
            "Model %r không còn dùng được cho %s (người dùng %s) — dùng %r thay thế",
            chosen,
            feature,
            user_id,
            settings.llm_model,
        )
        return settings.llm_model

    # Danh mục rỗng = không hỏi được CLIProxy, **không** phải "model đã bị gỡ". Đổi model của
    # người dùng vì ta đang mất mạng là một kiểu hỏng âm thầm hơn hẳn.
    models = await catalogue()
    if models and chosen not in models:
        logger.warning(
            "Model %r đã biến mất khỏi danh mục channel (người dùng %s, %s) — dùng %r thay thế",
            chosen,
            user_id,
            feature,
            settings.llm_model,
        )
        return settings.llm_model

    return chosen
