import asyncio
import json
import logging
import re
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import ValidationError

from app.core.config import settings
from app.models.card import BusinessCard
from app.prompts.enrichment import build_research_prompt, build_structure_prompt
from app.schemas.company import (
    SOURCED_FIELDS,
    CompanyProfileOut,
    CompanyProfileSchema,
    ProfileStatus,
    SourceRef,
)
from app.services.cliproxy_client import CliProxyClient
from app.services.company_matching import extract_domains
from app.services.llm import (
    BLOCKED_FINISH_REASONS,
    TOOL_GOOGLE_SEARCH,
    LLMBlockedError,
    LLMError,
    generate_content,
)

logger = logging.getLogger(__name__)

RESEARCH_TEMPERATURE = 0.2
STRUCTURE_TEMPERATURE = 0.0
REDIRECT_TIMEOUT = httpx.Timeout(5.0)
REDIRECT_HOSTS = frozenset({"vertexaisearch.cloud.google.com"})
PROFILE_FIELDS: tuple[str, ...] = (*SOURCED_FIELDS, "description")
LIST_FIELDS = frozenset({"industry", "products"})
COUNTRY_BY_LANGUAGE = {"vi": "Việt Nam", "ja": "Nhật Bản", "ko": "Hàn Quốc", "zh": "Trung Quốc"}

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_TRAILING_COMMA = re.compile(r",\s*([}\]])")


class EnrichmentParseError(LLMError):
    pass


@dataclass(frozen=True)
class GroundingSource:
    uri: str
    title: str | None = None
    resolved_url: str | None = None

    @property
    def hosts(self) -> frozenset[str]:
        hosts = {_host(value) for value in (self.resolved_url, self.title)}
        return frozenset(host for host in hosts if host and host not in REDIRECT_HOSTS)


async def enrich_company(
    name: str,
    hints: Mapping[str, str | None] | None = None,
    *,
    model: str | None = None,
    client: CliProxyClient | None = None,
    http: httpx.AsyncClient | None = None,
) -> CompanyProfileOut:
    company_name = " ".join(name.split())
    if not company_name:
        raise ValueError("Tên công ty rỗng.")

    model_name = model or settings.llm_model
    research_data = await generate_content(
        _payload(
            build_research_prompt(company_name, hints),
            temperature=RESEARCH_TEMPERATURE,
            tools=[TOOL_GOOGLE_SEARCH],
        ),
        model=model_name,
        client=client,
    )
    research = response_text(research_data)
    grounding = await resolve_redirects(extract_grounding(research_data), http=http)

    cited = [(source.title, source.resolved_url) for source in grounding if source.resolved_url]
    raw: dict[str, Any] = {}
    if cited:
        structure_data = await generate_content(
            _payload(build_structure_prompt(research, cited), temperature=STRUCTURE_TEMPERATURE),
            model=model_name,
            client=client,
        )
        raw = parse_profile_json(response_text(structure_data))

    profile = validate_profile(raw, grounding, llm_model=model_name, generated_at=datetime.now(UTC))
    logger.info(
        "Enrich %r: %d grounding sources, %d sourced fields, unverified %s",
        company_name,
        len(grounding),
        profile.sourced_field_count(),
        profile.unverified_fields,
    )
    return profile


def build_hints(cards: Iterable[BusinessCard]) -> dict[str, str]:
    contacts = list(cards)
    candidates: dict[str, Iterable[str | None]] = {
        "website": (card.website for card in contacts),
        "address": (card.address for card in contacts),
        "country": (
            COUNTRY_BY_LANGUAGE.get((card.language_detected or "").strip().lower())
            for card in contacts
        ),
        "email_domain": (
            domain for card in contacts for domain in sorted(extract_domains(card.email, None))
        ),
        "phone": (card.phone for card in contacts),
    }
    hints: dict[str, str] = {}
    for key, values in candidates.items():
        value = _most_common(values)
        if value:
            hints[key] = value
    return hints


def response_text(data: Mapping[str, Any]) -> str:
    root = _response_root(data)
    feedback = root.get("promptFeedback")
    if isinstance(feedback, Mapping) and feedback.get("blockReason"):
        raise LLMBlockedError(f"Provider chặn ngay từ prompt: {feedback['blockReason']}.")

    candidate = _first_candidate(root)
    if candidate is None:
        raise LLMError("Response không có `candidates`.")

    finish_reason = str(candidate.get("finishReason") or "")
    if finish_reason in BLOCKED_FINISH_REASONS:
        raise LLMBlockedError(f"Câu trả lời bị chặn (finishReason={finish_reason}).")

    content = candidate.get("content")
    parts = content.get("parts") if isinstance(content, Mapping) else None
    text = "".join(
        part["text"]
        for part in (parts if isinstance(parts, list) else [])
        if isinstance(part, Mapping)
        and isinstance(part.get("text"), str)
        and not part.get("thought")
    ).strip()
    if not text:
        raise LLMError(f"Model trả về nội dung rỗng (finishReason={finish_reason or 'không rõ'}).")
    return text


def extract_grounding(data: Mapping[str, Any]) -> list[GroundingSource]:
    candidate = _first_candidate(_response_root(data))
    metadata = candidate.get("groundingMetadata") if candidate else None
    chunks = metadata.get("groundingChunks") if isinstance(metadata, Mapping) else None

    sources: dict[str, GroundingSource] = {}
    for chunk in chunks if isinstance(chunks, list) else []:
        web = chunk.get("web") if isinstance(chunk, Mapping) else None
        if not isinstance(web, Mapping) or not isinstance(web.get("uri"), str):
            continue
        title = web.get("title")
        uri = web["uri"]
        sources.setdefault(uri, GroundingSource(uri, title if isinstance(title, str) else None))
    return list(sources.values())


async def resolve_redirects(
    sources: Sequence[GroundingSource], *, http: httpx.AsyncClient | None = None
) -> list[GroundingSource]:
    if http is None:
        async with httpx.AsyncClient(timeout=REDIRECT_TIMEOUT) as own_http:
            return await resolve_redirects(sources, http=own_http)
    return list(await asyncio.gather(*(_resolve(source, http) for source in sources)))


def parse_profile_json(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for block in (*_FENCE.findall(text), text):
        for candidate in (block, _TRAILING_COMMA.sub(r"\1", block)):
            start = candidate.find("{")
            if start < 0:
                continue
            try:
                value, _ = decoder.raw_decode(candidate, start)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    raise EnrichmentParseError(
        f"Không đọc được JSON hồ sơ từ câu trả lời của model: {text[:200]!r}"
    )


def validate_profile(
    raw: Mapping[str, Any],
    grounding: Sequence[GroundingSource],
    *,
    llm_model: str,
    generated_at: datetime,
) -> CompanyProfileOut:
    profile, invalid = _validate_values({name: raw.get(name) for name in PROFILE_FIELDS})
    sources = {
        name: refs
        for name, refs in _verified_sources(raw.get("sources"), grounding, generated_at).items()
        if _has_value(getattr(profile, name))
    }

    unverified = set(invalid)
    values = profile.model_dump(include=set(PROFILE_FIELDS))
    for name in PROFILE_FIELDS:
        if not _has_value(values[name]) or name in sources:
            continue
        if name == "description" and sources:
            continue
        unverified.add(name)
        values[name] = [] if name in LIST_FIELDS else None

    return CompanyProfileOut.model_validate(
        {
            **values,
            "sources": sources,
            "llm_model": llm_model,
            "generated_at": generated_at,
            "status": ProfileStatus.GENERATED,
            "unverified_fields": [name for name in PROFILE_FIELDS if name in unverified],
        }
    )


def _validate_values(values: dict[str, Any]) -> tuple[CompanyProfileSchema, set[str]]:
    try:
        return CompanyProfileSchema.model_validate(values), set()
    except ValidationError as exc:
        invalid = {str(error["loc"][0]) for error in exc.errors() if error["loc"]}
    cleaned = {name: value for name, value in values.items() if name not in invalid}
    return CompanyProfileSchema.model_validate(cleaned), {
        name for name in invalid if _has_value(values.get(name))
    }


def _verified_sources(
    raw_sources: object, grounding: Sequence[GroundingSource], retrieved_at: datetime
) -> dict[str, list[SourceRef]]:
    if not isinstance(raw_sources, Mapping):
        return {}

    resolved = {source.uri: source.resolved_url for source in grounding}
    grounded_hosts = frozenset(host for source in grounding for host in source.hosts)

    verified: dict[str, list[SourceRef]] = {}
    for name, entries in raw_sources.items():
        if name not in PROFILE_FIELDS:
            continue
        refs: dict[str, SourceRef] = {}
        for url, title in _source_entries(entries):
            real_url = resolved.get(url, url)
            host = _host(real_url)
            if real_url and host and _is_grounded(host, grounded_hosts):
                refs.setdefault(
                    real_url, SourceRef(url=real_url, title=title, retrieved_at=retrieved_at)
                )
        if refs:
            verified[name] = list(refs.values())
    return verified


def _source_entries(entries: object) -> Iterator[tuple[str, str | None]]:
    for entry in entries if isinstance(entries, list) else [entries]:
        if isinstance(entry, str):
            url, title = entry, None
        elif isinstance(entry, Mapping) and isinstance(entry.get("url"), str):
            url = entry["url"]
            title = entry["title"] if isinstance(entry.get("title"), str) else None
        else:
            continue
        url = url.strip()
        if url:
            yield (url if "://" in url else f"https://{url}"), title


async def _resolve(source: GroundingSource, http: httpx.AsyncClient) -> GroundingSource:
    if _host(source.uri) not in REDIRECT_HOSTS:
        return replace(source, resolved_url=source.uri)
    try:
        response = await http.get(source.uri, follow_redirects=False)
    except httpx.HTTPError as exc:
        logger.info("Không theo được redirect grounding %s: %s", source.uri, exc)
        return source
    location = response.headers.get("location")
    if not response.is_redirect or not location:
        return source
    return replace(source, resolved_url=urljoin(source.uri, location))


def _response_root(data: Mapping[str, Any]) -> Mapping[str, Any]:
    wrapped = data.get("response")
    if "candidates" not in data and isinstance(wrapped, Mapping):
        return wrapped
    return data


def _first_candidate(root: Mapping[str, Any]) -> Mapping[str, Any] | None:
    candidates = root.get("candidates")
    if isinstance(candidates, list) and candidates and isinstance(candidates[0], Mapping):
        return candidates[0]
    return None


def _host(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text or any(char.isspace() for char in text):
        return None
    try:
        host = urlsplit(text if "://" in text else f"http://{text}").hostname
    except ValueError:
        return None
    if not host or "." not in host:
        return None
    return host.removeprefix("www.")


def _is_grounded(host: str, grounded_hosts: frozenset[str]) -> bool:
    return any(host == grounded or host.endswith(f".{grounded}") for grounded in grounded_hosts)


def _payload(
    text: str, *, temperature: float, tools: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {"temperature": temperature},
    }
    if tools:
        payload["tools"] = tools
    return payload


def _has_value(value: object) -> bool:
    return value not in (None, "", [])


def _most_common(values: Iterable[str | None]) -> str | None:
    counts = Counter(" ".join(value.split()) for value in values if value and value.strip())
    return counts.most_common(1)[0][0] if counts else None
