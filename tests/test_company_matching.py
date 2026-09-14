import uuid

import pytest

from app.services.company_matching import Candidate, extract_domains, find_match


def candidate(name: str, *domains: str) -> Candidate:
    return Candidate(uuid.uuid4(), name, frozenset(domains))


@pytest.mark.parametrize(
    ("email", "website", "expected"),
    [
        ("an@fpt.com.vn", None, {"fpt.com.vn"}),
        (None, "https://www.fpt-software.com/en", {"fpt-software.com"}),
        (None, "www.abc.vn", {"abc.vn"}),
        ("An@FPT.com.vn ", "fpt.com.vn", {"fpt.com.vn"}),
        ("someone@gmail.com", None, set()),
        ("khong-co-a-cong", "http://[hong", set()),
        (None, None, set()),
    ],
)
def test_extract_domains(email: str | None, website: str | None, expected: set[str]) -> None:
    assert extract_domains(email, website) == expected


def test_no_candidates() -> None:
    assert find_match("abc trading", frozenset(), []) is None


def test_exact_key_wins() -> None:
    near = candidate("abc tradin")
    exact = candidate("abc trading")
    assert find_match("abc trading", frozenset(), [near, exact]) == exact.company_id


def test_ocr_typo_in_long_name_is_merged() -> None:
    existing = candidate("samsung electronics")
    assert find_match("samsung electronic", frozenset(), [existing]) == existing.company_id


@pytest.mark.parametrize(
    ("key", "existing"),
    [
        ("fpt", "fpt software"),
        ("samsung", "samsung electronics"),
        ("minh an", "minh anh"),
        ("abc trading 2", "abc trading 3"),
        ("samsung electro mechanics", "samsung electronics"),
    ],
)
def test_different_companies_are_not_merged(key: str, existing: str) -> None:
    assert find_match(key, frozenset(), [candidate(existing)]) is None


def test_shared_domain_merges_longer_name() -> None:
    existing = candidate("fpt software", "fpt-software.com")
    found = find_match("fpt software vietnam", frozenset({"fpt-software.com"}), [existing])
    assert found == existing.company_id


def test_subdomain_counts_as_same_domain() -> None:
    existing = candidate("fpt software", "fpt-software.com")
    found = find_match("fpt software hcm", frozenset({"hcm.fpt-software.com"}), [existing])
    assert found == existing.company_id


def test_group_domain_does_not_merge_sister_companies() -> None:
    existing = candidate("fpt software", "fpt.com.vn")
    assert find_match("fpt telecom", frozenset({"fpt.com.vn"}), [existing]) is None


def test_conflicting_domains_block_name_match() -> None:
    existing = candidate("samsung electronics", "samsung.com")
    assert find_match("samsung electronic", frozenset({"samsung-vn.net"}), [existing]) is None


def test_domain_known_on_one_side_still_allows_name_match() -> None:
    existing = candidate("samsung electronics", "samsung.com")
    assert find_match("samsung electronic", frozenset(), [existing]) == existing.company_id


def test_different_scripts_are_not_compared() -> None:
    existing = candidate("sony corporation", "sony.co.jp")
    assert find_match("ソニー", frozenset({"sony.co.jp"}), [existing]) is None


def test_best_candidate_is_chosen() -> None:
    farther = candidate("hoang long logistix")
    closer = candidate("hoang long logistic")
    found = find_match("hoang long logistics", frozenset(), [farther, closer])
    assert found == closer.company_id
