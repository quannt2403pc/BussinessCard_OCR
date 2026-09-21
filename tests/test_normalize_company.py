"""Test chuẩn hoá tên công ty.

Chủ sở hữu: T | Task: 4.9 | xem Task.md
"""

import unicodedata

import pytest

from app.services.normalize_company import normalize_company_name


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Tiếng Việt: hình thức pháp lý đứng đầu, có/không dấu, viết tắt
        ("Công ty TNHH ABC", "abc"),
        ("Cong ty TNHH ABC", "abc"),
        ("Cty TNHH ABC", "abc"),
        ("Công ty Cổ phần FPT", "fpt"),
        ("Công ty CP FPT", "fpt"),
        ("CTCP FPT", "fpt"),
        ("Công ty TNHH MTV ABC", "abc"),
        ("Công ty TNHH Một Thành Viên ABC", "abc"),
        ("ABC Công ty Cổ phần", "abc"),
        ("Đông Á", "dong a"),
        # Tiếng Anh: dấu câu, nhiều hậu tố liên tiếp
        ("ABC Co., Ltd.", "abc"),
        ("ABC Company Limited", "abc"),
        ("ABC JSC", "abc"),
        ("ABC Pte. Ltd.", "abc"),
        # Nhật, Hàn, Trung — cả dạng viết tắt đóng khung
        ("株式会社ソニー", "ソニー"),
        ("ソニー株式会社", "ソニー"),
        ("(株)ソニー", "ソニー"),
        ("株式会社ガス", "ガス"),
        ("주식회사 한빛", "한빛"),
        ("㈜한빛", "한빛"),
        ("深圳远洋有限公司", "深圳远洋"),
        # Định dạng
        ("ＡＢＣ　ＬＴＤ", "abc"),
        ("ABC   Trading    Ltd", "abc trading"),
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize_company_name(raw) == expected


@pytest.mark.parametrize(
    "variants",
    [
        ["Công ty TNHH ABC", "CONG TY TNHH ABC", "ABC Co., Ltd", "abc ltd"],
        ["Coca-Cola Ltd", "Coca Cola Ltd"],
        ["A.B.C Corp", "ABC Corp"],
        [unicodedata.normalize("NFD", "Công ty Cổ phần Á Châu"), "Công ty Cổ phần Á Châu"],
        [
            "TẬP ĐOÀN FPT CORPORATION",
            "Tập đoàn FPT",
            "Công ty Cổ phần FPT",
            "FPT Corporation",
            "FPT Group",
        ],
        ["Tổng công ty Cổ phần Bảo Minh", "Công ty Cổ phần Bảo Minh", "Bảo Minh Corporation"],
    ],
)
def test_same_company_same_key(variants: list[str]) -> None:
    assert len({normalize_company_name(v) for v in variants}) == 1


@pytest.mark.parametrize("raw", ["Zinc", "Unlimited", "Cisco", "Tesco"])
def test_does_not_cut_inside_words(raw: str) -> None:
    assert normalize_company_name(raw) == raw.casefold()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("CP Group", "cp group"), ("MTV Networks", "mtv networks"), ("Cô Tô Travel", "co to travel")],
)
def test_brand_is_not_mistaken_for_legal_form(raw: str, expected: str) -> None:
    assert normalize_company_name(raw) == expected


def test_hangul_is_stored_composed() -> None:
    key = normalize_company_name("주식회사 한빛")
    assert unicodedata.normalize("NFC", key) == key


@pytest.mark.parametrize("raw", ["株式会社", "Công ty TNHH", "Company"])
def test_legal_form_only_is_never_empty(raw: str) -> None:
    assert normalize_company_name(raw) != ""


@pytest.mark.parametrize(
    "raw",
    ["Công ty TNHH ABC", "ABC Co., Ltd.", "株式会社ソニー", "㈜한빛", "株式会社", "A.B.C Corp"],
)
def test_idempotent(raw: str) -> None:
    key = normalize_company_name(raw)
    assert normalize_company_name(key) == key


@pytest.mark.parametrize("raw", ["", "   ", "...", "（）"])
def test_empty_name_raises(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_company_name(raw)


def test_subsidiary_in_another_script_keeps_its_own_key() -> None:
    assert normalize_company_name("FPTジャパン株式会社") != normalize_company_name("Tập đoàn FPT")


@pytest.mark.parametrize("raw", ["Tập đoàn", "Tổng công ty", "Group"])
def test_group_word_alone_is_never_empty(raw: str) -> None:
    assert normalize_company_name(raw) != ""
