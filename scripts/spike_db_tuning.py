import argparse
import statistics
import sys
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, Select, create_engine, func, select, tuple_
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401
from app.core.config import settings
from app.core.db import Base
from app.models.card import BusinessCard
from app.models.company import ACTIVE_JOB_ITEM_STATUSES, Company, CompanyProfile, EnrichJobItem
from app.repositories import company as company_repo
from app.routers import export

DATABASE = "bizcard_tuning"
JOB_ID = "00000000-0000-0000-0000-000000000001"
INDUSTRIES = (
    "Logistics",
    "Sữa",
    "Cơ khí",
    "Phần mềm",
    "Xây dựng",
    "Thực phẩm",
    "Dược phẩm",
    "Vận tải",
)

CANDIDATES: dict[str, str] = {
    "ix_business_cards_uploaded_at_id": (
        "CREATE INDEX ix_business_cards_uploaded_at_id ON business_cards (uploaded_at, id)"
    ),
    "ix_companies_display_name_id": (
        "CREATE INDEX ix_companies_display_name_id ON companies (display_name, id)"
    ),
    "ix_companies_search_trgm": (
        "CREATE INDEX ix_companies_search_trgm ON companies "
        "USING gin (display_name gin_trgm_ops, name_normalized gin_trgm_ops)"
    ),
}


def tuning_url(override: str | None) -> URL:
    url = make_url(override or settings.database_url)
    if url.host == "db":
        url = url.set(host="localhost")
    return url.set(database=DATABASE)


def recreate_database(url: URL) -> None:
    admin = create_engine(
        url.set(database="postgres"), poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    with admin.connect() as connection:
        connection.exec_driver_sql(f"DROP DATABASE IF EXISTS {DATABASE}")
        connection.exec_driver_sql(f"CREATE DATABASE {DATABASE}")
    admin.dispose()


def drop_database(url: URL) -> None:
    admin = create_engine(
        url.set(database="postgres"), poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    with admin.connect() as connection:
        connection.exec_driver_sql(f"DROP DATABASE IF EXISTS {DATABASE}")
    admin.dispose()


def seed(engine: Engine, *, companies: int, cards: int) -> None:
    industries = ", ".join(f"'{name}'" for name in INDUSTRIES)
    statements = [
        "CREATE EXTENSION IF NOT EXISTS pg_trgm",
        f"""
        INSERT INTO companies (id, name_normalized, display_name, aliases)
        SELECT gen_random_uuid(),
               'cong ty ' || i,
               'Công ty ' || (ARRAY[{industries}])[1 + mod(i, {len(INDUSTRIES)})] || ' ' || i,
               ARRAY['Alias ' || i]
        FROM generate_series(1, {companies}) AS i
        """,
        """
        INSERT INTO company_profiles (id, company_id, status, tax_code, industry, sources)
        SELECT gen_random_uuid(), id, (ARRAY['generated', 'verified', 'draft'])[1 + mod(n, 3)],
               lpad(n::text, 10, '0'), ARRAY['Ngành ' || mod(n, 20)], '{}'::jsonb
        FROM (SELECT id, row_number() OVER (ORDER BY id) AS n FROM companies) AS c
        WHERE mod(n, 2) = 0
        """,
        f"""
        WITH c AS (SELECT id, row_number() OVER (ORDER BY id) - 1 AS n FROM companies)
        INSERT INTO business_cards
            (id, image_path, image_hash, full_name, status, company_id, uploaded_at)
        SELECT gen_random_uuid(), 'tuning/' || i || '.jpg', md5(i::text) || md5((-i)::text),
               'Người ' || i,
               (ARRAY['pending', 'needs_review', 'confirmed', 'confirmed'])[1 + mod(i, 4)],
               CASE WHEN mod(i, 5) = 0 THEN NULL ELSE c.id END,
               timestamp '2026-09-01' + (i / 10) * interval '1 minute'
        FROM generate_series(1, {cards}) AS i
        LEFT JOIN c ON c.n = mod(i, {companies})
        """,
        f"INSERT INTO enrich_jobs (id, finished_at) VALUES ('{JOB_ID}', now())",
        f"""
        INSERT INTO enrich_job_items (id, job_id, company_id, status, attempts)
        SELECT gen_random_uuid(), '{JOB_ID}', id,
               CASE WHEN n <= 5 THEN 'pending' ELSE 'done' END, 1
        FROM (SELECT id, row_number() OVER (ORDER BY id) AS n FROM companies) AS c
        WHERE mod(n, 3) = 0 OR n <= 5
        """,
        "ANALYZE",
    ]
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        Base.metadata.create_all(connection)
        for statement in statements:
            connection.exec_driver_sql(statement)


def probes(engine: Engine, *, companies: int, cards: int) -> dict[str, Select[Any]]:
    with engine.connect() as connection:
        busy_company = connection.scalar(
            select(BusinessCard.company_id)
            .where(BusinessCard.company_id.is_not(None))
            .group_by(BusinessCard.company_id)
            .order_by(func.count().desc())
            .limit(1)
        )
        card_cursor = connection.execute(
            select(BusinessCard.uploaded_at, BusinessCard.id)
            .order_by(BusinessCard.uploaded_at, BusinessCard.id)
            .offset(cards // 2)
            .limit(1)
        ).one()
        company_cursor = connection.execute(
            select(Company.display_name, Company.id)
            .order_by(Company.display_name, Company.id)
            .offset(companies // 2)
            .limit(1)
        ).one()

    def company_page(
        *, q: str | None = None, has_profile: bool | None = None, page: int = 1
    ) -> Select[Any]:
        conditions = company_repo._list_conditions(q=q, has_profile=has_profile)
        return (
            company_repo._company_rows()
            .where(*conditions)
            .order_by(Company.display_name, Company.id)
            .offset((page - 1) * 20)
            .limit(20)
        )

    def company_count(q: str | None) -> Select[Any]:
        return (
            select(func.count())
            .select_from(Company)
            .outerjoin(CompanyProfile, CompanyProfile.company_id == Company.id)
            .where(*company_repo._list_conditions(q=q, has_profile=None))
        )

    uploaded_at: datetime = card_cursor.uploaded_at
    card_id: uuid.UUID = card_cursor.id
    return {
        "companies: page 1": company_page(),
        "companies: page 50": company_page(page=50),
        "companies: q=logistics": company_page(q="logistics"),
        "companies: count q=logistics": company_count("logistics"),
        "companies: has_profile=true": company_page(has_profile=True),
        "company detail: contacts": (
            select(BusinessCard)
            .where(BusinessCard.company_id == busy_company)
            .order_by(BusinessCard.uploaded_at.desc(), BusinessCard.id.desc())
        ),
        "enrich: active item of a company": select(EnrichJobItem.job_id).where(
            EnrichJobItem.company_id == busy_company,
            EnrichJobItem.status.in_(ACTIVE_JOB_ITEM_STATUSES),
        ),
        "export cards: batch mid-table": export._cards_select(None)
        .where(tuple_(BusinessCard.uploaded_at, BusinessCard.id) > (uploaded_at, card_id))
        .limit(200),
        "export companies: batch mid-table": export._companies_select()
        .where(
            tuple_(Company.display_name, Company.id)
            > (company_cursor.display_name, company_cursor.id)
        )
        .limit(200),
        "cards list (Q): page 1": select(BusinessCard)
        .order_by(BusinessCard.uploaded_at.desc(), BusinessCard.id.desc())
        .limit(20),
    }


def plan_summary(node: dict[str, Any]) -> list[str]:
    steps: list[str] = []
    kind = node["Node Type"]
    if "Scan" in kind:
        target = node.get("Index Name") or node.get("Relation Name") or ""
        steps.append(f"{kind} {target}".strip())
    elif "Sort" in kind:
        steps.append(kind)
    for child in node.get("Plans", []):
        steps.extend(plan_summary(child))
    return steps


def measure(engine: Engine, statement: Select[Any], runs: int) -> tuple[float, str]:
    compiled = statement.compile(
        dialect=engine.dialect, compile_kwargs={"render_postcompile": True}
    )
    sql = f"EXPLAIN (ANALYZE, FORMAT JSON) {compiled}"
    timings: list[float] = []
    plan: dict[str, Any] = {}
    with engine.connect() as connection:
        for _ in range(runs):
            [report] = connection.exec_driver_sql(sql, compiled.params).scalar_one()
            timings.append(report["Execution Time"])
            plan = report["Plan"]
    steps = list(dict.fromkeys(plan_summary(plan)))
    return statistics.median(timings), " → ".join(steps)


def run(args: argparse.Namespace) -> int:
    url = tuning_url(args.url)
    recreate_database(url)
    engine = create_engine(url, poolclass=NullPool)
    try:
        seed(engine, companies=args.companies, cards=args.cards)
        statements = probes(engine, companies=args.companies, cards=args.cards)
        baseline = {name: measure(engine, stmt, args.runs) for name, stmt in statements.items()}
        with engine.begin() as connection:
            for ddl in CANDIDATES.values():
                connection.exec_driver_sql(ddl)
            connection.exec_driver_sql("ANALYZE")
        tuned = {name: measure(engine, stmt, args.runs) for name, stmt in statements.items()}
    finally:
        engine.dispose()
        if not args.keep:
            drop_database(url)

    print(f"Dữ liệu: {args.cards} danh thiếp, {args.companies} công ty, median {args.runs} lượt")
    print("| Truy vấn | Hiện tại (ms) | Có index ứng viên (ms) | Plan hiện tại | Plan có index |")
    print("|---|---:|---:|---|---|")
    for name in statements:
        before, before_plan = baseline[name]
        after, after_plan = tuned[name]
        print(f"| {name} | {before:.2f} | {after:.2f} | {before_plan} | {after_plan} |")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Đo truy vấn F2 trên dữ liệu giả, trước và sau khi thêm index ứng viên (9.8)."
    )
    parser.add_argument("--url", help="URL Postgres; mặc định lấy DATABASE_URL, đổi tên DB")
    parser.add_argument("--cards", type=int, default=20000)
    parser.add_argument("--companies", type=int, default=3000)
    parser.add_argument("--runs", type=int, default=7)
    parser.add_argument("--keep", action="store_true", help="giữ lại database đo sau khi chạy")
    return run(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
