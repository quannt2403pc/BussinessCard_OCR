import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.card import BusinessCard
from app.models.company import Company, CompanyProfile
from app.models.user import User
from app.prompts.enrichment import build_research_prompt, build_structure_prompt
from app.repositories import company as company_repo
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
from tests.conftest import make_user

#: Chủ sở hữu của mọi bản ghi trong file này (task 12.8). `0005` đặt `user_id` là NOT NULL
#: trên cả 6 bảng dữ liệu, nên object ORM nào ghi xuống DB cũng phải có nó. File này không
#: kiểm việc tách dữ liệu (đó là 12.6/12.7 của T) nên một chủ sở hữu duy nhất là đủ.
OWNER_ID = uuid.uuid4()


@pytest.fixture
async def owner(db_session: AsyncSession) -> User:
    """Hàng `users` cho `OWNER_ID` (task 12.8).

    Khoá ngoại `business_cards.user_id` / `companies.user_id` (revision `0005`) đòi chủ sở hữu
    tồn tại thật, nên test nào **ghi xuống DB** cũng phải dựng hàng này trước. Test chạy trên
    `FakeSession` thì không cần — vì thế fixture này không autouse.
    """
    return await make_user(
        db_session, "owner-enrichment@example.com", "Chủ sở hữu dữ liệu test", user_id=OWNER_ID
    )


NOW = datetime(2026, 9, 14, tzinfo=UTC)
REDIRECT = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc123"
TAX_PAGE = "https://masothue.com/0301234567-cong-ty-abc"
GENERATE_URL = "http://cliproxy.test/v1beta/models/gemini-3-flash:generateContent"
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
            user_id=OWNER_ID,
            website="https://abc.vn",
            email="an@abc.vn",
            address="1 Lê Lợi, Q1",
            phone="+842838220000",
            language_detected="vi",
        ),
        BusinessCard(
            user_id=OWNER_ID,
            email="binh@gmail.com",
            address="1  Lê Lợi, Q1",
            language_detected="en",
        ),
    ]
    assert build_hints(cards) == {
        "website": "https://abc.vn",
        "address": "1 Lê Lợi, Q1",
        "country": "Việt Nam",
        "email_domain": "abc.vn",
        "phone": "+842838220000",
    }
    assert build_hints([]) == {}


def test_research_prompt_has_no_json_format() -> None:
    prompt = build_research_prompt("ABC", {"website": "abc.vn", "phone": None})
    assert "Tên công ty in trên danh thiếp: ABC" in prompt
    assert "- Website: abc.vn" in prompt
    assert "Điện thoại" not in prompt
    assert '"sources"' not in prompt


def test_structure_prompt_lists_sources() -> None:
    prompt = build_structure_prompt(
        "MST {0301234567}", [("masothue.com", TAX_PAGE), (None, "https://abc.vn")]
    )
    assert "MST {0301234567}" in prompt
    assert f"[1] masothue.com — {TAX_PAGE}" in prompt
    assert "[2] https://abc.vn — https://abc.vn" in prompt


@respx.mock
async def test_enrich_company_end_to_end() -> None:
    answer = {
        "legal_name": "Công ty TNHH ABC",
        "tax_code": "0301234567",
        "founded_year": 2005,
        "address": "1 Lê Lợi, Q1",
        "sources": {
            "legal_name": [{"url": TAX_PAGE, "title": "MST"}],
            "tax_code": [{"url": TAX_PAGE}],
            "address": [{"url": "https://abc.vn/lien-he"}],
        },
    }
    research = gemini_response(
        "Tên pháp lý: Công ty TNHH ABC, MST 0301234567 (masothue.com).",
        [
            {"web": {"uri": REDIRECT, "title": "masothue.com"}},
            {"web": {"uri": "https://abc.vn/lien-he", "title": "abc.vn"}},
        ],
    )
    structure = gemini_response(f"```json\n{json.dumps(answer, ensure_ascii=False)}\n```")
    route = respx.post(GENERATE_URL).mock(
        side_effect=[httpx.Response(200, json=research), httpx.Response(200, json=structure)]
    )
    respx.get(REDIRECT).mock(return_value=httpx.Response(302, headers={"location": TAX_PAGE}))

    async with CliProxyClient(base_url="http://cliproxy.test") as client:
        profile = await enrich_company(
            "  Công ty TNHH   ABC ",
            {"website": "abc.vn", "phone": None},
            model="gemini-3-flash",
            client=client,
        )

    assert route.call_count == 2
    research_payload = json.loads(route.calls[0].request.content)
    assert research_payload["tools"] == [{"googleSearch": {}}]
    assert "systemInstruction" not in research_payload
    research_prompt = research_payload["contents"][0]["parts"][0]["text"]
    assert "Công ty TNHH ABC" in research_prompt and "abc.vn" in research_prompt

    structure_payload = json.loads(route.calls[1].request.content)
    assert "tools" not in structure_payload
    structure_prompt = structure_payload["contents"][0]["parts"][0]["text"]
    assert "MST 0301234567" in structure_prompt
    assert TAX_PAGE in structure_prompt and REDIRECT not in structure_prompt

    assert profile.legal_name == "Công ty TNHH ABC"
    assert profile.sources["legal_name"][0].url == TAX_PAGE
    assert profile.founded_year is None
    assert profile.unverified_fields == ["founded_year"]
    assert profile.sourced_field_count() == 3


@respx.mock
async def test_enrich_company_without_grounding_skips_structuring() -> None:
    route = respx.post(GENERATE_URL).mock(
        return_value=httpx.Response(200, json=gemini_response("MST 0301234567."))
    )
    async with CliProxyClient(base_url="http://cliproxy.test") as client:
        profile = await enrich_company("ABC", model="gemini-3-flash", client=client)

    assert route.call_count == 1
    assert profile.sourced_field_count() == 0
    assert profile.unverified_fields == []


def test_value_longer_than_db_column_is_cleared_not_truncated() -> None:
    long_range = "9.877 nhân sự (tính đến ngày 31/12/2023) / Khoảng 10.000 lao động"
    raw = {
        "tax_code": "0300588569",
        "employee_range": long_range,
        "sources": {"tax_code": [TAX_PAGE], "employee_range": [TAX_PAGE]},
    }
    profile = validate(raw)
    assert len(long_range) > 64
    assert profile.employee_range is None
    assert profile.tax_code == "0300588569"
    assert "employee_range" not in profile.sources
    assert profile.unverified_fields == ["employee_range"]


async def test_enrich_company_rejects_blank_name() -> None:
    with pytest.raises(ValueError):
        await enrich_company("   ")


async def saved_profiles(db: AsyncSession, company: Company) -> list[CompanyProfile]:
    rows = await db.scalars(
        select(CompanyProfile)
        .where(CompanyProfile.company_id == company.id)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def add_company(db: AsyncSession) -> Company:
    company = Company(user_id=OWNER_ID, display_name="Công ty TNHH ABC", name_normalized="abc")
    db.add(company)
    await db.flush()
    return company


async def test_db_saved_profile_keeps_only_sourced_fields(
    db_session: AsyncSession, owner: User
) -> None:
    company = await add_company(db_session)
    profile = validate(
        {
            "tax_code": "0301234567",
            "founded_year": 2005,
            "address": "1 Lê Lợi",
            "sources": {
                "tax_code": [{"url": TAX_PAGE, "title": "MST"}],
                "address": [{"url": "https://fake-directory.vn/abc"}],
            },
        }
    )

    await company_repo.save_profile(
        db_session, company.id, profile, llm_model="gemini-3-flash", generated_at=NOW
    )
    [stored] = await saved_profiles(db_session, company)

    assert stored.tax_code == "0301234567"
    assert stored.founded_year is None
    assert stored.address is None
    assert set(stored.sources or {}) == {"tax_code"}
    assert stored.sources["tax_code"][0]["url"] == TAX_PAGE
    assert stored.status == ProfileStatus.GENERATED.value


async def test_db_first_enrich_turns_draft_into_generated(
    db_session: AsyncSession, owner: User
) -> None:
    company = await add_company(db_session)
    await company_repo.ensure_draft_profile(db_session, company.id)
    [draft] = await saved_profiles(db_session, company)

    profile = validate({"tax_code": "0301234567", "sources": {"tax_code": [{"url": TAX_PAGE}]}})
    await company_repo.save_profile(db_session, company.id, profile, generated_at=NOW)
    [stored] = await saved_profiles(db_session, company)

    assert stored.id == draft.id
    assert stored.status == ProfileStatus.GENERATED.value
    assert stored.generated_at == NOW.replace(tzinfo=None)


async def test_db_regenerate_overwrites_manual_edit_in_place(
    db_session: AsyncSession, owner: User
) -> None:
    company = await add_company(db_session)
    db_session.add(
        CompanyProfile(
            user_id=OWNER_ID,
            company_id=company.id,
            tax_code="0309999999",
            size_label="SME",
            status=ProfileStatus.VERIFIED.value,
            sources={},
        )
    )
    await db_session.flush()
    [manual] = await saved_profiles(db_session, company)

    profile = validate({"tax_code": "0301234567", "sources": {"tax_code": [{"url": TAX_PAGE}]}})
    await company_repo.save_profile(db_session, company.id, profile, generated_at=NOW)
    [stored] = await saved_profiles(db_session, company)
    count = await db_session.scalar(
        select(func.count()).where(CompanyProfile.company_id == company.id)
    )

    assert count == 1
    assert stored.id == manual.id
    assert stored.tax_code == "0301234567"
    assert stored.size_label is None
    assert stored.status == ProfileStatus.GENERATED.value
    assert set(stored.sources or {}) == {"tax_code"}
