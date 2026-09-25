"""So hai bản hồ sơ doanh nghiệp và chỉ giữ lại phần thật sự khác.

Chủ sở hữu: T | Task: NEXT-06 | xem Task.md

Hai luật ở đây quyết định tính năng *làm mới hồ sơ* có dùng được hay không:

1. **Không so `description` và `sources`.** Đó là văn xuôi do model sinh; chạy lại lần nào cũng
   ra chữ khác, nên đưa vào thì **mọi** lượt làm mới đều báo "có thay đổi" và người dùng thôi
   đọc từ lần thứ hai. Chỉ so 11 trường có nguồn của `SOURCED_FIELDS` — những thứ đúng hoặc sai
   chứ không hay hoặc dở.
2. **Lượt làm mới không được xoá trắng dữ liệu đang có.** Lần tra sau có thể không tìm ra mã số
   thuế mà lần trước tìm được; ghi đè `NULL` lên nó là mất một dữ kiện đúng chỉ vì hôm nay
   Internet trả lời khác. `merge_keeping_known()` giữ giá trị cũ, và `ProfileDiff.missing` nói
   riêng ra rằng lần này không tra lại được.

So chuỗi thì **bỏ qua hoa thường và khoảng trắng thừa**: `"Công ty Cổ phần FPT"` và
`"CÔNG TY CỔ PHẦN FPT"` là cùng một cái tên, báo nó là thay đổi chỉ tổ nhiễu.
"""

import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.schemas.company import SOURCED_FIELDS, CompanyProfileSchema

#: Trường mà một thay đổi đáng để người dùng dừng lại xem. Mã số thuế đổi nghĩa là **pháp nhân
#: đổi** — sáp nhập, tách công ty, hoặc hồ sơ cũ gắn nhầm doanh nghiệp; tên pháp lý và địa chỉ
#: thì đi kèm với chuyện đó. Mấy trường còn lại đổi là chuyện thường của một công ty đang sống.
NOTABLE_FIELDS: frozenset[str] = frozenset({"tax_code", "legal_name", "address", "website"})

#: Trường dạng danh sách — so theo **tập hợp**, vì thứ tự do model trả về không mang nghĩa gì.
LIST_FIELDS: frozenset[str] = frozenset({"industry", "products"})


def is_empty(value: Any) -> bool:
    return value is None or value == "" or value == []


def _key(value: Any) -> Any:
    """Dạng đem đi so sánh: chuỗi thì bỏ hoa thường và gom khoảng trắng, danh sách thì thành tập."""
    if isinstance(value, str):
        return " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    if isinstance(value, list):
        return frozenset(_key(item) for item in value)
    return value


@dataclass(frozen=True)
class FieldChange:
    field: str
    old: Any
    new: Any


@dataclass(frozen=True)
class ProfileDiff:
    """Kết quả so một lượt làm mới với bản hồ sơ đang có."""

    changes: list[FieldChange] = field(default_factory=list)
    #: Trường lần trước có giá trị mà lần này tra không ra. **Không phải thay đổi** — giá trị cũ
    #: được giữ nguyên; đây chỉ là lời nhắc rằng nguồn hôm nay không xác nhận lại được.
    missing: list[str] = field(default_factory=list)

    @property
    def notable(self) -> bool:
        return any(change.field in NOTABLE_FIELDS for change in self.changes)

    def is_empty(self) -> bool:
        return not self.changes and not self.missing

    def as_json(self) -> dict[str, Any]:
        return {
            "changes": {
                change.field: {"old": change.old, "new": change.new} for change in self.changes
            },
            "missing": list(self.missing),
        }


def diff_profiles(old: CompanyProfileSchema, new: CompanyProfileSchema) -> ProfileDiff:
    """So bản vừa tra được với bản đang lưu. Chỉ xét 11 trường có nguồn."""
    changes: list[FieldChange] = []
    missing: list[str] = []

    for name in SOURCED_FIELDS:
        before = getattr(old, name)
        after = getattr(new, name)

        if is_empty(after):
            if not is_empty(before):
                missing.append(name)
            continue
        if is_empty(before):
            changes.append(FieldChange(field=name, old=None, new=after))
            continue
        if _key(before) != _key(after):
            changes.append(FieldChange(field=name, old=before, new=after))

    return ProfileDiff(changes=changes, missing=missing)


def merge_keeping_known(
    old: CompanyProfileSchema, new: CompanyProfileSchema
) -> CompanyProfileSchema:
    """Bản sẽ ghi xuống: giá trị mới thắng ở đâu tra được, còn lại **giữ nguyên bản cũ**.

    Không có đường nào để một lượt làm mới biến `tax_code` đang có thành `NULL`. Mất một dữ kiện
    đúng chỉ vì hôm nay Internet trả lời khác là cái giá quá đắt cho việc chạy lại.

    `description` và `sources` thì **lấy trọn bản mới** khi lượt tra có kết quả: chúng là ảnh
    chụp của đúng lần tra ấy, trộn nửa cũ nửa mới ra một thứ không lần tra nào từng nói.
    """
    merged = new.model_copy(deep=True)
    for name in SOURCED_FIELDS:
        if is_empty(getattr(new, name)) and not is_empty(getattr(old, name)):
            setattr(merged, name, getattr(old, name))
    if is_empty(merged.description) and not is_empty(old.description):
        merged.description = old.description
    return merged
