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
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    display_name_vi: str | None = None,
    email: str | None = None,
    website: str | None = None,
) -> uuid.UUID:
    """Tìm hoặc tạo công ty **trong phạm vi một không gian làm việc**.

    Gộp công ty **không bao giờ vượt qua ranh giới không gian**: ứng viên lấy toàn cục thì danh
    thiếp của tổ chức A có thể bị gắn vào công ty của tổ chức B — vừa rò tên công ty của B, vừa
    tạo ra một `company_id` mà mọi bộ lọc sau đó đều coi là không tồn tại.

    Hai tham số, hai việc khác hẳn nhau: `workspace_id` quyết định **tìm và ghi trong phạm vi
    nào**, còn `user_id` chỉ đi vào cột người tạo của dòng mới.

    `display_name_vi` lấy từ `business_cards.company_name_vi` — **không gọi model ở đây**. Công ty
    có sẵn mà đang thiếu bản Việt thì được **lấp vào** (thẻ đầu tiên có thể là thẻ chưa dịch);
    đã có rồi thì không ghi đè.
    """
    display_name = " ".join(raw_name.split())[:MAX_NAME_LENGTH]
    key = normalize_company_name(raw_name)[:MAX_NAME_LENGTH]

    company_id = await db.scalar(
        select(Company.id).where(
            Company.workspace_id == workspace_id, Company.name_normalized == key
        )
    )
    if company_id is None:
        candidates = await _load_candidates(db, workspace_id)
        company_id = find_match(key, extract_domains(email, website), candidates)
    if company_id is None:
        return await _insert_company(
            db, key, display_name, workspace_id, user_id, _vi_of(display_name, display_name_vi)
        )

    await _remember_alias(db, company_id, display_name)
    await _fill_missing_vi(db, company_id, _vi_of(display_name, display_name_vi))
    return company_id


def _similarity(key: str, domains: frozenset[str], candidate: Candidate) -> float:
    other = candidate.name_normalized
    if _scripts(key) != _scripts(other) or _DIGITS.findall(key) != _DIGITS.findall(other):
        return 0.0

    if domains_overlap(domains, candidate.domains):
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


def domains_overlap(left: frozenset[str], right: frozenset[str]) -> bool:
    return any(a == b or a.endswith(f".{b}") or b.endswith(f".{a}") for a in left for b in right)


async def _load_candidates(db: AsyncSession, workspace_id: uuid.UUID) -> list[Candidate]:
    companies = (
        await db.execute(
            select(Company.id, Company.name_normalized).where(Company.workspace_id == workspace_id)
        )
    ).all()
    contacts = await db.execute(
        select(BusinessCard.company_id, BusinessCard.email, BusinessCard.website).where(
            BusinessCard.workspace_id == workspace_id, BusinessCard.company_id.is_not(None)
        )
    )

    domains: dict[uuid.UUID | None, set[str]] = {}
    for company_id, email, website in contacts:
        domains.setdefault(company_id, set()).update(extract_domains(email, website))

    return [
        Candidate(company_id, name, frozenset(domains.get(company_id, set())))
        for company_id, name in companies
    ]


async def _insert_company(
    db: AsyncSession,
    key: str,
    display_name: str,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    display_name_vi: str | None,
) -> uuid.UUID:
    inserted = await db.scalar(
        insert(Company)
        .values(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            user_id=user_id,
            name_normalized=key,
            display_name=display_name,
            display_name_vi=display_name_vi,
            aliases=[display_name],
        )
        # `index_elements` phải khớp **đúng** bộ cột của index unique, nếu không Postgres báo *no
        # unique or exclusion constraint matching the ON CONFLICT specification*.
        .on_conflict_do_nothing(index_elements=[Company.workspace_id, Company.name_normalized])
        .returning(Company.id)
    )
    if inserted is not None:
        return inserted

    existing = await db.execute(
        select(Company.id).where(
            Company.workspace_id == workspace_id, Company.name_normalized == key
        )
    )
    return existing.scalar_one()


async def _remember_alias(db: AsyncSession, company_id: uuid.UUID, name: str) -> None:
    company = await db.get_one(Company, company_id)
    aliases = company.aliases or []
    if name not in aliases:
        company.aliases = [*aliases, name]
        await db.flush()


def _vi_of(display_name: str, display_name_vi: str | None) -> str | None:
    """Bản Việt hoá đáng lưu, hoặc `None`.

    Trùng y hệt tên gốc thì trả `None`: lưu bản chép y nguyên thì giao diện in hai dòng giống hệt.
    """
    cleaned = " ".join((display_name_vi or "").split())[:MAX_NAME_LENGTH]
    if not cleaned or cleaned.casefold() == display_name.casefold():
        return None
    return cleaned


async def _fill_missing_vi(db: AsyncSession, company_id: uuid.UUID, name_vi: str | None) -> None:
    """Lấp bản Việt cho công ty đã có mà còn trống. Không ghi đè bản đang có — xem `upsert_company()`."""
    if name_vi is None:
        return
    company = await db.get_one(Company, company_id)
    if not company.display_name_vi:
        company.display_name_vi = name_vi
        await db.flush()
