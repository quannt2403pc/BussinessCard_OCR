import argparse
import asyncio
import re
import statistics
import sys
import time
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.schemas.company import CompanyProfileOut
from app.services.enrichment import enrich_company

A5_FIELDS: dict[str, tuple[str, ...]] = {
    "MST": ("tax_code",),
    "Quy mô": ("size_label", "employee_range"),
    "Ngành nghề": ("industry",),
    "Sản phẩm": ("products",),
    "Địa chỉ": ("address",),
}

COMPANIES: tuple[str, ...] = (
    "Công ty Cổ phần FPT",
    "Công ty Cổ phần Sữa Việt Nam",
    "Công ty Cổ phần Tập đoàn Hòa Phát",
    "Ngân hàng TMCP Ngoại thương Việt Nam",
    "Công ty Cổ phần Đầu tư Thế Giới Di Động",
    "Công ty TNHH Samsung Electronics Việt Nam",
    "Công ty Cổ phần Tập đoàn Masan",
    "Công ty Cổ phần Traphaco",
    "Công ty Cổ phần Vận tải và Xếp dỡ Hải An",
    "Công ty Cổ phần Xây dựng Coteccons",
)

FICTIONAL = "Công ty TNHH Kỹ thuật Lam Vân Zeta 2031"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}


@dataclass
class Result:
    name: str
    seconds: float
    profile: CompanyProfileOut | None
    error: str | None
    tax_check: str = "—"


async def measure(name: str, model: str) -> Result:
    started = time.perf_counter()
    try:
        profile = await enrich_company(name, model=model)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        return Result(name, time.perf_counter() - started, None, message[:140])
    return Result(name, time.perf_counter() - started, profile, None)


def sourced_fields(profile: CompanyProfileOut) -> set[str]:
    return profile.fields_with_value() - profile.fields_missing_source()


def a5_hits(profile: CompanyProfileOut) -> list[str]:
    sourced = sourced_fields(profile)
    return [label for label, fields in A5_FIELDS.items() if any(f in sourced for f in fields)]


def source_urls(profile: CompanyProfileOut) -> set[str]:
    return {ref.url for refs in profile.sources.values() for ref in refs}


async def check_tax_code(http: httpx.AsyncClient, profile: CompanyProfileOut) -> str:
    digits = re.sub(r"\D", "", profile.tax_code or "")
    refs = profile.sources.get("tax_code") or []
    if not digits or not refs:
        return "—"
    statuses = []
    for ref in refs:
        try:
            response = await http.get(ref.url)
        except httpx.HTTPError as exc:
            statuses.append(type(exc).__name__)
            continue
        if response.status_code >= 400:
            statuses.append(f"HTTP {response.status_code}")
            continue
        if digits in re.sub(r"\D", "", response.text):
            return "khớp nguồn"
        statuses.append("không thấy trong trang")
    return "không kiểm được (" + ", ".join(dict.fromkeys(statuses)) + ")"


def row(result: Result) -> str:
    if result.profile is None:
        return f"| {result.name} | {result.seconds:.0f} | — | — | — | — | lỗi: {result.error} |"
    hits = a5_hits(result.profile)
    missing = [label for label in A5_FIELDS if label not in hits]
    a5 = f"{len(hits)}/5" + (f" (thiếu {', '.join(missing)})" if missing else "")
    unverified = ", ".join(result.profile.unverified_fields) or "—"
    return (
        f"| {result.name} | {result.seconds:.0f} | {len(sourced_fields(result.profile))} | {a5} "
        f"| {len(source_urls(result.profile))} | `{result.profile.tax_code or '—'}` · "
        f"{result.tax_check} | {unverified} |"
    )


async def run(args: argparse.Namespace) -> int:
    model = args.model or settings.llm_model
    names = list(COMPANIES[: args.limit]) + [FICTIONAL]
    results: list[Result] = []
    async with httpx.AsyncClient(
        timeout=15, follow_redirects=True, headers=BROWSER_HEADERS
    ) as http:
        for index, name in enumerate(names):
            if index:
                await asyncio.sleep(args.delay)
            result = await measure(name, model)
            if result.profile is not None:
                result.tax_check = await check_tax_code(http, result.profile)
            print(f"[{index + 1}/{len(names)}] {name}: {result.seconds:.0f}s", file=sys.stderr)
            results.append(result)

    real = [r for r in results if r.name != FICTIONAL]
    ok = [r for r in real if r.profile is not None]
    fictional = next(r for r in results if r.name == FICTIONAL)
    at_least_five = sum(len(sourced_fields(r.profile)) >= 5 for r in ok if r.profile)
    full_a5 = sum(len(a5_hits(r.profile)) == 5 for r in ok if r.profile)
    matched = sum(r.tax_check == "khớp nguồn" for r in ok)
    fictional_fields = (
        len(sourced_fields(fictional.profile)) if fictional.profile else f"lỗi {fictional.error}"
    )

    print(f"Model: `{model}` · {len(real)} công ty thật + 1 công ty bịa\n")
    print(
        "| Công ty | Giây | Trường có nguồn | 5 trường A5 | Số nguồn | MST · kiểm với nguồn "
        "| Trường bị gỡ vì không có nguồn |"
    )
    print("|---|---:|---:|---|---:|---|---|")
    for result in results:
        print(row(result))
    print()
    print(f"- Gọi thành công: {len(ok)}/{len(real)}")
    print(f"- Có ≥ 5 trường có nguồn: {at_least_five}/{len(real)}")
    print(f"- Đủ cả 5 trường A5 có nguồn: {full_a5}/{len(real)}")
    print(f"- MST tìm thấy đúng trong trang nguồn: {matched}/{len(real)}")
    if ok:
        print(f"- Thời gian trung vị: {statistics.median(r.seconds for r in ok):.0f} giây/công ty")
    print(f"- Công ty bịa `{FICTIONAL}`: {fictional_fields} trường có nguồn")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Đo chất lượng hồ sơ doanh nghiệp (task 10.9)")
    parser.add_argument("--model", help="Model muốn đo; mặc định LLM_MODEL đang cấu hình")
    parser.add_argument("--limit", type=int, default=len(COMPANIES))
    parser.add_argument("--delay", type=float, default=3.0, help="Giây nghỉ giữa hai công ty")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
