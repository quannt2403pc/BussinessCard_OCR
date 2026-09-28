"""Schema danh thiếp + kết quả OCR.

Chủ sở hữu: Q | Task: 3.4

`CardExtraction` là **cửa khẩu duy nhất** cho dữ liệu do model sinh ra. Vì nguồn là một model
ngôn ngữ chứ không phải API có hợp đồng, validator ở đây dọn dẹp nhiều hơn là kiểm tra: kéo khoá
alias về đúng tên, quy `"N/A"` / `"không có"` về `None`, ép `confidence` về float trong [0, 1].

Nguyên tắc: **dọn dẹp thì làm, vứt thì không.** Bản JSON gốc chưa đụng vào nằm ở `ocr_raw_json`.
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

#: Model hay trả tên ngôn ngữ bằng chữ thay vì mã ISO 639-1. **Không phải danh sách ngôn ngữ
#: được hỗ trợ** — mã lạ vẫn đi thẳng vào DB; đây chỉ là bảng gom các cách viết của cùng một
#: ngôn ngữ để tra mã vùng số điện thoại và in nhãn tiếng Việt.
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
    # EX-05 — mở rộng ngoài 5 ngôn ngữ chính.
    "thai": "th",
    "russian": "ru",
    "arabic": "ar",
    "german": "de",
    "french": "fr",
    "spanish": "es",
    "portuguese": "pt",
    "italian": "it",
    "indonesian": "id",
    "malay": "ms",
    "filipino": "tl",
    "tagalog": "tl",
    "hindi": "hi",
    "khmer": "km",
    "lao": "lo",
    "burmese": "my",
    "dutch": "nl",
    "polish": "pl",
    "turkish": "tr",
    "hebrew": "he",
    "greek": "el",
    "czech": "cs",
    "swedish": "sv",
    "mandarin": "zh",
    "cantonese": "yue",
    "zh-tw": "zh-tw",
    "pt-br": "pt",
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


#: Cột người dùng được sửa tay ở màn hình review. `status` KHÔNG nằm ở đây — vòng đời bản ghi
#: đổi qua `POST /{id}/confirm`, để lẫn vào thì một lần PATCH lỡ tay đẩy thẳng sang `confirmed`.
EDITABLE_FIELDS: tuple[str, ...] = (
    "full_name",
    "job_title",
    "company_name_raw",
    "email",
    "phone",
    "phone_alt",
    "address",
    "website",
    "language_detected",
    "notes",
    # Bản Việt hoá (EX-04) — xem ghi chú ở `CardUpdateIn`.
    "full_name_vi",
    "job_title_vi",
    "company_name_vi",
    "address_vi",
)


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
    # Việt hoá sau khi quét (EX-02). `None` = bản gốc dùng được luôn, KHÔNG phải "chưa dịch" —
    # giao diện chỉ hiện dòng chú thích khi hai bản thật sự khác nhau.
    full_name_vi: str | None = None
    job_title_vi: str | None = None
    company_name_vi: str | None = None
    address_vi: str | None = None
    translation_meta: dict[str, Any] | None = None
    company_id: uuid.UUID | None = None
    notes: str | None = None
    #: `None` được phép nhưng không đường nào sinh ra nữa — xem `models/card.py`.
    image_path: str | None = None
    uploaded_at: datetime


class CardDetailOut(CardOut):
    """Chi tiết một danh thiếp — thêm bản JSON gốc và mốc thời gian.

    `ocr_raw_json` chỉ có ở đây chứ không có trong danh sách: nó là thứ duy nhất đối chiếu được
    khi nghi OCR sai, mà nhét vào danh sách 50 bản ghi thì payload phình vô ích.
    """

    image_hash: str
    ocr_raw_json: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class CardListOut(BaseModel):
    """Một trang danh sách danh thiếp (task 4.1)."""

    items: list[CardOut]
    total: int
    page: int
    size: int
    pages: int


class CardUpdateIn(BaseModel):
    """Body của `PATCH /api/cards/{id}` — sửa tay sau khi review.

    Mọi trường đều tuỳ chọn và **phân biệt "không gửi" với "gửi null"**: không gửi thì giữ
    nguyên, gửi `null` là chủ ý xoá trắng. Gộp hai ca lại thì không xoá được giá trị model đọc sai.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    full_name: str | None = None
    job_title: str | None = None
    company_name_raw: str | None = None
    email: str | None = None
    phone: str | None = None
    phone_alt: str | None = None
    address: str | None = None
    website: str | None = None
    language_detected: str | None = None
    notes: str | None = None
    # Bản Việt hoá cũng sửa tay được. Sửa tay thì `translation_meta` được đánh dấu
    # `source="manual"` để lần *Dịch lại* sau không lặng lẽ ghi đè.
    full_name_vi: str | None = None
    job_title_vi: str | None = None
    company_name_vi: str | None = None
    address_vi: str | None = None

    def changes(self) -> dict[str, Any]:
        """Đúng những trường client gửi lên (kể cả khi giá trị là `null`)."""
        return self.model_dump(exclude_unset=True)


class CardConfirmOut(BaseModel):
    """Kết quả `POST /api/cards/{id}/confirm`.

    `company_matched=false` và `kb_indexed=false` **không phải lỗi**: thẻ vẫn `confirmed`, chỉ là
    chưa gắn được công ty / chưa vào được Knowledge Base. `detail` nói rõ lý do.
    """

    id: uuid.UUID
    status: str
    company_id: uuid.UUID | None = None
    company_matched: bool = False
    kb_indexed: bool = False
    detail: str | None = None


class CardUploadOut(BaseModel):
    """Kết quả `POST /api/cards/upload`.

    `duplicate=true` nghĩa là ảnh đã được quét trước đó: trả về bản ghi cũ, **không** gọi lại
    model. Đây là kết quả hợp lệ chứ không phải lỗi.
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


class BatchUploadOut(BaseModel):
    """Kết quả `POST /api/cards/batch-upload` — trả **202**, việc quét chạy ở nền.

    Ba con số vì ba việc phải làm tiếp khác nhau: `queued` sẽ có kết quả nếu chờ, `duplicates`
    đã có sẵn trong DB, `rejected` phải xử lý bằng tay.
    """

    job_id: uuid.UUID
    total: int
    #: Sẽ được gọi vision ở nền.
    queued: int
    #: Ảnh đã quét từ trước — trả lại bản ghi cũ, không gọi model.
    duplicates: int
    #: Không nhận được ngay từ đầu (quá dung lượng, không phải ảnh, file rỗng).
    rejected: int


class BatchItemOut(BaseModel):
    """Tiến trình của một ảnh trong job (task 5.2).

    Cố ý **không** trả đường dẫn ảnh trong volume: đó là đường dẫn nội bộ của container, UI chỉ
    cần `card_id` để dựng link `/cards/{id}`.
    """

    filename: str
    #: `pending` | `running` | `done` | `error`
    status: str
    card_id: uuid.UUID | None = None
    duplicate: bool = False
    error: str | None = None
    #: Số lượt đã gọi model. > 1 nghĩa là đã phải retry — dấu hiệu sớm của rate limit (R5).
    attempts: int = 0
    ocr_ms: int | None = None


class BatchJobOut(BaseModel):
    """Trạng thái một job batch — `GET /api/cards/batch-jobs/{job_id}`.

    `finished` là cờ riêng chứ không suy ra từ `done + failed == total`: job bị huỷ sớm cũng cho
    hai con số bằng nhau, mà UI cần biết lúc nào ngừng poll.
    """

    job_id: uuid.UUID
    total: int
    done: int
    failed: int
    running: int
    finished: bool
    #: Lý do cả lượt bị dừng sớm, `null` nếu chạy bình thường.
    aborted_reason: str | None = None
    items: list[BatchItemOut]


def _clean_scalar(value: Any) -> Any:
    """Chuẩn hoá một giá trị vô hướng do model trả về.

    Model trả **mảng** cho trường đơn (`"phone": ["024…", "090…"]`) thì nối lại bằng `/` để
    `normalize.split_phones()` tách đúng, thay vì lấy phần tử đầu và làm mất số thứ hai.
    """
    if isinstance(value, (list, tuple)):
        cleaned = (_clean_scalar(item) for item in value)
        return " / ".join(str(item) for item in cleaned if item is not None) or None
    if not isinstance(value, str):
        return value
    text = value.strip()
    return None if text.lower() in _EMPTY_MARKERS else text
