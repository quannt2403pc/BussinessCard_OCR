"""Hậu xử lý sau khi quét: chuẩn hoá SĐT (E.164), email, khoảng trắng.

Chủ sở hữu: Q | Task: 3.6 | xem Task.md

Gọi ngay trong `services/ocr.py` sau khi parse JSON của model — đây là bước "tối ưu dữ liệu sau
khi quét" thuộc F1 (Plan.md mục 5.2), không phải một endpoint riêng.

**Nguyên tắc xuyên suốt file: không vứt dữ liệu.** Danh thiếp đi vào trạng thái `needs_review`,
người dùng sẽ nhìn lại từng trường (rủi ro R3). Chuẩn hoá được thì trả bản chuẩn; không chuẩn
hoá được thì trả **bản đã dọn sạch** chứ không trả `None` — giá trị gốc y nguyên vẫn nằm trong
`ocr_raw_json` để đối chiếu.

Vì sao dùng `phonenumbers` (bản port của libphonenumber) thay vì tự viết regex: danh thiếp của
dự án có 5 ngôn ngữ Anh/Việt/Hàn/Nhật/Trung (Plan.md mục 1.3), mỗi nước một quy tắc mã vùng và
số 0 đứng đầu. `+84 24 7300 7300`, `02-1234-5678`, `090-1234-5678` không có mẫu chung nào bắt
được bằng regex mà không sai.

Cập nhật EX-05 (2026-09-22): phạm vi ngôn ngữ mở ra **không giới hạn**, nên `REGION_BY_LANGUAGE`
dài thêm. Chọn thư viện thay vì regex nay còn đáng giá hơn: quy tắc số của Thái Lan hay Ba Lan
không phải thứ viết tay được, mà `phonenumbers` thì đã có sẵn cả 200+ nước.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

import phonenumbers

logger = logging.getLogger(__name__)

#: Vùng mặc định để đọc số viết theo kiểu nội địa (`0912…`) — suy từ `language_detected` mà
#: prompt OCR trả về (task 3.3). Không đoán được vùng thì `phonenumbers` chỉ đọc được số đã có
#: dấu `+`, phần còn lại giữ nguyên dạng đã dọn.
#:
#: EX-05 mở rộng ra ngoài 5 ngôn ngữ chính. Chỉ điền những ngôn ngữ **gắn chặt với một nước**:
#: tiếng Thái → Thái Lan, tiếng Ba Lan → Ba Lan. Ngôn ngữ nói ở nhiều nước (Anh, Tây Ban Nha,
#: Ả Rập, Bồ Đào Nha, Pháp, Đức) cố ý để `None` — đoán bừa một nước còn tệ hơn không đoán: số
#: `030-1234567` đọc theo `DE` ra một số Berlin, đọc theo `AT` ra một số không tồn tại, và cả
#: hai đều được ghi vào DB trông như thật. Không có vùng thì `phonenumbers` chỉ đọc số có dấu
#: `+`, phần còn lại giữ nguyên dạng đã dọn — mất phần chuẩn hoá, không mất dữ liệu.
REGION_BY_LANGUAGE: dict[str, str | None] = {
    "vi": "VN",
    "ko": "KR",
    "ja": "JP",
    "zh": "CN",
    "zh-tw": "TW",
    "yue": "HK",
    "th": "TH",
    "km": "KH",
    "lo": "LA",
    "my": "MM",
    "id": "ID",
    "ms": "MY",
    "tl": "PH",
    "hi": "IN",
    "ru": "RU",
    "uk": "UA",
    "pl": "PL",
    "cs": "CZ",
    "tr": "TR",
    "he": "IL",
    "el": "GR",
    "it": "IT",
    "nl": "NL",
    "sv": "SE",
    "fi": "FI",
    "da": "DK",
    "no": "NO",
    "hu": "HU",
    "ro": "RO",
    "en": None,  # tiếng Anh không gắn với nước nào — mặc định US sẽ đọc sai số Việt
    "es": None,
    "pt": None,
    "fr": None,
    "de": None,
    "ar": None,
}

#: Nhãn hay đứng trước số trên danh thiếp; model thường chép cả nhãn vào trường `phone`.
_PHONE_LABEL_RE = re.compile(
    r"(?:tel|phone|mobile|mob|cell|hp|direct|office|fax|dd|"
    r"đt|dt|sđt|sdt|điện\s*thoại|di\s*động|电话|手机|電話|전화|휴대폰)"
    r"\s*[.:：·]*\s*",
    re.IGNORECASE,
)

#: Tách nhiều số trong cùng một ô: "090-1234-5678 / 02-3456-7890", "…; …", hoặc xuống dòng.
_PHONE_SPLIT_RE = re.compile(r"[;,/|\n]+|\s+(?:hoặc|or)\s+", re.IGNORECASE)

#: Ký tự được phép giữ lại trong một số (`+`, ngoặc, dấu nối, và chữ cho phần máy lẻ).
_PHONE_KEEP_RE = re.compile(r"[^0-9+()\-.\sextEXT#]")

#: Ngắn hơn mức này thì không phải số điện thoại mà là mảnh vụn (máy lẻ viết rời, năm, mã bưu điện).
_MIN_PHONE_DIGITS = 7

#: Kiểm email ở mức "trông giống email" — cố ý lỏng. Địa chỉ sai luật vẫn được giữ để người dùng
#: sửa ở màn hình review; chặt tay ở đây chỉ làm mất dữ liệu.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

#: `mailto:` phải cắt TRƯỚC nhãn, nếu không `mail` của nhãn ăn mất bốn chữ đầu và còn lại `to:`.
_MAILTO_RE = re.compile(r"^mailto\s*:\s*", re.IGNORECASE)

_EMAIL_PREFIX_RE = re.compile(r"^(?:e-?mail|mail|thư điện tử)\s*[.:：]*\s*", re.IGNORECASE)

#: Rác hay dính vào hai đầu giá trị khi model chép nguyên một dòng trên danh thiếp.
_TRAILING_JUNK = " \t\r\n.,;:·|<>()[]\"'"


def squash_spaces(value: Any) -> str | None:
    """Gộp mọi loại khoảng trắng thành một dấu cách, cắt hai đầu. Rỗng ⇒ `None`.

    Dùng cho mọi trường văn bản (tên, chức vụ, công ty, địa chỉ). Địa chỉ trên danh thiếp hay
    xuống dòng giữa chừng; giữ nguyên xuống dòng thì so khớp tên công ty (task 3.8 của T) và
    tìm kiếm (task 4.1) đều vấp.
    """
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    # NBSP và khoảng trắng full-width lọt vào từ ảnh tiếng Nhật/Trung. `\s` của Python bắt được
    # NBSP nhưng không bắt U+3000, nên đổi tay trước.
    text = text.replace("　", " ").replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def normalize_email(value: Any) -> str | None:
    """Về chữ thường, bỏ `mailto:`/nhãn/ngoặc, vá lỗi OCR đọc dấu `.` thành `,` trong tên miền.

    Không chuẩn hoá được thì **vẫn trả chuỗi đã dọn** (xem đầu file).
    """
    text = squash_spaces(value)
    if text is None:
        return None

    # NFKC: danh thiếp Nhật/Trung hay dùng ký tự full-width `＠`, `．` — không chuẩn hoá thì địa
    # chỉ trông vẫn đúng trên màn hình nhưng không gửi được thư.
    text = unicodedata.normalize("NFKC", text)
    text = _EMAIL_PREFIX_RE.sub("", _MAILTO_RE.sub("", text))
    text = text.strip("<>").strip(_TRAILING_JUNK)
    text = text.replace(" ", "").lower()
    if not text:
        return None

    local, sep, domain = text.rpartition("@")
    if sep:
        # Dấu phẩy không hợp lệ trong tên miền ⇒ chắc chắn là OCR đọc nhầm dấu chấm.
        text = f"{local}@{domain.replace(',', '.')}"

    if not _EMAIL_RE.match(text):
        logger.debug("Email không đúng dạng, giữ nguyên để người dùng sửa: %r", text)
    return text


def normalize_website(value: Any) -> str | None:
    """Thêm `https://` khi thiếu, bỏ khoảng trắng, hạ chữ thường phần scheme và tên miền.

    Giữ nguyên hoa/thường của đường dẫn phía sau: `example.com/Partners` khác
    `example.com/partners` trên máy chủ phân biệt hoa thường.
    """
    text = squash_spaces(value)
    if text is None:
        return None

    text = unicodedata.normalize("NFKC", text).replace(" ", "").strip(_TRAILING_JUNK)
    if not text:
        return None

    scheme, sep, rest = text.partition("://")
    if sep:
        scheme = scheme.lower()
    else:
        scheme, rest = "https", text
    host, slash, path = rest.partition("/")
    return f"{scheme}://{host.lower()}{slash}{path}"


def region_for_language(language: str | None) -> str | None:
    """Mã vùng ISO cho `language_detected` của prompt OCR. Không biết ⇒ `None`, đừng đoán bừa."""
    if not language:
        return None
    key = language.strip().lower().replace("_", "-")
    if key in REGION_BY_LANGUAGE:
        return REGION_BY_LANGUAGE[key]
    return REGION_BY_LANGUAGE.get(key.split("-")[0])


def split_phones(value: Any) -> list[str]:
    """Tách một ô chứa nhiều số thành danh sách, bỏ mảnh quá ngắn để có thể là số.

    Mảnh dưới 7 chữ số thường là số máy lẻ viết rời ("… / 102") hoặc rác OCR — giữ lại sẽ sinh
    ra một `phone_alt` vô nghĩa.

    ⚠️ Xuống dòng phải đổi thành dấu tách **trước** khi gọi `squash_spaces()`. Phát hiện khi
    viết test 4.10: `squash_spaces` gộp `\\n` thành khoảng trắng, nên tới lượt `_PHONE_SPLIT_RE`
    thì không còn gì để tách — `"024 7300 7300\\n0912345678"` dính thành một chuỗi, `phone` lưu
    một số vô nghĩa và số thứ hai mất hẳn. Danh thiếp in hai số trên hai dòng là chuyện thường.
    """
    raw = "" if value is None else (value if isinstance(value, str) else str(value))
    text = squash_spaces(raw.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " / "))
    if text is None:
        return []

    text = unicodedata.normalize("NFKC", text)
    out: list[str] = []
    for chunk in _PHONE_SPLIT_RE.split(text):
        cleaned = _clean_phone_chunk(chunk)
        if cleaned and sum(ch.isdigit() for ch in cleaned) >= _MIN_PHONE_DIGITS:
            out.append(cleaned)
    return out


def normalize_phone(value: Any, *, region: str | None = None) -> str | None:
    """Về E.164 (`+84912345678`) khi đọc được; không đọc được thì trả bản đã dọn.

    Ba cách đọc, theo đúng thứ tự:

    1. số nội địa của `region` — bắt được `0912…`, `02-1234-5678`;
    2. chuỗi không có `+` nhưng đã mở đầu bằng mã nước thì thử lại với `+` — bắt được
       `84 24 7300 7300` và `(84) 24 …`, dạng rất hay gặp trên danh thiếp Việt;
    3. số quốc tế không kèm vùng — bắt được mọi số đã có `+`.

    Chỉ trả E.164 khi `is_valid_number()` đồng ý. Số "có thể đúng" nhưng không hợp lệ mà vẫn ép
    về E.164 thì màn hình review hiện một số trông rất chuẩn nhưng gọi không được — lỗi âm thầm,
    tệ hơn hẳn việc giữ đúng chữ người dùng nhìn thấy trên ảnh.
    """
    cleaned = _clean_phone_chunk(value)
    if not cleaned:
        return None

    for candidate, candidate_region in _phone_candidates(cleaned, region):
        try:
            parsed = phonenumbers.parse(candidate, candidate_region)
        except phonenumbers.NumberParseException:
            continue
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)

    logger.debug("Không đọc được số %r (vùng=%s), giữ nguyên bản đã dọn", cleaned, region)
    return cleaned


def normalize_card_fields(fields: dict[str, Any], *, language: str | None = None) -> dict[str, Any]:
    """Chuẩn hoá cả bộ trường của một danh thiếp — `ocr.py` gọi đúng hàm này (task 3.4).

    Trả về dict **mới**, không sửa tại chỗ: bản gốc còn phải ghi vào `ocr_raw_json` (task 3.5).
    Số thứ hai tìm thấy trong ô `phone` sẽ lấp vào `phone_alt` nếu ô đó còn trống — danh thiếp
    hay in "Tel / Mobile" trên cùng một dòng.
    """
    region = region_for_language(language)
    out = dict(fields)

    for key in ("full_name", "job_title", "company_name_raw", "address"):
        if key in out:
            out[key] = squash_spaces(out.get(key))

    if "email" in out:
        out["email"] = normalize_email(out.get("email"))
    if "website" in out:
        out["website"] = normalize_website(out.get("website"))

    numbers = _collect_phones(out.get("phone"))
    for item in _collect_phones(out.get("phone_alt")):
        if item not in numbers:
            numbers.append(item)

    normalized: list[str] = []
    for item in numbers:
        value = normalize_phone(item, region=region)
        if value and value not in normalized:
            normalized.append(value)

    out["phone"] = normalized[0] if normalized else None
    out["phone_alt"] = normalized[1] if len(normalized) > 1 else None
    if len(normalized) > 2:
        # Hiếm, nhưng có danh thiếp in 3 số. DB chỉ có 2 cột (Plan.md mục 3); phần dư vẫn còn đủ
        # trong `ocr_raw_json`, ghi log để biết mà nhìn lại chứ không âm thầm bỏ.
        logger.info("Danh thiếp có %d số điện thoại, chỉ lưu 2 số đầu", len(normalized))

    return out


# --------------------------------------------------------------------------- nội bộ


def _collect_phones(value: Any) -> list[str]:
    """Các số trong một ô. Ô quá ngắn để coi là số thì vẫn giữ lại nguyên bản đã dọn."""
    found = split_phones(value)
    if found:
        return found
    cleaned = _clean_phone_chunk(value)
    return [cleaned] if cleaned else []


def _clean_phone_chunk(value: Any) -> str | None:
    """Bỏ nhãn ("Tel:", "電話"), ký tự lạ và khoảng trắng thừa; giữ `+`, ngoặc, dấu nối, máy lẻ."""
    text = squash_spaces(value)
    if text is None:
        return None
    text = unicodedata.normalize("NFKC", text)
    text = _PHONE_LABEL_RE.sub("", text)
    text = _PHONE_KEEP_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" -.")
    return text or None


def _phone_candidates(cleaned: str, region: str | None) -> list[tuple[str, str | None]]:
    """Các cách đọc một chuỗi số, theo thứ tự ưu tiên (xem docstring `normalize_phone`)."""
    candidates: list[tuple[str, str | None]] = []
    if region:
        candidates.append((cleaned, region))
        code = str(phonenumbers.country_code_for_region(region))
        digits = "".join(ch for ch in cleaned if ch.isdigit())
        if "+" not in cleaned and code != "0" and digits.startswith(code):
            candidates.append((f"+{digits}", None))
    candidates.append((cleaned, None))
    return candidates
