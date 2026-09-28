"""Ba chức năng ↔ model nào dùng được — và model nào **không** được phép chọn.

Chủ sở hữu: Q | Task: EX-12 (đo), EX-14 (phân giải) | xem `docs/adr-model-per-feature.md`

File này trả lời đúng hai câu hỏi, và cố tình không biết gì thêm:

1. **Ô chọn của chức năng X hiện những model nào?** → `allowed_for()`
2. **Người dùng đã chọn model M cho chức năng X, giờ gọi model nào?** → `resolve()`

Hai đường hỏng mà nó tồn tại để chặn, **cả hai đều im lặng** (đo thật ở `EX-12`, ADR mục 3):

* Model không đọc được ảnh vẫn trả lời bình thường với nội dung bịa — `gpt-oss-120b-medium` trả
  `{"full_name": "none", "company": "none"}` cho một tấm thẻ rõ nét. Chọn nhầm nó cho quét danh
  thiếp thì mọi tấm thẻ vào hệ thống đều rỗng mà không một dòng log đỏ nào.
* Model không tra cứu được Internet vẫn trả `HTTP 200` kèm câu trả lời đọc được, chỉ thiếu
  `groundingMetadata` — `enrichment.py` bỏ mọi trường không nguồn nên hồ sơ sinh ra **trống trơn**,
  tức trượt tiêu chí **A5**, và người dùng chỉ thấy "hệ thống dở" chứ không thấy "tôi chọn nhầm".

**Hai bảng dưới đây là ảnh chụp phép đo ngày 2026-09-24, không phải hằng số.** Danh mục channel
đã đổi 11 → 12 model trong 14 ngày (I-03 so với hôm nay). Vì vậy danh sách cho phép **luôn giao
với danh mục thật lúc chạy** (ADR mục 4, M4), và mã ở đây không bao giờ tự sinh ra một tên model.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Collection, Sequence
from typing import Literal, get_args

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.repositories import model_pref as pref_repo
from app.services.cliproxy_client import CliProxyClient, CliProxyError
from app.services.llm import LLMPaymentRequiredError

logger = logging.getLogger(__name__)

Feature = Literal["ocr", "enrich", "chat"]

#: Ba chức năng chủ dự án nêu (ADR mục 4, M2). Bước **Việt hoá sau khi quét** cố ý **không** có
#: khoá riêng — nó đi theo `ocr` (M3/QĐ-5): là lượt gọi thứ hai *bên trong* luồng quét, người dùng
#: không nhìn thấy nó như một chức năng, nên thêm ô thứ tư chỉ là thêm một ô để chọn sai.
FEATURES: tuple[Feature, ...] = get_args(Feature)

FEATURE_LABELS: dict[Feature, str] = {
    "ocr": "Quét danh thiếp",
    "enrich": "Lập hồ sơ doanh nghiệp",
    "chat": "Trợ lý AI",
}

#: Model **đo được là KHÔNG đọc nổi ảnh** (`EX-12`, 2026-09-24). Danh sách *cấm*, không phải danh
#: sách *cho phép*: 11/12 model nhận `inline_data`, nên model lạ mặc định được coi là đọc được ảnh.
#: Chọn hướng này vì sai số của nó nhỏ và lộ ra ngay — quét một tấm thẻ là biết.
NO_VISION: frozenset[str] = frozenset({"gpt-oss-120b-medium"})

#: Model **đo được là tra cứu Internet có nguồn** (`EX-12`). Danh sách *cho phép*, ngược hẳn với
#: `NO_VISION` ở trên, và đây là bất đối xứng có chủ đích:
#:
#: * chỉ **7/12** model làm được, tức không làm được mới là đa số — mặc định cho qua là sai nhiều
#:   hơn đúng;
#: * và cái sai này **không lộ ra**: hồ sơ vẫn sinh, chỉ là trống trường, mất hàng phút chạy nền
#:   rồi mới thấy. Một model lạ chưa đo mà lọt vào ô *Lập hồ sơ* là đổi tiêu chí A5 lấy sự tiện.
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

#: Danh mục model của channel đổi theo thời gian nhưng không đổi theo từng giây. Nhớ lại trong
#: tiến trình để `resolve()` — chạy trên **mọi** lượt quét, lượt enrich và lượt hỏi — không kéo
#: theo một lời gọi HTTP nữa. 5 phút đủ ngắn để model mới xuất hiện trong ô chọn ngay trong buổi
#: làm việc, đủ dài để không ai thấy nó trong log.
CATALOGUE_TTL_SECONDS = 300.0

_catalogue_cache: tuple[float, list[str]] | None = None


def supports(model: str, feature: Feature) -> bool:
    """Model này dùng được cho chức năng kia không? Chỉ tra bảng, **không gọi mạng**.

    `chat` luôn `True`: trợ lý không cần ảnh, cũng không cần tra cứu Internet —
    `prompts/assistant.py` quy tắc 1 **cấm** model dùng kiến thức ngoài Knowledge Base, nên model
    tra được Internet cũng không được phép dùng khả năng đó.
    """
    if feature == "ocr":
        return model not in NO_VISION
    if feature == "enrich":
        return model in WITH_WEB_SEARCH
    return True


def allowed_for(feature: Feature, catalogue: Sequence[str]) -> list[str]:
    """Danh sách model được phép chọn cho một chức năng = danh mục thật ∩ bảng năng lực.

    Giao với `catalogue` chứ không trả thẳng `WITH_WEB_SEARCH`: bảng năng lực là ảnh chụp của một
    phép đo, còn danh mục mới là sự thật lúc chạy. Model đã bị channel gỡ bỏ mà vẫn hiện trong ô
    chọn là mời người dùng chọn một thứ chắc chắn hỏng.
    """
    return [model for model in catalogue if supports(model, feature)]


async def catalogue(client: CliProxyClient | None = None) -> list[str]:
    """Danh mục model thật của channel, nhớ trong `CATALOGUE_TTL_SECONDS` giây.

    Hỏng thì trả rỗng chứ không ném — cùng lối `routers/integration.py::_safe_model_ids`: không
    lấy được danh mục là chuyện của CLIProxy, không đáng làm hỏng lượt quét của người dùng.
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


async def resolve(
    db: AsyncSession,
    user_id: uuid.UUID,
    feature: Feature,
    *,
    avoid: Collection[str] = (),
) -> str:
    """Tên model **chưa có tiền tố** sẽ dùng cho `feature` của người dùng này.

    Thứ tự: lựa chọn của người dùng → `LLM_MODEL` nếu họ chưa chọn → `LLM_MODEL` nếu lựa chọn của
    họ nay không dùng được nữa → **model khác nếu cái vừa chọn vượt quá gói cước** (`avoid`).

    `avoid` là danh sách model mà **tài khoản Google đang kết nối** bị từ chối vì gói cước —
    `user_credentials.paid_only_models()` đọc nó từ `cooldowns` của chính credential ấy (I-42).
    Nó là tham số chứ không phải hằng số trong file này, và đó là điểm mấu chốt: cùng một model,
    tài khoản có gói thì gọi được, tài khoản free thì không. Một danh sách *Pro* cứng sẽ chặn
    nhầm người đã trả tiền.

    Nhánh thứ ba là quyết định **M5** của ADR: model đã lưu có thể biến mất khỏi danh mục (danh
    mục tự đổi) hoặc bị bảng năng lực loại sau một lượt đo mới. Lúc đó **rơi về mặc định kèm cảnh
    báo, không ném lỗi** — cùng lối đã chốt ở `EX-02`: mất bản dịch còn hơn mất tấm thẻ vừa quét.
    Ở đây còn đúng hơn thế: người dùng không làm gì sai, model tự biến mất dưới chân họ.
    """
    choices = await pref_repo.as_dict(db, user_id)
    chosen = choices.get(feature)
    if not chosen:
        # **Cả nhánh mặc định cũng phải tránh** — đây mới là đường của người dùng free, không
        # phải nhánh dưới: họ vừa đăng nhập, chưa chọn gì bao giờ, nên `LLM_MODEL` là thứ duy
        # nhất họ từng gọi. Thoát sớm ở đây là để nguyên đúng cái lỗi I-42 sinh ra để chữa.
        return await _dodge_paid_only(settings.llm_model, feature, avoid, user_id)

    if not supports(chosen, feature):
        logger.warning(
            "Model %r không còn dùng được cho %s (người dùng %s) — dùng %r thay thế",
            chosen,
            feature,
            user_id,
            settings.llm_model,
        )
        return await _dodge_paid_only(settings.llm_model, feature, avoid, user_id)

    # Danh mục rỗng = không hỏi được CLIProxy, **không** phải "model đã bị gỡ". Tin lựa chọn của
    # người dùng trong trường hợp đó: đổi model của họ vì ta đang mất mạng là một kiểu hỏng khác,
    # âm thầm hơn hẳn.
    models = await catalogue()
    if models and chosen not in models:
        logger.warning(
            "Model %r đã biến mất khỏi danh mục channel (người dùng %s, %s) — dùng %r thay thế",
            chosen,
            user_id,
            feature,
            settings.llm_model,
        )
        return await _dodge_paid_only(settings.llm_model, feature, avoid, user_id)

    return await _dodge_paid_only(chosen, feature, avoid, user_id)


async def _dodge_paid_only(
    chosen: str,
    feature: Feature,
    avoid: Collection[str],
    user_id: uuid.UUID,
) -> str:
    """Đổi sang model khác nếu `chosen` vượt quá gói cước của tài khoản đang kết nối (I-42).

    Vì sao tránh **trước khi gọi** chứ không thử-rồi-đổi: CLIProxy đã ghi `cooldowns` từ lần
    hỏng đầu, nên ta biết chắc mà không tốn thêm lượt gọi nào. Thử lại rồi mới đổi thì mỗi lượt
    quét của người dùng free đều mất một vòng gọi hỏng — và với upload hàng loạt là mất một vòng
    cho **mỗi** tấm thẻ.

    Model thay thế lấy từ `allowed_for()` nên **không bao giờ hạ cấp năng lực**: thay cho *Quét
    danh thiếp* thì vẫn phải đọc được ảnh, thay cho *Lập hồ sơ* thì vẫn phải tra được Internet.
    Thà báo lỗi còn hơn lặng lẽ đổi sang model sinh ra hồ sơ trống (`EX-12`, tiêu chí A5).
    """
    # **Thoát trước khi hỏi danh mục.** `avoid` rỗng là trường hợp thường gặp nhất — không ai
    # bị chặn model nào — và bản đầu của I-42 gọi `catalogue()` ngay cả lúc đó, biến một nhánh
    # vốn trả về tức thì thành một lượt HTTP. 14 test đỏ vì đúng chuyện này, và mỗi lượt quét
    # của mọi người dùng phải trả giá cho một thứ gần như không bao giờ dùng tới.
    if chosen not in avoid:
        return chosen

    catalogue_models = await catalogue()
    # Danh mục rỗng = không hỏi được CLIProxy. Giữ nguyên lựa chọn, cùng lý do với nhánh trên:
    # đổi model của người dùng vì ta đang mất mạng là một kiểu hỏng âm thầm hơn.
    if not catalogue_models:
        return chosen

    thay_the = next((m for m in allowed_for(feature, catalogue_models) if m not in avoid), None)
    if thay_the is None:
        raise LLMPaymentRequiredError(
            f"Tài khoản Google đang kết nối không có gói dùng được model nào cho "
            f"{FEATURE_LABELS[feature]!r}. Vào /settings kết nối bằng tài khoản có gói phù hợp."
        )

    logger.warning(
        "Model %r vượt gói cước của người dùng %s (%s) — dùng %r thay thế",
        chosen,
        user_id,
        feature,
        thay_the,
    )
    return thay_the
