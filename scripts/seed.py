"""Nạp dữ liệu mẫu cho demo rồi đánh chỉ mục vào Knowledge Base.

Chủ sở hữu: Q | Task: 9.2 (bộ chấm A6) + 11.2 (bộ demo) + 12.8 (gắn người dùng) | xem Task.md

Chạy trong Docker (cần `db` và `embedder` phân giải được):

    docker compose exec api python -m scripts.seed
    docker compose exec api python -m scripts.seed --reset    # nạp lại từ đầu

**Từ D12 mọi dữ liệu đều thuộc về một tài khoản, nên script phải biết nạp cho AI (task 12.8).**
Mặc định là `demo@bizcard.local` / `demo12345`: chưa có thì script **tạo** và in rõ hai dòng đăng
nhập ra màn hình; có rồi thì nạp thêm vào đúng tài khoản đó và **không** đổi mật khẩu. Nạp cho
tài khoản khác:

    docker compose exec api python -m scripts.seed --user tung@example.com --password matkhau123

Vì sao có mật khẩu mặc định *và* nó không phải lỗ hổng: bộ seed chỉ chạy bằng tay trên máy dev
(`docker compose exec`), còn bản triển khai ở D13 dựng từ máy sạch và người dùng tự đăng ký —
không lượt deploy nào gọi script này. Mật khẩu vẫn đi qua đúng luật của `schemas/user.py`, nên
không có đường nào tạo được tài khoản yếu hơn mức mà form đăng ký cho phép.

**Hai bộ dữ liệu, hai mục đích khác nhau — đừng lẫn.**

| Lệnh | Nạp gì | Để làm gì |
|------|--------|-----------|
| `python -m scripts.seed` | 3 công ty + 4 danh thiếp *hư cấu* của `docs/qa-testset.md` | Chấm tiêu chí **A6**; runbook mục 2 bước 4 |
| `python -m scripts.seed --demo` | 7 thẻ **thật** của `samples/demo/` ở **trạng thái cuối buổi demo** | Task **11.2**: lưới an toàn khi mạng/OAuth hỏng |

Bộ `--demo` dựng lại đúng thứ người xem đáng lẽ thấy ở phút 9 của buổi demo — thẻ đã xác nhận,
công ty đã gộp, hồ sơ đã có nguồn — mà **không gọi một lời nào tới model**. Mất mạng giữa buổi,
tài khoản dính `429`, hay OAuth hết hạn thì vẫn còn đường đi tiếp: một lệnh là có lại toàn bộ
màn hình để nói. Đây là lưới an toàn *thứ hai*, sau video dự phòng của task 11.4.

⚠️ **Nạp `--demo` rồi thì không quét lại được đúng những tấm ảnh đó** — hệ thống chặn trùng ảnh
theo `image_hash`, mà script này băm đúng cách `POST /api/cards/upload` băm. Muốn diễn lại phần
quét trực tiếp thì dọn trước bằng `samples/demo/reset_demo.sql` (runbook mục 2 bước 5) hoặc
`--reset --demo`. Nói cách khác: **chạy `--demo` khi đã quyết định bỏ phần quét trực tiếp**, đừng
chạy nó trong lúc chuẩn bị cho một buổi demo chạy thật.

Hồ sơ doanh nghiệp trong bộ `--demo` lấy từ `scripts/demo_profiles.json` — **ảnh chụp kết quả
enrich thật**, mọi trường kèm `sources` do chính lượt chạy đó trả về, không một dòng bịa (rủi ro
**R4**). Chụp lại sau khi enrich thật một lượt:

    docker compose exec api python -m scripts.seed --capture-profiles

(`-m` chứ không phải `python scripts/seed.py`: chạy thẳng một file thì Python đặt `sys.path[0]`
thành `/app/scripts`, mà gói `app` lẫn gói `scripts` đều không nằm ở đó. `-m` chạy từ `/app` nên
cả hai import được, khỏi phải nhớ `PYTHONPATH`.)

**Vì sao task này tồn tại.** `docs/qa-testset.md` (8.7 của T) chấm điểm tiêu chí **A6** trên
một bộ dữ liệu cố định — 4 danh thiếp + 3 hồ sơ, tiếng Việt/Hàn/Nhật. Bộ đó nằm trong
`scripts/eval_retrieval.py::seed()`, nhưng script ấy **rollback** ở cuối để không để lại rác
trong DB đang dùng. Hệ quả đo được ở D8: trợ lý chạy thật *không hề thấy* dữ liệu mà bộ câu hỏi
nói tới, nên 10 câu tính điểm chưa bao giờ chấm được. Script này là mảnh còn thiếu: cùng bộ dữ
liệu đó, nhưng **commit**.

**Dùng lại `eval_retrieval.seed()` chứ không chép sang đây.** Hai bộ dữ liệu song song là kịch
bản hỏng kinh điển: người ta sửa một bên, đo trên bên kia, rồi kết luận về một hệ thống không
tồn tại. Recall đã đo ở 7.4 trên chính bộ này, nên câu nào trượt là biết ngay lỗi nằm ở truy hồi
hay ở prompt.

**Ba điều đáng nêu:**

1. **Không nạp chồng.** Chạy hai lần mà không kiểm là DB có hai bản "Công ty CP Sữa Mộc Châu",
   đúng kịch bản I-24 (bản ghi trùng chiếm hết top-k, ngữ cảnh hẹp lại) — lần này do chính ta
   tự tạo ra. Mặc định: thấy dữ liệu mẫu cũ thì **dừng**, không ghi gì.
2. **`--reset` xoá có mục tiêu, không `TRUNCATE`.** Chỉ xoá đúng 3 công ty và 4 danh thiếp của
   bộ mẫu (nhận theo khoá nghiệp vụ), kèm chunk KB của chúng. Xoá sạch bảng thì mọi danh thiếp
   người dùng đã quét thật trong lúc dev cũng bay theo — script tiện tay không được phép làm
   điều đó.
3. **`REINDEX` ở cuối, bắt buộc.** Index `ivfflat` học phân cụm từ dữ liệu *có sẵn lúc tạo
   index*; revision `0003` chạy khi `kb_chunks` còn rỗng nên centroid vô nghĩa (I-22). Bỏ bước
   này thì seed xong tìm kiếm vẫn gần như không ra gì, mà **không có lỗi nào báo**.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import uuid
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models.card import BusinessCard, CardStatus
from app.models.company import Company, CompanyProfile
from app.models.kb import KBChunk
from app.models.user import User
from app.models.workspace import WorkspaceMember
from app.repositories import card as card_repo
from app.repositories import kb as kb_repo
from app.repositories import user as user_repo
from app.routers import cards as cards_router
from app.services import auth, company_matching, kb, normalize
from app.services import image as image_service
from app.services.normalize_company import normalize_company_name
from scripts.eval_retrieval import SEED_CARD_EMAILS, SEED_COMPANY_NAMES, seed

#: Tài khoản nhận dữ liệu seed khi không truyền `--user`. Xem docstring đầu file về lý do có mật
#: khẩu mặc định.
DEFAULT_SEED_EMAIL = "demo@bizcard.local"
DEFAULT_SEED_PASSWORD = "demo12345"

#: Khoá nhận dạng công ty mẫu. Cùng hàm chuẩn hoá mà `upsert_company()` của T dùng, nên nhận ra
#: được cả bản ghi do lượt seed trước tạo lẫn bản do người dùng xác nhận danh thiếp tạo ra.
SEED_COMPANY_KEYS: tuple[str, ...] = tuple(
    normalize_company_name(name) for name in SEED_COMPANY_NAMES
)

#: Bộ thẻ demo của T (task 11.7). Chỉ **đọc**, không sửa — `samples/` thuộc quyền T.
REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = REPO_ROOT / "samples" / "demo"
DEMO_CARDS_JSON = DEMO_DIR / "cards.json"

#: Ảnh chụp **kết quả enrich thật** của bộ demo, để máy không có mạng/OAuth vẫn dựng lại được
#: trạng thái cuối buổi demo. File của Q (đi liền `seed.py`), sinh bằng `--capture-profiles`.
DEMO_PROFILES_JSON = Path(__file__).resolve().parent / "demo_profiles.json"


async def ensure_user(db: AsyncSession, email: str, password: str) -> User:
    """Tài khoản nhận dữ liệu seed: có thì dùng lại, chưa có thì tạo (task 12.8).

    Đi qua `services/auth.register()` của T chứ không `INSERT` thẳng: mật khẩu phải được băm
    Argon2 đúng cách và đi qua đúng bộ luật của `schemas/user.py`. Tự chèn một hàng `users` ở đây
    là dựng ra một tài khoản mà form đăng nhập không mở được, và lỗi ấy chỉ lộ ra giữa buổi demo.

    **Không đổi mật khẩu của tài khoản đã có.** Truyền `--password` khác cho một email đang tồn
    tại thì script dùng tài khoản cũ và nói rõ điều đó: một script nạp dữ liệu mẫu không được
    phép thay mật khẩu của người khác.
    """
    existing = await user_repo.get_by_email(db, email.strip().lower())
    if existing is not None:
        print(f"Nạp vào tài khoản đã có: {existing.email}")
        return existing

    user = await auth.register(db, email=email, password=password, display_name="Tài khoản demo")
    print(
        f"Đã tạo tài khoản {user.email} (mật khẩu: {password}) — đăng nhập tại /auth/login "
        "rồi đổi mật khẩu ở /account nếu cần."
    )
    return user


def load_demo_cards() -> list[dict[str, Any]]:
    """Đọc `samples/demo/cards.json`. Thiếu file thì nói rõ ai làm task nào."""
    if not DEMO_CARDS_JSON.exists():
        raise FileNotFoundError(
            f"Không thấy {DEMO_CARDS_JSON} — bộ thẻ demo là task 11.7 của T, chưa có thì "
            "không seed được dữ liệu demo."
        )
    cards = json.loads(DEMO_CARDS_JSON.read_text(encoding="utf-8"))
    if not isinstance(cards, list) or not cards:
        raise ValueError(f"{DEMO_CARDS_JSON} không phải danh sách thẻ hợp lệ.")
    return cards


def demo_card_emails() -> tuple[str, ...]:
    """Khoá nhận dạng thẻ demo — đọc từ chính `cards.json` thay vì chép lại danh sách.

    Chép sang đây là mở đường cho hai bên lệch nhau trong im lặng: T thêm một thẻ thứ 8 thì
    bước chống-nạp-chồng bên dưới sẽ bỏ sót đúng thẻ đó.
    """
    return tuple(str(entry["email"]).lower() for entry in load_demo_cards())


async def _workspace_of(db: AsyncSession, user: User) -> uuid.UUID:
    """Không gian của người dùng khởi tạo. Seed luôn ghi vào đúng không gian ấy (task NEXT-05).

    `ensure_user()` tạo tài khoản mới thì revision `0015` đã cho họ một không gian riêng; người
    đã có sẵn thì lấy không gian đầu tiên họ có chân.
    """
    workspace_id = await db.scalar(
        select(WorkspaceMember.workspace_id)
        .where(WorkspaceMember.user_id == user.id)
        .order_by(WorkspaceMember.joined_at)
        .limit(1)
    )
    if workspace_id is None:
        raise RuntimeError(f"Tài khoản {user.email} chưa ở không gian làm việc nào.")
    return workspace_id


async def seed_demo(
    db: AsyncSession, *, workspace_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[list[BusinessCard], list[CompanyProfile]]:
    """Dựng lại **trạng thái cuối buổi demo**: thẻ đã xác nhận + công ty + hồ sơ có nguồn.

    Đi đúng đường mà giao diện đi — băm ảnh gốc, tiền xử lý, `upsert_company()` của T — chứ
    không `INSERT` thẳng. Nhờ vậy dữ liệu seed *không phân biệt được* với dữ liệu quét thật:
    hai thẻ Hòa Phát vẫn gộp về một công ty, cặp Samsung vẫn chỉ được gợi ý chứ không gộp, và
    upload lại đúng tấm ảnh đó vẫn bị chặn trùng (đúng lý do `samples/demo/reset_demo.sql` tồn
    tại — xem `docs/demo-runbook.md` mục 2 bước 5).
    """
    cards: list[BusinessCard] = []

    for entry in load_demo_cards():
        image_file = DEMO_DIR / str(entry["file"])
        if not image_file.exists():
            print(f"  bỏ qua {entry['file']}: không có file ảnh", file=sys.stderr)
            continue

        raw = image_file.read_bytes()
        # Băm ảnh **gốc**, lưu ảnh **đã xử lý** — đúng thứ tự của `POST /api/cards/upload`.
        image_hash = hashlib.sha256(raw).hexdigest()
        processed = image_service.preprocess(raw)
        relative_path = cards_router._store(processed.data, image_hash)

        language = str(entry.get("language") or "") or None
        fields = normalize.normalize_card_fields(
            {
                "full_name": entry.get("full_name"),
                "job_title": entry.get("job_title"),
                "company_name_raw": entry.get("company"),
                "email": entry.get("email"),
                "phone": entry.get("phone"),
                "address": entry.get("address"),
                "website": entry.get("website"),
                "language_detected": language,
            },
            language=language,
        )

        card = await card_repo.create_card(
            db,
            workspace_id=workspace_id,
            user_id=user_id,
            image_path=relative_path,
            image_hash=image_hash,
            fields=fields,
            # Gắn nhãn rõ đây là dữ liệu seed. Không bịa một `ocr_raw_json` trông như model trả
            # về: màn hình review in thẳng khối JSON này ra, người xem phải phân biệt được.
            ocr_raw_json={
                "_seed": "samples/demo/cards.json",
                "_note": "dữ liệu seed cho demo, KHÔNG phải kết quả model trả về",
                "shows": entry.get("shows"),
            },
            status=CardStatus.NEEDS_REVIEW,
        )

        card.company_id = await company_matching.upsert_company(
            db,
            str(entry["company"]),
            workspace_id=workspace_id,
            email=fields.get("email"),
            website=fields.get("website"),
        )
        card.status = CardStatus.CONFIRMED
        await db.commit()
        await db.refresh(card)
        cards.append(card)

    profiles = await _seed_demo_profiles(db, workspace_id=workspace_id, creator_id=user_id)

    for card in cards:
        await kb.ingest_card(db, card)
    for profile in profiles:
        await kb.ingest_company_profile(db, profile)

    return cards, profiles


async def _seed_demo_profiles(
    db: AsyncSession, *, workspace_id: uuid.UUID, creator_id: uuid.UUID
) -> list[CompanyProfile]:
    """Nạp hồ sơ doanh nghiệp từ ảnh chụp kết quả enrich thật (`scripts/demo_profiles.json`).

    Không có file thì **bỏ qua chứ không bịa**: hồ sơ không nguồn đúng là thứ rủi ro **R4** cấm.
    Trạng thái demo khi đó dừng ở "đã quét, chưa có hồ sơ" — vẫn demo được phần F1 và F3.
    """
    if not DEMO_PROFILES_JSON.exists():
        print(
            f"  chưa có {DEMO_PROFILES_JSON.name} → seed không kèm hồ sơ doanh nghiệp.\n"
            "  Sinh bằng: docker compose exec api python -m scripts.seed --capture-profiles",
            file=sys.stderr,
        )
        return []

    payload = json.loads(DEMO_PROFILES_JSON.read_text(encoding="utf-8"))
    profiles: list[CompanyProfile] = []

    for item in payload.get("profiles", []):
        key = normalize_company_name(str(item["company"]))
        company = (
            await db.execute(
                select(Company).where(
                    Company.workspace_id == workspace_id, Company.name_normalized == key
                )
            )
        ).scalar_one_or_none()
        if company is None:
            print(f"  bỏ qua hồ sơ {item['company']}: không có công ty tương ứng", file=sys.stderr)
            continue

        generated_at = item.get("generated_at")
        profile = CompanyProfile(
            workspace_id=workspace_id,
            user_id=creator_id,
            company_id=company.id,
            legal_name=item.get("legal_name"),
            tax_code=item.get("tax_code"),
            founded_year=item.get("founded_year"),
            size_label=item.get("size_label"),
            employee_range=item.get("employee_range"),
            industry=item.get("industry"),
            products=item.get("products"),
            address=item.get("address"),
            website=item.get("website"),
            phone=item.get("phone"),
            email=item.get("email"),
            description=item.get("description"),
            sources=item.get("sources"),
            llm_model=item.get("llm_model") or payload.get("llm_model"),
            generated_at=datetime.fromisoformat(generated_at) if generated_at else None,
            status=item.get("status") or "generated",
        )
        db.add(profile)
        profiles.append(profile)

    await db.commit()
    for profile in profiles:
        await db.refresh(profile)
    return profiles


async def capture_profiles(db: AsyncSession, *, workspace_id: uuid.UUID) -> int:
    """Ghi hồ sơ đang có trong DB của các công ty demo ra `scripts/demo_profiles.json`.

    Chạy **sau** khi đã enrich thật một lượt. Cách dùng đầy đủ ở docstring đầu file.
    """
    keys = [normalize_company_name(str(entry["company"])) for entry in load_demo_cards()]
    rows = (
        await db.execute(
            select(Company, CompanyProfile)
            .join(CompanyProfile, CompanyProfile.company_id == Company.id)
            .where(Company.workspace_id == workspace_id, Company.name_normalized.in_(keys))
            .order_by(Company.display_name)
        )
    ).all()

    profiles = []
    for company, profile in rows:
        if profile.status == "draft":
            continue
        profiles.append(
            {
                "company": company.display_name,
                "legal_name": profile.legal_name,
                "tax_code": profile.tax_code,
                "founded_year": profile.founded_year,
                "size_label": profile.size_label,
                "employee_range": profile.employee_range,
                "industry": profile.industry,
                "products": profile.products,
                "address": profile.address,
                "website": profile.website,
                "phone": profile.phone,
                "email": profile.email,
                "description": profile.description,
                "sources": profile.sources,
                "llm_model": profile.llm_model,
                "generated_at": profile.generated_at.isoformat() if profile.generated_at else None,
                "status": profile.status,
            }
        )

    payload = {
        "_note": (
            "Ảnh chụp kết quả enrich THẬT của bộ thẻ demo (samples/demo/cards.json). "
            "Mọi trường đều kèm `sources` do chính lượt chạy đó trả về — không sửa tay. "
            "Sinh lại bằng: python -m scripts.seed --capture-profiles"
        ),
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "llm_model": settings.llm_model,
        "profiles": profiles,
    }
    DEMO_PROFILES_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return len(profiles)


async def existing_ids(
    db: AsyncSession,
    company_keys: Sequence[str] = SEED_COMPANY_KEYS,
    card_emails: Sequence[str] = SEED_CARD_EMAILS,
    *,
    workspace_id: uuid.UUID,
) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """(id công ty mẫu, id danh thiếp mẫu) đang có **trong không gian này**.

    Lọc theo `workspace_id` (task 12.8, đổi khoá ở `NEXT-05`): bước chống-nạp-chồng bên dưới
    chỉ được nhìn dữ liệu của chính không gian đang nạp. Đếm toàn cục thì T nạp bộ demo cho
    không gian của mình sẽ nhận câu "đã có dữ liệu mẫu" chỉ vì Q đã nạp cho không gian khác,
    và `--reset` còn xoá đúng dữ liệu của bên kia.
    """
    companies = list(
        (
            await db.execute(
                select(Company.id).where(
                    Company.workspace_id == workspace_id, Company.name_normalized.in_(company_keys)
                )
            )
        )
        .scalars()
        .all()
    )
    cards = list(
        (
            await db.execute(
                select(BusinessCard.id).where(
                    BusinessCard.workspace_id == workspace_id,
                    BusinessCard.email.in_(card_emails),
                )
            )
        )
        .scalars()
        .all()
    )
    return companies, cards


async def purge(
    db: AsyncSession, company_ids: Sequence[uuid.UUID], card_ids: Sequence[uuid.UUID]
) -> None:
    """Xoá dữ liệu mẫu cũ **và** chunk KB của nó.

    Phải xoá chunk bằng tay: `kb_chunks` cố ý không có khoá ngoại (`source_id` trỏ tới hai bảng
    khác nhau tuỳ `source_type`), nên `DELETE` bảng gốc không kéo theo gì — đúng vấn đề I-25.
    Bỏ bước này thì trợ lý vẫn trích dẫn hồ sơ của những công ty vừa bị xoá.
    """
    source_ids = [*company_ids, *card_ids]
    if source_ids:
        await db.execute(delete(KBChunk).where(KBChunk.source_id.in_(source_ids)))
    if card_ids:
        await db.execute(delete(BusinessCard).where(BusinessCard.id.in_(card_ids)))
    if company_ids:
        # `company_profiles` có FK ON DELETE CASCADE → hồ sơ đi theo công ty, không cần xoá riêng.
        await db.execute(delete(Company).where(Company.id.in_(company_ids)))


async def run_demo(reset: bool, *, email: str, password: str) -> int:
    """`--demo`: dựng lại trạng thái cuối buổi demo từ `samples/demo/` (task 11.2)."""
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            user = await ensure_user(db, email, password)
            keys = [normalize_company_name(str(e["company"])) for e in load_demo_cards()]
            companies, cards = await existing_ids(
                db, keys, demo_card_emails(), workspace_id=await _workspace_of(db, user)
            )
            if companies or cards:
                if not reset:
                    print(
                        f"Đã có dữ liệu demo trong DB ({len(companies)} công ty, {len(cards)} "
                        "danh thiếp). Không nạp chồng — chạy lại với `--reset --demo`."
                    )
                    return 1
                print(f"--reset: xoá {len(companies)} công ty + {len(cards)} danh thiếp demo cũ")
                await purge(db, companies, cards)
                await db.commit()

            workspace_id = await _workspace_of(db, user)
            seeded_cards, profiles = await seed_demo(db, workspace_id=workspace_id, user_id=user.id)
            await db.commit()

            # Xem điểm 3 ở đầu file — bỏ bước này thì trợ lý gần như không tìm ra gì.
            await kb_repo.rebuild_vector_index(db)
            await db.commit()

            company_count = len({card.company_id for card in seeded_cards if card.company_id})
            print(
                f"Đã nạp {len(seeded_cards)} danh thiếp demo → {company_count} công ty "
                f"+ {len(profiles)} hồ sơ, đã REINDEX."
            )
            print(
                "Thử ngay: mở /companies rồi bấm bong bóng trợ lý, hỏi 'Mã số thuế của Vinamilk là gì?'"
            )
    finally:
        await engine.dispose()
    return 0


async def run_capture(*, email: str, password: str) -> int:
    """`--capture-profiles`: chụp lại hồ sơ enrich thật của bộ demo ra file fixture."""
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            user = await ensure_user(db, email, password)
            written = await capture_profiles(db, workspace_id=await _workspace_of(db, user))
    finally:
        await engine.dispose()

    if not written:
        print(
            "Không có hồ sơ nào để chụp — chạy enrich cho các công ty demo trước "
            "(/companies → tích chọn → Tạo hồ sơ doanh nghiệp).",
            file=sys.stderr,
        )
        return 1
    print(f"Đã ghi {written} hồ sơ vào {DEMO_PROFILES_JSON}")
    return 0


async def run(reset: bool, *, email: str, password: str) -> int:
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            user = await ensure_user(db, email, password)
            companies, cards = await existing_ids(db, workspace_id=await _workspace_of(db, user))
            if companies or cards:
                if not reset:
                    print(
                        f"Đã có dữ liệu mẫu trong DB ({len(companies)} công ty, {len(cards)} "
                        "danh thiếp). Không nạp chồng — chạy lại với `--reset` để nạp lại."
                    )
                    return 1
                print(f"--reset: xoá {len(companies)} công ty + {len(cards)} danh thiếp mẫu cũ")
                await purge(db, companies, cards)
                await db.commit()

            corpus = await seed(db, user_id=user.id)

            # Đối chiếu lại với dữ liệu vừa tạo: nếu ai đó đổi tên công ty trong
            # `eval_retrieval.py` mà quên sửa `SEED_COMPANY_NAMES` thì bước chống-nạp-chồng ở
            # trên sẽ âm thầm mất tác dụng. Thà chết ở đây còn hơn để lộ ra sau vài lượt seed.
            created = set(
                (
                    await db.execute(
                        select(Company.display_name).where(
                            Company.user_id == user.id, Company.id.in_(corpus.labels)
                        )
                    )
                )
                .scalars()
                .all()
            )
            drift = created - set(SEED_COMPANY_NAMES)
            if drift:
                await db.rollback()
                print(
                    "LỖI: dữ liệu mẫu đã đổi nhưng SEED_COMPANY_NAMES chưa cập nhật: "
                    f"{sorted(drift)}",
                    file=sys.stderr,
                )
                return 2

            await db.commit()

            # Xem điểm 3 ở đầu file — không có bước này thì tìm kiếm vector gần như không ra gì.
            await kb_repo.rebuild_vector_index(db)
            await db.commit()

            chunks = sum(len(document.chunks) for document in corpus.documents)
            print(
                f"Đã nạp {len(SEED_COMPANY_NAMES)} công ty + {len(SEED_CARD_EMAILS)} danh thiếp "
                f"→ {len(corpus.documents)} nguồn / {chunks} chunk KB, đã REINDEX."
            )
            print(
                "Thử ngay: bấm bong bóng trợ lý góc phải dưới, hỏi 'Công ty nào làm về logistics?'"
            )
    finally:
        await engine.dispose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Nạp dữ liệu mẫu cho demo (task 9.2 / 11.2)")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Xoá dữ liệu mẫu của lượt trước rồi nạp lại (chỉ đụng đúng bộ mẫu)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Nạp bộ demo của `samples/demo/` ở trạng thái cuối buổi (task 11.2) thay vì bộ A6",
    )
    parser.add_argument(
        "--capture-profiles",
        action="store_true",
        help="Chụp hồ sơ enrich thật của bộ demo ra scripts/demo_profiles.json rồi thoát",
    )
    parser.add_argument(
        "--user",
        default=DEFAULT_SEED_EMAIL,
        help=f"Email tài khoản nhận dữ liệu (mặc định {DEFAULT_SEED_EMAIL}); chưa có thì tạo",
    )
    parser.add_argument(
        "--password",
        default=DEFAULT_SEED_PASSWORD,
        help="Mật khẩu dùng KHI PHẢI TẠO tài khoản mới; tài khoản đã có thì bỏ qua",
    )
    args = parser.parse_args()
    account = {"email": args.user, "password": args.password}
    if args.capture_profiles:
        return asyncio.run(run_capture(**account))
    if args.demo:
        return asyncio.run(run_demo(reset=args.reset, **account))
    return asyncio.run(run(reset=args.reset, **account))


if __name__ == "__main__":
    raise SystemExit(main())
