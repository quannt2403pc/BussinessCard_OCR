import argparse
import asyncio
import json
import sys
import uuid

from app.core.db import SessionLocal
from app.repositories import company as company_repo
from app.schemas.company import CompanyProfileOut
from app.services.enrichment import build_hints, enrich_company
from app.services.llm import LLMError

HINT_KEYS = ("website", "address", "country", "email_domain", "phone")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enrich one company profile (task 4.7)")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--name", help="Company name as printed on the card")
    target.add_argument("--company-id", type=uuid.UUID, help="Load name and hints from DB")
    for key in HINT_KEYS:
        parser.add_argument(f"--{key.replace('_', '-')}", dest=key)
    parser.add_argument("--model", help="Override LLM_MODEL")
    parser.add_argument("--save", action="store_true", help="Store profile (needs --company-id)")
    args = parser.parse_args()
    if args.save and args.company_id is None:
        parser.error("--save requires --company-id")
    return args


async def run(args: argparse.Namespace) -> int:
    hints = {key: getattr(args, key) for key in HINT_KEYS if getattr(args, key)}

    if args.company_id is None:
        profile = await enrich(args.name, hints, args.model)
        return 0 if profile else 1

    async with SessionLocal() as db:
        company = await company_repo.get_company(db, args.company_id)
        if company is None:
            print(f"Company {args.company_id} not found", file=sys.stderr)
            return 2

        contacts = await company_repo.list_contacts(db, company.id)
        profile = await enrich(company.display_name, {**build_hints(contacts), **hints}, args.model)
        if profile is None:
            return 1

        if args.save:
            saved = await company_repo.save_profile(
                db,
                company.id,
                profile,
                llm_model=profile.llm_model,
                generated_at=profile.generated_at,
            )
            await db.commit()
            print(f"Saved profile {saved.id} ({saved.status})", file=sys.stderr)
    return 0


async def enrich(name: str, hints: dict[str, str], model: str | None) -> CompanyProfileOut | None:
    print(f"Enriching {name!r} with hints {hints}", file=sys.stderr)
    try:
        profile = await enrich_company(name, hints, model=model)
    except LLMError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return None

    print(json.dumps(profile.model_dump(mode="json"), ensure_ascii=False, indent=2))
    print(
        f"Sourced fields: {profile.sourced_field_count()} · unverified: {profile.unverified_fields}",
        file=sys.stderr,
    )
    return profile


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
