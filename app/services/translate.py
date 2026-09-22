"""Việt hoá danh thiếp sau khi quét: dịch chức vụ / loại hình pháp nhân, phiên âm tên riêng.

Chủ sở hữu: Q | Task: EX-02 | xem Task.md

Bước thứ hai của hậu xử lý F1, chạy ngay sau `services/normalize.py` (3.6) và là **lượt gọi
model thứ hai** — lý do tách khỏi prompt OCR nằm ở đầu `app/prompts/translate.py`.

Hai tầng, cố ý xếp theo thứ tự này:

1. **Bảng tra cứu tất định** (`LEGAL_FORMS`, `JOB_TITLES`) — không gọi mạng, luôn ra cùng một
   kết quả, test được bằng `pytest` không cần model. Phủ phần lặp đi lặp lại và hay bị dịch mỗi
   lúc một khác: `株式会社` phải luôn là *Công ty Cổ phần*, không lúc *Công ty cổ phần* lúc
   *Tập đoàn*.
2. **Model** — phủ phần còn lại, tức là chính phần mà bảng tra cứu không bao giờ phủ nổi: phiên
   âm tên riêng của **bất cứ ngôn ngữ nào** (yêu cầu số 3 của EX). Không có thư viện Python nào
   cho âm Hán Việt, và một bảng chức vụ viết tay thì luôn thiếu đúng cái chức vụ vừa gặp.

Model hỏng (chưa kết nối OAuth, hết hạn mức, trả chữ không phải JSON) thì **rơi xuống tầng 1**
chứ không ném lỗi lên: mất bản Việt hoá là mất một tiện ích, mất tấm thẻ vừa quét mới là hỏng.
Chỗ gọi đọc `translation_meta["source"]` để biết kết quả đến từ đâu.

**Không ghi đè bản gốc.** Bốn cột `*_vi` là cột riêng (revision `0006`); `full_name`,
`company_name_raw`… giữ nguyên chữ trên thẻ để giao diện in bản gốc làm chú thích nhỏ dưới bản
dịch, và để `ocr_raw_json` còn đối chiếu được với ảnh.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.prompts import translate as prompts
from app.services import llm
from app.services.cliproxy_client import CliProxyClient
from app.services.llm_json import JsonExtractError, extract_json_object
from app.services.normalize import squash_spaces

logger = logging.getLogger(__name__)

#: Trần độ dài 3 cột `VARCHAR(255)`. Model lảm nhảm một đoạn giải thích vào `company_name_vi` là
#: chuyện có thật; cắt ở đây để một lượt Việt hoá hỏng không làm hỏng luôn lượt ghi DB.
_MAX_LEN = 255

#: Loại hình pháp nhân → tiếng Việt, kèm **vị trí được phép khớp**: `s` = chỉ cuối tên,
#: `p` = chỉ đầu tên, `b` = cả hai đầu. Khoá là chữ **in trên thẻ**, giá trị tiếng Việt là dạng
#: duy nhất được dùng trong toàn hệ thống.
#:
#: Vị trí không phải chi tiết thừa: `Group` chỉ là loại hình khi đứng CUỐI — khớp cả ở đầu thì
#: `Group Dynamics Institute` bị bóc mất chữ đầu và thành *Tập đoàn Dynamics Institute*, một
#: công ty khác hẳn. Ngược lại `PT` (Indonesia) và `OOO` (Nga) chỉ đứng đầu, còn `株式会社` thì
#: thật sự đứng được cả hai đầu.
#:
#: Thứ tự cũng có ý nghĩa: quét từ trên xuống nên dạng dài phải đứng trước dạng ngắn nằm trong
#: nó (`股份有限公司` trước `有限公司`, `Co., Ltd.` trước `Ltd`), nếu không dạng ngắn khớp trước
#: và phần thừa của hậu tố rơi vào tên riêng.
LEGAL_FORMS: tuple[tuple[str, str, str], ...] = (
    # --- Nhật ---
    ("株式会社", "Công ty Cổ phần", "b"),
    ("有限会社", "Công ty TNHH", "b"),
    ("合同会社", "Công ty TNHH", "b"),
    ("合資会社", "Công ty Hợp danh", "b"),
    ("社団法人", "Hiệp hội", "b"),
    # --- Hàn ---
    ("주식회사", "Công ty Cổ phần", "b"),
    ("유한회사", "Công ty TNHH", "b"),
    # --- Trung ---
    ("股份有限公司", "Công ty Cổ phần", "s"),
    ("有限責任公司", "Công ty TNHH", "s"),
    ("有限责任公司", "Công ty TNHH", "s"),
    ("有限公司", "Công ty TNHH", "s"),
    ("集团", "Tập đoàn", "s"),
    ("集團", "Tập đoàn", "s"),
    # --- Latin: khớp nguyên từ (xem `_strip_latin_form`) ---
    ("Joint Stock Company", "Công ty Cổ phần", "s"),
    ("Company Limited", "Công ty TNHH", "s"),
    ("Incorporated", "Công ty Cổ phần", "s"),
    ("Corporation", "Tập đoàn", "s"),
    ("Co., Ltd.", "Công ty TNHH", "s"),
    ("Co Ltd", "Công ty TNHH", "s"),
    ("Pte Ltd", "Công ty TNHH", "s"),
    ("Sdn Bhd", "Công ty TNHH", "s"),
    ("Pvt Ltd", "Công ty TNHH", "s"),
    ("JSC", "Công ty Cổ phần", "b"),
    ("PLC", "Công ty Đại chúng", "s"),
    ("LLC", "Công ty TNHH", "s"),
    ("Ltd", "Công ty TNHH", "s"),
    ("Inc", "Công ty Cổ phần", "s"),
    ("Corp", "Tập đoàn", "s"),
    ("GmbH", "Công ty TNHH", "s"),
    ("S.p.A.", "Công ty Cổ phần", "s"),
    ("S.A.", "Công ty Cổ phần", "s"),
    ("B.V.", "Công ty TNHH", "s"),
    ("N.V.", "Công ty Cổ phần", "s"),
    ("AG", "Công ty Cổ phần", "s"),
    ("OOO", "Công ty TNHH", "p"),
    ("PAO", "Công ty Cổ phần", "p"),
    ("PT", "Công ty TNHH", "p"),
    ("Group", "Tập đoàn", "s"),
)

#: Chức vụ hay gặp → tiếng Việt. Không nhằm phủ hết (không bảng nào phủ hết được) mà nhằm **giữ
#: một cách dịch duy nhất** cho những chức vụ xuất hiện đi xuất hiện lại, và làm lưới đỡ khi
#: model không gọi được. Khoá đã hạ chữ thường + bỏ dấu chấm, xem `_title_key()`.
JOB_TITLES: dict[str, str] = {
    # tiếng Anh
    "ceo": "Tổng giám đốc",
    "chief executive officer": "Tổng giám đốc",
    "cto": "Giám đốc Công nghệ",
    "cfo": "Giám đốc Tài chính",
    "coo": "Giám đốc Vận hành",
    "cmo": "Giám đốc Marketing",
    "president": "Chủ tịch",
    "vice president": "Phó chủ tịch",
    "chairman": "Chủ tịch Hội đồng quản trị",
    "general director": "Tổng giám đốc",
    "deputy general director": "Phó tổng giám đốc",
    "director": "Giám đốc",
    "deputy director": "Phó giám đốc",
    "manager": "Trưởng phòng",
    "sales manager": "Trưởng phòng Kinh doanh",
    "marketing manager": "Trưởng phòng Marketing",
    "project manager": "Trưởng dự án",
    "product manager": "Trưởng sản phẩm",
    "hr manager": "Trưởng phòng Nhân sự",
    "team leader": "Trưởng nhóm",
    "senior engineer": "Kỹ sư cao cấp",
    "software engineer": "Kỹ sư phần mềm",
    "engineer": "Kỹ sư",
    "chief accountant": "Kế toán trưởng",
    "accountant": "Kế toán",
    "secretary": "Thư ký",
    "consultant": "Chuyên viên tư vấn",
    "specialist": "Chuyên viên",
    "staff": "Nhân viên",
    "sales executive": "Nhân viên kinh doanh",
    "founder": "Nhà sáng lập",
    "co-founder": "Đồng sáng lập",
    # tiếng Nhật
    "代表取締役社長": "Chủ tịch kiêm Tổng giám đốc",
    "代表取締役": "Tổng giám đốc",
    "社長": "Tổng giám đốc",
    "副社長": "Phó tổng giám đốc",
    "専務": "Giám đốc điều hành",
    "営業部長": "Trưởng phòng Kinh doanh",
    "技術部長": "Trưởng phòng Kỹ thuật",
    "部長": "Trưởng phòng",
    "次長": "Phó phòng",
    "課長": "Trưởng bộ phận",
    "係長": "Tổ trưởng",
    "主任": "Chuyên viên chính",
    "営業担当": "Nhân viên kinh doanh",
    "顧問": "Cố vấn",
    # tiếng Hàn
    "대표이사": "Tổng giám đốc",
    "사장": "Tổng giám đốc",
    "부사장": "Phó tổng giám đốc",
    "이사": "Giám đốc",
    "부장": "Trưởng phòng",
    "차장": "Phó phòng",
    "과장": "Trưởng bộ phận",
    "대리": "Chuyên viên",
    "주임": "Chuyên viên chính",
    "사원": "Nhân viên",
    # tiếng Trung
    "总经理": "Tổng giám đốc",
    "總經理": "Tổng giám đốc",
    "副总经理": "Phó tổng giám đốc",
    "董事长": "Chủ tịch Hội đồng quản trị",
    "董事長": "Chủ tịch Hội đồng quản trị",
    "总监": "Giám đốc",
    "销售经理": "Trưởng phòng Kinh doanh",
    "经理": "Trưởng phòng",
    "經理": "Trưởng phòng",
    "主管": "Trưởng bộ phận",
    "工程师": "Kỹ sư",
    "工程師": "Kỹ sư",
    # tiếng Đức / Pháp / Nga — ba thứ tiếng hay gặp nhất ngoài 5 ngôn ngữ chính
    "geschäftsführer": "Giám đốc điều hành",
    "vertriebsleiter": "Trưởng phòng Kinh doanh",
    "directeur général": "Tổng giám đốc",
    "directeur commercial": "Giám đốc Kinh doanh",
    "директор": "Giám đốc",
    "генеральный директор": "Tổng giám đốc",
}

#: Có chữ Latin trong dạng loại hình pháp nhân → so khớp theo từ; không có → dính liền kiểu CJK.
_HAS_LATIN_RE = re.compile(r"[A-Za-zÀ-ÿ]")


class TranslationError(RuntimeError):
    """Không Việt hoá được.

    Chỉ nổi lên tới người dùng từ `POST /api/cards/{id}/translate` — họ vừa bấm nút nên cần biết
    vì sao hỏng. Luồng quét tự động thì nuốt lỗi và rơi về bảng tra cứu (xem docstring đầu file).
    """


@dataclass(frozen=True, slots=True)
class Translation:
    """Kết quả một lượt Việt hoá: giá trị 4 cột `*_vi` + siêu dữ liệu ghi vào `translation_meta`."""

    values: dict[str, str | None] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    def columns(self) -> dict[str, Any]:
        """Phần ghi thẳng vào `business_cards`, ghép được với `card_columns()` của CardExtraction."""
        out: dict[str, Any] = {name: self.values.get(name) for name in prompts.VI_COLUMNS.values()}
        out["translation_meta"] = self.meta or None
        return out

    @property
    def is_empty(self) -> bool:
        return not any(self.values.values())


async def translate_card(
    fields: Mapping[str, Any],
    *,
    language: str | None = None,
    model: str | None = None,
    client: CliProxyClient | None = None,
    raise_on_error: bool = False,
) -> Translation:
    """Việt hoá 4 trường của một danh thiếp.

    `fields` là dict trường đã chuẩn hoá (khoá trùng tên cột `business_cards`). Trả về
    `Translation` với `meta["source"]` cho biết kết quả đến từ đâu: `llm` · `mixed` (model thiếu
    trường, bảng tra cứu lấp) · `dictionary` (model hỏng hẳn) · `skipped` (không có gì để dịch).

    Mặc định **không ném lỗi**: luồng quét gọi hàm này và một lượt dịch hỏng không được phép làm
    hỏng lượt quét. `raise_on_error=True` dành cho nút *Dịch lại* — ở đó im lặng mới là sai.
    """
    source = _source_fields(fields)
    if not source:
        return Translation(meta={"source": "skipped", "reason": "Thẻ không có trường nào để dịch."})

    started = time.perf_counter()
    values: dict[str, str | None] = dict(_offline_values(source))
    meta: dict[str, Any] = {"source": "dictionary"}

    if settings.translate_after_ocr:
        try:
            raw = await _ask_model(source, language=language, model=model, client=client)
        except Exception as exc:  # noqa: BLE001 — xem docstring: dịch hỏng ≠ quét hỏng
            logger.warning("Việt hoá bằng model thất bại, dùng bảng tra cứu: %s", exc)
            if raise_on_error:
                # Ném lại **nguyên loại lỗi cũ**, không bọc: router phân biệt
                # `LLMNotConnectedError` (503, mời bấm nút kết nối OAuth) với lỗi gọi model khác
                # (502). Bọc hết vào `TranslationError` là xoá mất chính chỗ phân biệt đó.
                raise
            meta["error"] = str(exc)[:300]
            if not values:
                meta["source"] = "failed"
        else:
            model_values = {
                column: _clean(raw.get(column))
                for name, column in prompts.VI_COLUMNS.items()
                if name in source
            }
            filled = {key: value for key, value in model_values.items() if value}
            meta["source"] = "llm" if len(filled) >= len(values) else "mixed"
            meta["model"] = settings.llm_model
            values.update(filled)
            for key in ("source_language", "script", "name_method", "note"):
                if cleaned := _clean(raw.get(key)):
                    meta[key] = cleaned

    meta["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    meta["stale"] = False
    return Translation(values=_finalize(values, source), meta=meta)


def translate_offline(fields: Mapping[str, Any]) -> Translation:
    """Chỉ bảng tra cứu, không gọi mạng. Dùng trong test và khi CLIProxy chưa kết nối."""
    source = _source_fields(fields)
    if not source:
        return Translation(meta={"source": "skipped", "reason": "Thẻ không có trường nào để dịch."})
    return Translation(
        values=_finalize(_offline_values(source), source),
        meta={"source": "dictionary", "stale": False},
    )


def split_legal_form(name: str) -> tuple[str | None, str]:
    """`"東京テック株式会社"` → `("Công ty Cổ phần", "東京テック")`.

    Trả `(None, tên nguyên vẹn)` khi không nhận ra loại hình nào — tuyệt đối không đoán. Chỉ bóc
    **một** loại hình: "FPT Software Co., Ltd." có đúng một, còn thứ trông như loại hình thứ hai
    thường là một phần của tên riêng.
    """
    text = squash_spaces(name) or ""
    if not text:
        return None, ""

    for raw_form, vi_form, position in LEGAL_FORMS:
        if _HAS_LATIN_RE.search(raw_form):
            stripped = _strip_latin_form(text, raw_form, position)
        else:
            stripped = _strip_cjk_form(text, raw_form, position)
        if stripped is not None and stripped.strip():
            return vi_form, squash_spaces(stripped) or text
    return None, text


# --------------------------------------------------------------------------- nội bộ


def _source_fields(fields: Mapping[str, Any]) -> dict[str, str]:
    """Đúng các trường dịch được và có nội dung, đã dọn khoảng trắng."""
    out: dict[str, str] = {}
    for name in prompts.TRANSLATABLE_FIELDS:
        value = squash_spaces(fields.get(name))
        if value:
            out[name] = value
    return out


async def _ask_model(
    source: Mapping[str, Any],
    *,
    language: str | None,
    model: str | None,
    client: CliProxyClient | None,
) -> dict[str, Any]:
    """Một lượt gọi model, trả về JSON đã bóc khỏi câu trả lời.

    `temperature=0.0` vì đây là việc chuyển đổi có đáp án chứ không phải viết sáng tạo: cùng một
    tấm thẻ mở hai lần phải ra cùng một cách phiên âm, nếu không người dùng sẽ thấy tên mình đổi
    mỗi lần bấm *Dịch lại*.
    """
    text = await llm.generate_text(
        prompts.build_prompt(dict(source), language=language),
        system=prompts.SYSTEM_PROMPT,
        model=model,
        temperature=0.0,
        client=client,
    )
    try:
        return extract_json_object(text)
    except JsonExtractError as exc:
        raise TranslationError(f"Model trả lời không phải JSON: {text[:200]}") from exc


def _offline_values(source: Mapping[str, str]) -> dict[str, str | None]:
    """Phần dịch được **không cần model**: chức vụ có trong bảng, loại hình pháp nhân của công ty.

    Tên người và địa chỉ không có ở đây: phiên âm tên riêng là việc chỉ model làm được.
    """
    out: dict[str, str | None] = {}

    title = source.get("job_title")
    if title and (vi_title := JOB_TITLES.get(_title_key(title))):
        out["job_title_vi"] = vi_title

    company = source.get("company_name_raw")
    if company:
        vi_form, remainder = split_legal_form(company)
        # Chỉ ghép được khi phần tên riêng đã là chữ Latin. Phần chữ Hán/Kana còn lại mà đem ghép
        # với "Công ty Cổ phần" thì ra một chuỗi nửa Việt nửa Nhật — tệ hơn là để trống cho model.
        if vi_form and _is_latin(remainder):
            out["company_name_vi"] = f"{vi_form} {remainder}"

    return out


def _finalize(values: Mapping[str, str | None], source: Mapping[str, str]) -> dict[str, str | None]:
    """Dọn giá trị cuối: cắt độ dài, và **bỏ bản dịch trùng y hệt bản gốc**.

    Bỏ bản trùng là quy tắc quan trọng nhất ở đây: thẻ tiếng Việt, hay thẻ tiếng Anh viết bằng
    chữ Latin, thì bản "Việt hoá" chính là bản gốc — giao diện in cả hai sẽ thành hai dòng chữ
    giống hệt nhau chồng lên nhau. `None` ở cột `*_vi` mang đúng một nghĩa: *bản gốc dùng được
    luôn, không cần chú thích gì thêm*.
    """
    out: dict[str, str | None] = {}
    for name, column in prompts.VI_COLUMNS.items():
        value = _clean(values.get(column))
        original = source.get(name)
        if value and original and _same_text(value, original):
            value = None
        if value and column != "address_vi" and len(value) > _MAX_LEN:
            logger.info("Việt hoá %s dài %d ký tự, cắt còn %d", column, len(value), _MAX_LEN)
            value = value[:_MAX_LEN].rstrip()
        out[column] = value
    return out


def _clean(value: Any) -> str | None:
    """Giá trị model trả về → chuỗi đã dọn, hoặc `None`. Chuỗi rỗng và `"null"` đều thành `None`."""
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    text = squash_spaces(str(value))
    if not text or text.casefold() in {"null", "none", "n/a", "không có"}:
        return None
    return text


def _same_text(left: str, right: str) -> bool:
    """So hai chuỗi bỏ qua hoa thường và khoảng trắng — đủ để bắt bản dịch chép y nguyên bản gốc."""
    return re.sub(r"\s+", "", left).casefold() == re.sub(r"\s+", "", right).casefold()


def _title_key(title: str) -> str:
    """Khoá tra `JOB_TITLES`: bỏ dấu chấm, gộp khoảng trắng, hạ chữ thường."""
    return re.sub(r"\s+", " ", title.replace(".", " ")).strip().casefold()


def _is_latin(text: str) -> bool:
    """Chuỗi chỉ gồm chữ Latin (kể cả chữ có dấu), số và dấu câu? Chữ CJK/Cyrillic/Thái → False."""
    return not any(ord(char) > 0x036F for char in text)


def _strip_cjk_form(text: str, form: str, position: str) -> str | None:
    """Bóc loại hình dính liền (không khoảng trắng) ở đầu hoặc cuối tên chữ Hán/Kana/Hangul."""
    if position in ("p", "b") and text.startswith(form):
        return text[len(form) :]
    if position in ("s", "b") and text.endswith(form):
        return text[: -len(form)]
    return None


def _strip_latin_form(text: str, form: str, position: str) -> str | None:
    """Bóc loại hình viết bằng chữ Latin — khớp nguyên từ, đúng vị trí cho phép.

    Hai chỗ dễ sai, cả hai đã gặp thật ở `normalize_company.py` của T (task 3.7/10.7): không
    khớp nguyên từ thì `Ltd` khớp vào giữa `Altdorf`; không tôn trọng vị trí thì `Group` trong
    `Group Dynamics Institute` bị bóc mất. Dấu chấm/phẩy trong `Co., Ltd.` là tuỳ hứng của người
    in thẻ nên so khớp lỏng ở đúng chỗ đó, không so từng ký tự.
    """
    parts = [re.escape(part) for part in form.replace(".", " ").split() if part]
    if not parts:
        return None
    pattern = r"[.,\s]*".join(parts)
    if position in ("p", "b"):
        head = re.match(rf"^{pattern}\b[.,\s]*", text, re.IGNORECASE)
        if head:
            return text[head.end() :]
    if position in ("s", "b"):
        tail = re.search(rf"[,\s]+{pattern}\.?$", text, re.IGNORECASE)
        if tail:
            return text[: tail.start()]
    return None
