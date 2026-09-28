"""Bóc một object JSON ra khỏi chuỗi chữ do model trả về.

Chủ sở hữu: Q | Task: EX-02

Tách khỏi `services/ocr.py` vì cả lượt quét lẫn lượt Việt hoá đều vấp đúng một chỗ: **I-15, model
bọc kết quả trong khối ```json dù prompt cấm**.

Ném `JsonExtractError` chứ không ném lỗi của F1 — chỗ gọi tự dịch sang lỗi của mình để thông báo
vẫn nói đúng việc vừa hỏng là quét ảnh hay Việt hoá.
"""

from __future__ import annotations

import json
import re
from typing import Any

#: Hàng rào code ```json … ``` (I-15).
_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_+-]*\s*\n?(?P<body>.*?)\n?\s*```\s*$", re.DOTALL)

#: Dấu phẩy thừa trước `}` hoặc `]` — lỗi cú pháp JSON duy nhất mà model hay mắc và sửa được
#: an toàn. Chỉ dùng ở bước cuối, sau khi đã cắt đúng phạm vi object.
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


class JsonExtractError(ValueError):
    """Không bóc được object JSON nào ra khỏi câu trả lời của model."""


def extract_json_object(text: str) -> dict[str, Any]:
    """Chuỗi model trả về → dict. Gỡ hàng rào code, cắt phần thừa, vá dấu phẩy thừa.

    Bốn lần thử, dừng ngay khi thành công:

    1. nguyên văn — trường hợp model ngoan;
    2. bỏ hàng rào ```…``` (I-15) — trường hợp gặp thật ở task 2.3;
    3. quét lấy object `{…}` cân bằng ngoặc đầu tiên — bắt được cả lời dẫn kiểu "Đây là JSON:";
    4. bỏ dấu phẩy thừa trước `}`/`]`.
    """
    candidates: list[str] = []
    stripped = text.strip()
    if stripped:
        candidates.append(stripped)

    fenced = _FENCE_RE.match(stripped)
    if fenced:
        candidates.append(fenced.group("body").strip())

    span = first_json_object(candidates[-1] if candidates else stripped)
    if span:
        candidates.append(span)
        candidates.append(_TRAILING_COMMA_RE.sub(r"\1", span))

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            return data
        # Model đôi khi gói kết quả trong mảng một phần tử.
        if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
            return data[0]

    raise JsonExtractError("Câu trả lời của model không chứa object JSON nào đọc được.")


def first_json_object(text: str) -> str | None:
    """Cắt object JSON cân bằng ngoặc đầu tiên trong chuỗi.

    Đếm ngoặc chứ không dùng regex: giá trị trong danh thiếp có thể chứa `{`/`}` (địa chỉ, tên
    công ty), và chuỗi JSON có thể chứa dấu nháy đã escape — regex không xử lý nổi hai thứ đó.
    """
    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None
