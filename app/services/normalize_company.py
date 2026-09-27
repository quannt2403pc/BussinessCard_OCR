"""Chuẩn hoá tên công ty thành khoá chống trùng `companies.name_normalized`.

Chủ sở hữu: T | Task: 3.7, I-21 | xem Task.md

⚠️ **Hình thức pháp lý viết bằng ASCII chỉ được gỡ khi chữ gốc không có dấu** (`I-21`). Bỏ dấu
chạy trước khi so hậu tố, nên `Cơ` và `Cô` đều thành `co` và bị cắt y như `Co.` của tiếng Anh:
*Công ty TNHH Phú Cơ* và *Công ty TNHH Phú* cùng ra khoá `phu`, *Minh Cô* và *Minh* cùng ra
`minh`. Trùng khoá thì `upsert_company()` gộp thẳng **không qua so mờ**, mà task gộp tay `8.10`
đã cắt — gộp nhầm rồi thì không có đường nào tách ra từ giao diện.
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
    return _strip_legal_forms(text, _accented_tokens(name)) or text


def _accented_tokens(name: str) -> frozenset[str]:
    """Những token trong khoá mà **chữ gốc có dấu** — lá chắn của `I-21`.

    Trả về token *sau khi* bỏ dấu, vì đó là dạng mà `_strip_legal_forms()` đang cầm trên tay.
    `Phú Cơ` cho `{"phu", "co"}`, còn `Phu Co` cho tập rỗng — nhờ đó hậu tố ASCII `co` gỡ được ở
    cái sau mà không đụng tới cái trước.

    Không chạy `_join_initials()`: token ghép từ các chữ cái rời (`A.B.C` → `abc`) sẽ không khớp
    tập này, tức bị coi là *không dấu*. Chấp nhận được — chuỗi chữ cái rời ghép lại đúng bằng một
    hình thức pháp lý là chuyện không xảy ra, và nhầm theo chiều này chỉ làm mất một lần gỡ hậu
    tố chứ không gộp nhầm hai công ty.
    """
    text = unicodedata.normalize("NFKC", name).casefold()
    text = "".join(" " if unicodedata.category(char)[0] in "PS" else char for char in text)
    return frozenset(
        stripped for token in text.split() if (stripped := _strip_latin_diacritics(token)) != token
    )


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


def _token_forms(raw: Iterable[str]) -> tuple[tuple[tuple[str, ...], bool], ...]:
    """Mỗi hình thức pháp lý kèm cờ **nguồn viết bằng ASCII** — cờ ấy bật lá chắn `I-21`.

    Chỉ dạng ASCII mới cần lá chắn: `co`, `ltd`, `group`… là chữ tiếng Anh, nên chữ gốc có dấu
    nghĩa là nó **không phải** hình thức pháp lý. Ngược lại `công ty`, `cổ phần`, `tập đoàn` vốn
    đã có dấu — bắt chúng phải không dấu thì không bao giờ gỡ được gì.

    Dạng viết tắt tiếng Việt (`cty`, `tnhh`, `mtv`, `cp`) cũng là ASCII nên cũng mang cờ này, vô
    hại: không có chữ tiếng Việt nào bỏ dấu ra đúng những chuỗi đó.
    """
    forms = {(tuple(_normalize_text(form).split()), form.isascii()) for form in raw}
    return tuple(sorted(forms, key=lambda item: len(item[0]), reverse=True))


def _string_forms(raw: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({_normalize_text(form) for form in raw}, key=len, reverse=True))


_PREFIXES = _token_forms(_RAW_PREFIXES)
_SUFFIXES = _token_forms(_RAW_SUFFIXES)
_CJK_PREFIXES = _string_forms(_RAW_CJK_BOTH_ENDS)
_CJK_SUFFIXES = _string_forms((*_RAW_CJK_BOTH_ENDS, *_RAW_CJK_SUFFIXES))


def _strip_legal_forms(text: str, accented: frozenset[str] = frozenset()) -> str:
    previous = None
    while text != previous:
        previous = text
        tokens = _drop_suffix(_drop_prefix(text.split(), accented), accented)
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


def _drop_prefix(tokens: list[str], accented: frozenset[str]) -> list[str]:
    for form, ascii_source in _PREFIXES:
        matched = tokens[: len(form)]
        if tuple(matched) == form and _strippable(matched, ascii_source, accented):
            return tokens[len(form) :]
    return tokens


def _drop_suffix(tokens: list[str], accented: frozenset[str]) -> list[str]:
    for form, ascii_source in _SUFFIXES:
        if len(tokens) < len(form):
            continue
        matched = tokens[len(tokens) - len(form) :]
        if tuple(matched) == form and _strippable(matched, ascii_source, accented):
            return tokens[: len(tokens) - len(form)]
    return tokens


def _strippable(matched: list[str], ascii_source: bool, accented: frozenset[str]) -> bool:
    """Cụm vừa khớp có thật sự là hình thức pháp lý không (`I-21`).

    Hình thức viết bằng ASCII mà chữ gốc lại **có dấu** thì chỉ là trùng hình thức sau khi bỏ
    dấu — `Cơ` không phải `Co.`, `Cô` không phải `Co.` — nên giữ nguyên.
    """
    return not ascii_source or not any(token in accented for token in matched)


def normalize_label(text: str) -> str:
    """Khoá chuẩn hoá cho một nhãn tự do — nay chỉ còn `NEXT-04` dùng để so tên người.

    Khác `normalize_company_name()` ở đúng một chỗ: **không gỡ hình thức pháp lý**. Với tên công
    ty thì "Co., Ltd" là nhiễu, nhưng với tên người thì mọi chữ đều mang nghĩa.

    Ném `ValueError` khi nhãn không còn ký tự nào có nghĩa.
    """
    key = _normalize_text(text)
    if not key:
        raise ValueError(f"Nhãn rỗng sau khi chuẩn hoá: {text!r}")
    return key
