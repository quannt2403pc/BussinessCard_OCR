import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from app.models.card import BusinessCard
from app.schemas.company import ProfileStatus
from app.services.cliproxy_client import CliProxyClient
from app.services.enrichment import (
    EnrichmentParseError,
    GroundingSource,
    build_hints,
    enrich_company,
    extract_grounding,
    parse_profile_json,
    resolve_redirects,
    response_text,
    validate_profile,
)
from app.services.llm import LLMBlockedError, LLMError

NOW = datetime(2026, 9, 14, tzinfo=UTC)
REDIRECT = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc123"
TAX_PAGE = "https://masothue.com/0301234567-cong-ty-abc"
GROUNDING = [
    GroundingSource(uri=REDIRECT, title="masothue.com", resolved_url=TAX_PAGE),
    GroundingSource(
        uri="https://abc.vn/lien-he", title="abc.vn", resolved_url="https://abc.vn/lien-he"
    ),
]


def validate(raw: dict[str, Any], grounding: list[GroundingSource] | None = None):
    return validate_profile(
        raw,
        GROUNDING if grounding is None else grounding,
        llm_model="gemini-3-flash",
        generated_at=NOW,
    )


def gemini_response(text: str, chunks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "content": {"role": "model", "parts": [{"text": text}]},
        "finishReason": "STOP",
    }
    if chunks is not None:
        candidate["groundingMetadata"] = {"groundingChunks": chunks}
    return {"candidates": [candidate]}


@pytest.mark.parametrize(
    "text",
    [
        '{"tax_code": "0301234567"}',
        '```json\n{"tax_code": "0301234567"}\n```',
        'Kết quả tra cứu:\n{"tax_code": "0301234567"}\nHết.',
        '{"tax_code": "0301234567", "sources": {},}',
        '[{"tax_code": "0301234567"}]',
    ],
)
def test_parse_profile_json(text: str) -> None:
    assert parse_profile_json(text)["tax_code"] == "0301234567"


@pytest.mark.parametrize("text", ["không tìm thấy", '{"tax_code": ', "```json\n```"])
def test_parse_profile_json_rejects_non_json(text: str) -> None:
    with pytest.raises(EnrichmentParseError):
        parse_profile_json(text)


def test_grounded_source_keeps_field() -> None:
    profile = validate(
        {"tax_code": "0301234567", "sources": {"tax_code": [{"url": TAX_PAGE, "title": "MST"}]}}
    )
    assert profile.tax_code == "0301234567"
    assert [ref.url for ref in profile.sources["tax_code"]] == [TAX_PAGE]
    assert profile.sources["tax_code"][0].retrieved_at == NOW
    assert profile.unverified_fields == []


def test_field_without_source_is_cleared() -> None:
    profile = validate({"founded_year": 2005, "sources": {}})
    assert profile.founded_year is None
    assert profile.unverified_fields == ["founded_year"]


def test_source_outside_grounding_is_dropped() -> None:
    profile = validate(
        {"address": "1 Lê Lợi", "sources": {"address": [{"url": "https://fake-directory.vn/abc"}]}}
    )
    assert profile.address is None
    assert "address" not in profile.sources
    assert profile.unverified_fields == ["address"]


def test_redirect_source_is_replaced_by_real_url() -> None:
    profile = validate({"tax_code": "0301234567", "sources": {"tax_code": [{"url": REDIRECT}]}})
    assert profile.sources["tax_code"][0].url == TAX_PAGE


def test_unresolved_redirect_is_not_a_source() -> None:
    grounding = [GroundingSource(uri=REDIRECT, title="Mã số thuế ABC")]
    profile = validate(
        {"tax_code": "0301234567", "sources": {"tax_code": [{"url": REDIRECT}]}}, grounding
    )
    assert profile.tax_code is None
    assert profile.unverified_fields == ["tax_code"]


@pytest.mark.parametrize(
    "url", ["https://www.abc.vn/gioi-thieu", "https://careers.abc.vn/", "abc.vn/san-pham"]
)
def test_pages_on_grounded_site_are_accepted(url: str) -> None:
    profile = validate({"products": ["Kho vận"], "sources": {"products": [url]}})
    assert profile.products == ["Kho vận"]


def test_lookalike_domain_is_rejected() -> None:
    profile = validate({"website": "https://abc.vn", "sources": {"website": ["https://notabc.vn"]}})
    assert profile.website is None


def test_no_grounding_clears_every_field() -> None:
    raw = {
        "tax_code": "0301234567",
        "industry": ["Logistics"],
        "description": "ABC làm vận tải.",
        "sources": {"tax_code": [TAX_PAGE], "industry": ["https://abc.vn"]},
    }
    profile = validate(raw, [])
    assert profile.tax_code is None
    assert profile.industry == []
    assert profile.description is None
    assert profile.sources == {}
    assert profile.unverified_fields == ["tax_code", "industry", "description"]


def test_description_is_kept_when_profile_has_a_verified_source() -> None:
    raw = {
        "tax_code": "0301234567",
        "description": "ABC làm vận tải.",
        "sources": {"tax_code": [TAX_PAGE]},
    }
    assert validate(raw).description == "ABC làm vận tải."


def test_invalid_value_is_cleared_without_losing_other_fields() -> None:
    raw = {
        "tax_code": "0301234567",
        "founded_year": "khoảng năm 2005",
        "sources": {"tax_code": [TAX_PAGE], "founded_year": [TAX_PAGE]},
    }
    profile = validate(raw)
    assert profile.tax_code == "0301234567"
    assert profile.founded_year is None
    assert "founded_year" not in profile.sources
    assert profile.unverified_fields == ["founded_year"]


def test_empty_values_are_not_unverified() -> None:
    profile = validate({"legal_name": "", "industry": [], "phone": None, "sources": {}})
    assert profile.unverified_fields == []


def test_unknown_and_malformed_sources_are_ignored() -> None:
    raw = {
        "website": "https://abc.vn",
        "sources": {
            "ceo": [TAX_PAGE],
            "website": [42, {"title": "không có url"}, "https://abc.vn"],
        },
    }
    profile = validate(raw)
    assert set(profile.sources) == {"website"}
    assert profile.sources["website"][0].url == "https://abc.vn"


def test_profile_metadata() -> None:
    profile = validate({"sources": "không phải object"})
    assert profile.status == ProfileStatus.GENERATED
    assert profile.llm_model == "gemini-3-flash"
    assert profile.generated_at == NOW
    assert profile.sourced_field_count() == 0


@pytest.mark.parametrize("wrapped", [False, True])
def test_extract_grounding(wrapped: bool) -> None:
    chunks = [
        {"web": {"uri": REDIRECT, "title": "masothue.com"}},
        {"web": {"uri": REDIRECT, "title": "masothue.com"}},
        {"retrievedContext": {"uri": "gs://bucket"}},
        {"web": {"title": "không có uri"}},
    ]
    data = gemini_response("{}", chunks)
    grounding = extract_grounding({"response": data} if wrapped else data)
    assert grounding == [GroundingSource(uri=REDIRECT, title="masothue.com")]


def test_extract_grounding_without_metadata() -> None:
    assert extract_grounding(gemini_response("{}")) == []


def test_response_text_skips_thoughts() -> None:
    data = gemini_response("")
    data["candidates"][0]["content"]["parts"] = [
        {"text": "nghĩ về {cách tìm}", "thought": True},
        {"text": '{"tax_code": '},
        {"text": '"0301234567"}'},
    ]
    assert response_text(data) == '{"tax_code": "0301234567"}'


def test_response_text_blocked() -> None:
    with pytest.raises(LLMBlockedError):
        response_text({"promptFeedback": {"blockReason": "SAFETY"}})
    data = gemini_response("x")
    data["candidates"][0]["finishReason"] = "SAFETY"
    with pytest.raises(LLMBlockedError):
        response_text(data)


def test_response_text_empty() -> None:
    with pytest.raises(LLMError):
        response_text(gemini_response("   "))
    with pytest.raises(LLMError):
        response_text({"candidates": []})


@respx.mock
async def test_resolve_redirects() -> None:
    respx.get(REDIRECT).mock(return_value=httpx.Response(302, headers={"location": TAX_PAGE}))
    broken = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/broken"
    respx.get(broken).mock(side_effect=httpx.ConnectError("offline"))

    resolved = await resolve_redirects(
        [
            GroundingSource(uri=REDIRECT, title="masothue.com"),
            GroundingSource(uri="https://abc.vn/lien-he", title="abc.vn"),
            GroundingSource(uri=broken, title="abc.vn"),
        ]
    )
    assert [source.resolved_url for source in resolved] == [
        TAX_PAGE,
        "https://abc.vn/lien-he",
        None,
    ]


def test_build_hints() -> None:
    cards = [
        BusinessCard(
            website="https://abc.vn",
            email="an@abc.vn",
            address="1 Lê Lợi, Q1",
            phone="+842838220000",
            language_detected="vi",
        ),
        BusinessCard(email="binh@gmail.com", address="1  Lê Lợi, Q1", language_detected="en"),
    ]
    assert build_hints(cards) == {
        "website": "https://abc.vn",
        "address": "1 Lê Lợi, Q1",
        "country": "Việt Nam",
        "email_domain": "abc.vn",
        "phone": "+842838220000",
    }
    assert build_hints([]) == {}


@respx.mock
async def test_enrich_company_end_to_end() -> None:
    answer = {
        "legal_name": "Công ty TNHH ABC",
        "tax_code": "0301234567",
        "founded_year": 2005,
        "address": "1 Lê Lợi, Q1",
        "sources": {
            "legal_name": [{"url": REDIRECT, "title": "MST"}],
            "tax_code": [{"url": TAX_PAGE}],
            "address": [{"url": "https://abc.vn/lien-he"}],
        },
    }
    route = respx.post("http://cliproxy.test/v1beta/models/gemini-3-flash:generateContent").mock(
        return_value=httpx.Response(
            200,
            json=gemini_response(
                f"```json\n{json.dumps(answer, ensure_ascii=False)}\n```",
                [
                    {"web": {"uri": REDIRECT, "title": "masothue.com"}},
                    {"web": {"uri": "https://abc.vn/lien-he", "title": "abc.vn"}},
                ],
            ),
        )
    )
    respx.get(REDIRECT).mock(return_value=httpx.Response(302, headers={"location": TAX_PAGE}))

    async with CliProxyClient(base_url="http://cliproxy.test") as client:
        profile = await enrich_company(
            "  Công ty TNHH   ABC ",
            {"website": "abc.vn", "phone": None},
            model="gemini-3-flash",
            client=client,
        )

    payload = json.loads(route.calls.last.request.content)
    assert payload["tools"] == [{"googleSearch": {}}]
    assert "systemInstruction" in payload
    prompt = payload["contents"][0]["parts"][0]["text"]
    assert "Công ty TNHH ABC" in prompt and "abc.vn" in prompt

    assert profile.legal_name == "Công ty TNHH ABC"
    assert profile.sources["legal_name"][0].url == TAX_PAGE
    assert profile.founded_year is None
    assert profile.unverified_fields == ["founded_year"]
    assert profile.sourced_field_count() == 3


async def test_enrich_company_rejects_blank_name() -> None:
    with pytest.raises(ValueError):
        await enrich_company("   ")
