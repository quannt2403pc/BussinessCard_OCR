"""Chụp lại 8 ảnh minh hoạ của `docs/user-guide.md` (task 14.11).

Chủ sở hữu: T | Task: 14.11, sửa ở EX-09 (Q) | xem Task.md

    pip install playwright && playwright install chromium     # chỉ máy dev, KHÔNG vào requirements.txt
    docker compose exec api python -m scripts.seed --demo --user anh@bizcard.local --password <mk>
    python -m scripts.make_doc_screenshots --user anh@bizcard.local

Hai điều đáng biết trước khi sửa file này:

* **Không gõ mật khẩu vào trình duyệt.** Phiên đăng nhập là cookie ký bằng chính
  `services/auth.py`, nạp thẳng vào context của Playwright.
* **Đọc DB bằng SQLAlchemy đồng bộ.** Trên Windows, psycopg async đòi `SelectorEventLoop` còn
  Playwright đòi `ProactorEventLoop` — hai thứ không sống chung trong một tiến trình.
"""

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from playwright._impl._api_structures import SetCookieParam
from playwright.async_api import Page, async_playwright
from playwright.async_api import ViewportSize as Viewport
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.card import BusinessCard, CardStatus
from app.models.company import Company, CompanyProfile
from app.models.user import User
from app.services import auth

OUT_DIR = Path("docs/images")
VIEWPORT: Viewport = {"width": 1280, "height": 860}
SETTLE_MS = 900


@dataclass(frozen=True)
class Shot:
    name: str
    path: str
    full_page: bool = False
    #: Mở bong bóng trợ lý trước khi chụp. Từ `EX-09` trợ lý **không còn là một trang**, nên ảnh
    #: của nó phải chụp một trang bất kỳ đang mở panel. Thiếu cờ này thì `/assistant` chỉ `301`
    #: về `/` và ta lưu ảnh trang chủ dưới cái tên `07-assistant.png` — hỏng mà không báo gì.
    open_assistant: bool = False


def ids_for(db: Session, user: User) -> dict[str, str]:
    card = db.scalar(
        select(BusinessCard)
        .where(BusinessCard.user_id == user.id, BusinessCard.status == CardStatus.CONFIRMED.value)
        .order_by(BusinessCard.uploaded_at)
        .limit(1)
    )
    company = db.scalar(
        select(Company)
        .join(CompanyProfile, CompanyProfile.company_id == Company.id)
        .where(Company.user_id == user.id)
        .order_by(Company.display_name)
        .limit(1)
    )
    if card is None or company is None:
        raise SystemExit(
            "Tài khoản chưa có dữ liệu demo. Chạy trước:\n"
            "  docker compose exec api python -m scripts.seed --demo --user <email> --password <mk>"
        )
    return {"card": str(card.id), "company": str(company.id)}


def shots(ids: dict[str, str]) -> list[Shot]:
    return [
        Shot("01-settings.png", "/settings"),
        Shot("02-upload.png", "/cards/upload"),
        Shot("03-review.png", f"/cards/{ids['card']}", full_page=True),
        Shot("04-cards-list.png", "/cards"),
        Shot("05-companies.png", "/companies"),
        Shot("06-company-detail.png", f"/companies/{ids['company']}", full_page=True),
        Shot("07-assistant.png", "/", open_assistant=True),
        Shot("08-home.png", "/"),
    ]


async def capture(page: Page, base_url: str, shot: Shot, out_dir: Path) -> None:
    response = await page.goto(base_url + shot.path, wait_until="networkidle")
    if response is not None and response.status >= 400:
        raise SystemExit(f"{shot.path} trả {response.status} — phiên đăng nhập hỏng?")
    await page.wait_for_timeout(SETTLE_MS)
    if shot.open_assistant:
        await page.click('[data-w="toggle"]')
        await page.wait_for_selector("#assistant-panel:not([hidden])")
        await page.wait_for_timeout(SETTLE_MS)
    target = out_dir / shot.name
    await page.screenshot(path=target, full_page=shot.full_page)
    print(f"{shot.name:<24} {shot.path:<46} {target.stat().st_size / 1024:6.1f} KB")


def session_cookie(args: argparse.Namespace) -> tuple[SetCookieParam, list[Shot]]:
    """Đọc DB **đồng bộ**: psycopg async đòi `SelectorEventLoop`, còn Playwright trên Windows
    đòi `ProactorEventLoop` — hai thứ không sống chung trong một tiến trình."""
    engine = create_engine(args.database_url)
    try:
        with Session(engine) as db:
            user = db.scalar(select(User).where(User.email == args.user))
            if user is None:
                raise SystemExit(f"Không có tài khoản {args.user}.")
            # Cookie ký bằng chính `services/auth.py` — không gõ mật khẩu vào trình duyệt.
            cookie: SetCookieParam = {
                "name": auth.SESSION_COOKIE,
                "value": auth.sign_session(user),
                "domain": urlparse(args.base_url).hostname or "localhost",
                "path": "/",
            }
            return cookie, shots(ids_for(db, user))
    finally:
        engine.dispose()


async def run(args: argparse.Namespace) -> int:
    cookie, targets = session_cookie(args)

    args.out.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as driver:
        browser = await driver.chromium.launch()
        context = await browser.new_context(viewport=VIEWPORT, locale="vi-VN")
        await context.add_cookies([cookie])
        page = await context.new_page()
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        try:
            for shot in targets:
                await capture(page, args.base_url.rstrip("/"), shot, args.out)
        finally:
            await context.close()
            await browser.close()

    if errors:
        print("\nLỗi console gặp khi chụp:", *errors, sep="\n  ")
    return 1 if errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Chụp lại ảnh minh hoạ cho docs/user-guide.md (task 14.11)"
    )
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--user", default="demo@bizcard.local")
    parser.add_argument(
        "--database-url",
        default="postgresql+psycopg://bizcard:change-me@localhost:5432/bizcard",
        help="DB nhìn từ máy host (cổng 5432 đã publish trong docker-compose)",
    )
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
