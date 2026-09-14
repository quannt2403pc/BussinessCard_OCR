import re
import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.card import BusinessCard
from app.models.company import Company
from app.services.normalize_company import normalize_company_name

NAME_MATCH_THRESHOLD = 90.0
DOMAIN_MATCH_THRESHOLD = 80.0
MIN_FUZZY_LENGTH = 10
MAX_NAME_LENGTH = 255

FREE_EMAIL_DOMAINS = frozenset(
    {
        "126.com",
        "163.com",
        "daum.net",
        "gmail.com",
        "hanmail.net",
        "hotmail.com",
        "icloud.com",
        "live.com",
        "naver.com",
        "outlook.com",
        "qq.com",
        "yahoo.co.jp",
        "yahoo.com",
        "yahoo.com.vn",
    }
)

_DIGITS = re.compile(r"\d+")


@dataclass(frozen=True)
class Candidate:
    company_id: uuid.UUID
    name_normalized: str
    domains: frozenset[str] = frozenset()


def extract_domains(email: str | None = None, website: str | None = None) -> frozenset[str]:
    hosts: list[str] = []
    if email and "@" in email:
        hosts.append(email.rsplit("@", 1)[1])
    if website and website.strip():
        url = website.strip()
        if "://" not in url:
            url = f"http://{url}"
        try:
            host = urlsplit(url).hostname
        except ValueError:
            host = None
        if host:
            hosts.append(host)

    domains = set()
    for host in hosts:
        domain = host.strip().strip(".").lower().removeprefix("www.")
        if "." in domain and domain not in FREE_EMAIL_DOMAINS:
            domains.add(domain)
    return frozenset(domains)


def find_match(
    key: str, domains: frozenset[str], candidates: Sequence[Candidate]
) -> uuid.UUID | None:
    for candidate in candidates:
        if candidate.name_normalized == key:
            return candidate.company_id

    best_score = 0.0
    best_id: uuid.UUID | None = None
    for candidate in candidates:
        score = _similarity(key, domains, candidate)
        if score > best_score:
            best_score, best_id = score, candidate.company_id
    return best_id


async def upsert_company(
    db: AsyncSession,
    raw_name: str,
    *,
    email: str | None = None,
    website: str | None = None,
) -> uuid.UUID:
    display_name = " ".join(raw_name.split())[:MAX_NAME_LENGTH]
    key = normalize_company_name(raw_name)[:MAX_NAME_LENGTH]

    company_id = await db.scalar(select(Company.id).where(Company.name_normalized == key))
    if company_id is None:
        candidates = await _load_candidates(db)
        company_id = find_match(key, extract_domains(email, website), candidates)
    if company_id is None:
        return await _insert_company(db, key, display_name)

    await _remember_alias(db, company_id, display_name)
    return company_id


def _similarity(key: str, domains: frozenset[str], candidate: Candidate) -> float:
    other = candidate.name_normalized
    if _scripts(key) != _scripts(other) or _DIGITS.findall(key) != _DIGITS.findall(other):
        return 0.0

    if _domains_overlap(domains, candidate.domains):
        score = fuzz.token_set_ratio(key, other)
        return score if score >= DOMAIN_MATCH_THRESHOLD else 0.0
    if domains and candidate.domains:
        return 0.0

    if min(len(key), len(other)) < MIN_FUZZY_LENGTH:
        return 0.0
    score = fuzz.ratio(key, other)
    return score if score >= NAME_MATCH_THRESHOLD else 0.0


def _scripts(text: str) -> frozenset[str]:
    scripts = set()
    for char in text:
        if not char.isalpha():
            continue
        head = unicodedata.name(char, "").split(" ", 1)[0]
        scripts.add("KANA" if "HIRAGANA" in head or "KATAKANA" in head else head)
    return frozenset(scripts)


def _domains_overlap(left: frozenset[str], right: frozenset[str]) -> bool:
    return any(a == b or a.endswith(f".{b}") or b.endswith(f".{a}") for a in left for b in right)


async def _load_candidates(db: AsyncSession) -> list[Candidate]:
    companies = (await db.execute(select(Company.id, Company.name_normalized))).all()
    contacts = await db.execute(
        select(BusinessCard.company_id, BusinessCard.email, BusinessCard.website).where(
            BusinessCard.company_id.is_not(None)
        )
    )

    domains: dict[uuid.UUID | None, set[str]] = {}
    for company_id, email, website in contacts:
        domains.setdefault(company_id, set()).update(extract_domains(email, website))

    return [
        Candidate(company_id, name, frozenset(domains.get(company_id, set())))
        for company_id, name in companies
    ]


async def _insert_company(db: AsyncSession, key: str, display_name: str) -> uuid.UUID:
    inserted = await db.scalar(
        insert(Company)
        .values(
            id=uuid.uuid4(),
            name_normalized=key,
            display_name=display_name,
            aliases=[display_name],
        )
        .on_conflict_do_nothing(index_elements=[Company.name_normalized])
        .returning(Company.id)
    )
    if inserted is not None:
        return inserted

    existing = await db.execute(select(Company.id).where(Company.name_normalized == key))
    return existing.scalar_one()


async def _remember_alias(db: AsyncSession, company_id: uuid.UUID, name: str) -> None:
    company = await db.get_one(Company, company_id)
    aliases = company.aliases or []
    if name not in aliases:
        company.aliases = [*aliases, name]
        await db.flush()
