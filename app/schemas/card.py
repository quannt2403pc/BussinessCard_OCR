"""Schema danh thiếp + kết quả OCR.

Chủ sở hữu: Q | Task: 3.4 | xem Task.md

`CardExtraction` là **cửa khẩu duy nhất** cho dữ liệu do model sinh ra: mọi thứ đi từ
`services/ocr.py` vào DB đều phải qua đây. Vì nguồn dữ liệu là một model ngôn ngữ chứ không
phải một API có hợp đồng, validator ở đây làm việc "dọn dẹp" nhiều hơn là "kiểm tra":

* model đổi tên khoá theo hứng (`name` thay `full_name`, `company` thay `company_name_raw`) —
  `_ALIASES` kéo về đúng khoá thay vì để rớt trường;
* model điền `"N/A"`, `"không có"`, `""` thay vì `null` — quy về `None`, nếu không DB sẽ đầy
  những chuỗi rác mà mọi truy vấn đều phải lọc tay;
* model trả `confidence` là chuỗi `"0.9"`, hoặc số 95 (thang 100) — quy về float trong [0, 1].

Nguyên tắc: **dọn dẹp thì làm, vứt thì không.** Trường không hiểu được vẫn giữ nguyên chuỗi để
người dùng sửa ở màn hình review; bản JSON gốc chưa đụng vào nằm ở `ocr_raw_json` (task 3.5).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.prompts.ocr import CONTENT_FIELDS, REQUIRED_FIELDS

#: Tên khoá model hay dùng thay cho tên trong schema. Ánh xạ về khoá thật thay vì bỏ qua —
#: một trường rớt ở đây là một trường người dùng phải gõ lại bằng tay.
_ALIASES: dict[str, str] = {
    "name": "full_name",
    "fullname": "full_name",
    "person_name": "full_name",
    "title": "job_title",
    "position": "job_title",
    "role": "job_title",
    "company": "company_name_raw",
    "company_name": "company_name_raw",
    "organization": "company_name_raw",
    "e-mail": "email",
    "mail": "email",
    "tel": "phone",
    "telephone": "phone",
    "phone_number": "phone",
    "mobile": "phone_alt",
    "phone2": "phone_alt",
    "phone_secondary": "phone_alt",
    "url": "website",
    "web": "website",
    "language": "language_detected",
    "lang": "language_detected",
}

#: Chuỗi model dùng để nói "không có" — tất cả phải thành `None`. So khớp **đúng cả chuỗi** sau
#: khi hạ chữ thường: "NA" là rác, nhưng "Na" có thể là tên người Hàn.
_EMPTY_MARKERS = frozenset(
    {"", "-", "--", "n/a", "na", "none", "null", "unknown", "không có", "khong co", "không rõ"}
)

#: Model hay trả tên ngôn ngữ bằng chữ thay vì mã ISO 639-1.
_LANGUAGE_ALIASES: dict[str, str] = {
    "english": "en",
    "vietnamese": "vi",
    "tiếng việt": "vi",
    "korean": "ko",
    "japanese": "ja",
    "chinese": "zh",
    "zh-cn": "zh",
    "zh-hans": "zh",
    "zh-hant": "zh-tw",
}


class CardExtraction(BaseModel):
    """Kết quả model đọc được từ một ảnh danh thiếp — khoá trùng cột `business_cards`."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    full_name: str | None = None
    job_title: str | None = None
    company_name_raw: str | None = None
    email: str | None = None
    phone: str | None = None
    phone_alt: str | None = None
    address: str | None = None
    website: str | None = None
    language_detected: str | None = None
    #: Ảnh không phải danh thiếp thì model để `false` (quy tắc 6 của prompt) — router ghi chú
    #: lại trong `notes` để người dùng biết vì sao mọi trường đều trống (task 10.3).
    is_business_card: bool = True
    confidence: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _tidy_input(cls, data: Any) -> Any:
        """Kéo khoá lạ về khoá thật và quy mọi cách viết "không có" thành `None`."""
        if not isinstance(data, dict):
            return data

        out: dict[str, Any] = {}
        aliased: dict[str, Any] = {}
        for key, value in data.items():
            name = str(key).strip().lower()
            cleaned = value if name == "confidence" else _clean_scalar(value)
            if name in cls.model_fields:
                out[name] = cleaned
            elif (target := _ALIASES.get(name)) and aliased.get(target) is None:
                aliased[target] = cleaned

        # Khoá thật luôn thắng khoá alias, bất kể thứ tự model trả về: thẻ có cả `name` lẫn
        # `full_name` thì `full_name` mới là cái theo đúng schema đã yêu cầu.
        for name, value in aliased.items():
            if out.get(name) is None:
                out[name] = value
        return out

    @field_validator("language_detected", mode="after")
    @classmethod
    def _normalize_language(cls, value: str | None) -> str | None:
        """Về mã ISO 639-1 chữ thường. `services/normalize.py` tra mã vùng SĐT bằng trường này."""
        if value is None:
            return None
        key = value.strip().lower().replace("_", "-")
        return _LANGUAGE_ALIASES.get(key, key)[:16] or None

    @field_validator("confidence", mode="before")
    @classmethod
    def _clean_confidence(cls, value: Any) -> dict[str, float]:
        """Giữ lại điểm của đúng các trường nội dung, ép về float trong [0, 1].

        Model có lúc chấm theo thang 100 (`95`) — chia 100 thay vì kẹp về 1.0, nếu không cả thẻ
        đều 1.0 và màn hình review mất hẳn khả năng chỉ chỗ cần kiểm.
        """
        if not isinstance(value, dict):
            return {}

        out: dict[str, float] = {}
        for key, raw in value.items():
            name = _ALIASES.get(str(key).strip().lower(), str(key).strip().lower())
            if name not in CONTENT_FIELDS:
                continue
            try:
                score = float(raw)
            except (TypeError, ValueError):
                continue
            if score > 1.0:
                score = score / 100.0 if score <= 100.0 else 1.0
            out[name] = min(max(score, 0.0), 1.0)
        return out

    def missing_required(self) -> list[str]:
        """Trường bắt buộc (tiêu chí A3) mà model không đọc được. Dùng để đo ở task 7.8."""
        return [name for name in REQUIRED_FIELDS if getattr(self, name, None) is None]

    def card_columns(self) -> dict[str, Any]:
        """Phần map thẳng vào cột của `business_cards` — `is_business_card` không phải là cột."""
        return self.model_dump(exclude={"is_business_card"})


class CardOut(BaseModel):
    """Một danh thiếp trả về cho UI/API. Không kèm `ocr_raw_json` cho nhẹ (task 4.2 mới cần)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    full_name: str | None = None
    job_title: str | None = None
    company_name_raw: str | None = None
    email: str | None = None
    phone: str | None = None
    phone_alt: str | None = None
    address: str | None = None
    website: str | None = None
    language_detected: str | None = None
    confidence: dict[str, float] | None = None
    company_id: uuid.UUID | None = None
    notes: str | None = None
    image_path: str
    uploaded_at: datetime


class CardUploadOut(BaseModel):
    """Kết quả `POST /api/cards/upload` (task 3.1).

    `duplicate=true` nghĩa là ảnh này đã được quét trước đó: trả về đúng bản ghi cũ, **không**
    gọi lại model. Đây là kết quả hợp lệ chứ không phải lỗi — tiêu chí hoàn thành D3 yêu cầu
    upload lại cùng một ảnh thì không sinh bản ghi trùng.
    """

    card: CardOut
    duplicate: bool = False
    #: Thiếu trường bắt buộc nào — UI dùng để nhắc người dùng điền nốt ở màn hình review.
    missing_required: list[str] = Field(default_factory=list)
    #: Lý do quét hỏng, khi đó `card.status = pending` và ảnh vẫn được lưu để quét lại.
    #: Upload vẫn trả 201: lưu được ảnh là thành công, quét hỏng là một kết quả cần hiển thị.
    ocr_error: str | None = None
    #: `null` khi trùng ảnh (không gọi model). Đo riêng phần gọi LLM, xem `notes` của task 3.5.
    ocr_ms: int | None = None
    elapsed_ms: int


def _clean_scalar(value: Any) -> Any:
    """Chuẩn hoá một giá trị vô hướng do model trả về.

    Xử lý luôn trường hợp model trả **mảng** cho trường đơn (`"phone": ["024…", "090…"]`) —
    nối lại bằng dấu `/` để `normalize.split_phones()` tách đúng, thay vì lấy phần tử đầu và
    im lặng làm mất số thứ hai.
    """
    if isinstance(value, (list, tuple)):
        cleaned = (_clean_scalar(item) for item in value)
        return " / ".join(str(item) for item in cleaned if item is not None) or None
    if not isinstance(value, str):
        return value
    text = value.strip()
    return None if text.lower() in _EMPTY_MARKERS else text
