"""Chuẩn hoá tên công ty thành khoá chống trùng `companies.name_normalized`.

Chủ sở hữu: T | Task: 3.7 | xem Task.md
"""

import unicodedata
from collections.abc import Iterable

_VI_ENTITIES = ("tổng công ty", "công ty", "cty", "tập đoàn")
_VI_LLC = ("trách nhiệm hữu hạn", "tnhh")
_VI_MEMBERS = (
    "một thành viên",
    "1 thành viên",
    "mtv",
    "hai thành viên trở lên",
    "2 thành viên trở lên",
)
_VI_LLC_FULL = (*_VI_LLC, *(f"{llc} {member}" for llc in _VI_LLC for member in _VI_MEMBERS))
_VI_KINDS = (*_VI_LLC_FULL, "cổ phần", "cp")
_VI_WITH_ENTITY = tuple(f"{entity} {kind}" for entity in _VI_ENTITIES for kind in _VI_KINDS)

# NFKC đổi ㈱ ㈲ ㈜ thành (株) (有) (주); bỏ ngoặc xong còn lại đúng một chữ đứng riêng.
_ENCLOSED = ("株", "有", "주")

# "cp", "mtv" không bỏ khi đứng đầu một mình: dễ cắt nhầm thương hiệu như "CP Group", "MTV Networks".
_RAW_PREFIXES = (
    *_VI_WITH_ENTITY,
    *_VI_ENTITIES,
    *_VI_LLC_FULL,
    "cổ phần",
    "ctcp",
    "doanh nghiệp tư nhân",
    "dntn",
    *_ENCLOSED,
)

# Dạng tiếng Anh chỉ bỏ ở cuối: đứng đầu thì trùng từ tiếng Việt đã bỏ dấu ("Cô Tô" -> "co to").
_RAW_SUFFIXES = (
    *_VI_WITH_ENTITY,
    *_VI_ENTITIES,
    *_VI_KINDS,
    "ctcp",
    "jsc",
    "joint stock company",
    "limited",
    "ltd",
    "company",
    "co",
    "corporation",
    "corp",
    "group",
    "incorporated",
    "inc",
    "llc",
    "plc",
    "pte",
    "sdn bhd",
    "bhd",
    *_ENCLOSED,
)

# Chữ Hán/Hangul thường viết liền không khoảng trắng nên khớp theo chuỗi, không theo từ.
_RAW_CJK_BOTH_ENDS = (
    "株式会社",
    "有限会社",
    "合同会社",
    "合資会社",
    "合名会社",
    "주식회사",
    "유한회사",
)
_RAW_CJK_SUFFIXES = ("股份有限公司", "有限责任公司", "有限責任公司", "有限公司", "股份公司")


def normalize_company_name(name: str) -> str:
    """Trả khoá chuẩn hoá của tên công ty; ném `ValueError` nếu tên không còn ký tự có nghĩa."""
    text = _normalize_text(name)
    if not text:
        raise ValueError(f"Tên công ty rỗng sau khi chuẩn hoá: {name!r}")
    # Tên chỉ gồm hình thức pháp lý (vd. "株式会社") thì giữ nguyên, không đưa chuỗi rỗng vào cột UNIQUE.
    return _strip_legal_forms(text) or text


def _normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    text = _strip_latin_diacritics(text)
    text = "".join(" " if unicodedata.category(char)[0] in "PS" else char for char in text)
    return " ".join(_join_initials(text.split()))


def _strip_latin_diacritics(text: str) -> str:
    # Chỉ bỏ dấu đi sau chữ Latin: bỏ mọi dấu kết hợp sẽ biến "ガス" thành "カス".
    kept = []
    base_is_latin = False
    for char in unicodedata.normalize("NFD", text):
        if not unicodedata.combining(char):
            base_is_latin = unicodedata.name(char, "").startswith("LATIN")
        elif base_is_latin:
            continue
        kept.append(char)
    # Ghép lại để chữ Hàn và chữ Nhật có dấu kêu không bị lưu ở dạng đã tách.
    return unicodedata.normalize("NFC", "".join(kept)).replace("đ", "d")


def _join_initials(tokens: list[str]) -> list[str]:
    # "A.B.C" thành "a b c" sau khi bỏ dấu câu; ghép lại để trùng key với "ABC".
    joined: list[str] = []
    run = ""
    for token in tokens:
        if len(token) == 1 and token.isascii() and token.isalnum():
            run += token
            continue
        if run:
            joined.append(run)
            run = ""
        joined.append(token)
    if run:
        joined.append(run)
    return joined


def _token_forms(raw: Iterable[str]) -> tuple[tuple[str, ...], ...]:
    forms = {tuple(_normalize_text(form).split()) for form in raw}
    return tuple(sorted(forms, key=len, reverse=True))


def _string_forms(raw: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({_normalize_text(form) for form in raw}, key=len, reverse=True))


_PREFIXES = _token_forms(_RAW_PREFIXES)
_SUFFIXES = _token_forms(_RAW_SUFFIXES)
_CJK_PREFIXES = _string_forms(_RAW_CJK_BOTH_ENDS)
_CJK_SUFFIXES = _string_forms((*_RAW_CJK_BOTH_ENDS, *_RAW_CJK_SUFFIXES))


def _strip_legal_forms(text: str) -> str:
    previous = None
    while text != previous:
        previous = text
        tokens = _drop_suffix(_drop_prefix(text.split()))
        text = " ".join(tokens)
        for affix in _CJK_PREFIXES:
            if text.startswith(affix):
                text = text[len(affix) :].strip()
                break
        for affix in _CJK_SUFFIXES:
            if text.endswith(affix):
                text = text[: -len(affix)].strip()
                break
    return text


def _drop_prefix(tokens: list[str]) -> list[str]:
    for form in _PREFIXES:
        if tuple(tokens[: len(form)]) == form:
            return tokens[len(form) :]
    return tokens


def _drop_suffix(tokens: list[str]) -> list[str]:
    for form in _SUFFIXES:
        if len(tokens) >= len(form) and tuple(tokens[len(tokens) - len(form) :]) == form:
            return tokens[: len(tokens) - len(form)]
    return tokens
