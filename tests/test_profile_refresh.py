"""Làm mới hồ sơ doanh nghiệp: so bản mới với bản cũ, và chỉ báo phần khác.

Chủ sở hữu: T | Task: NEXT-06 | xem Task.md

Ba thứ quyết định tính năng này có dùng được hay không, cả ba đều hỏng theo kiểu **vẫn chạy**:

1. **Không so `description`.** Đó là văn xuôi model sinh ra, lần nào cũng khác chữ; đưa vào thì
   mọi lượt tra lại đều báo "có thay đổi" và người dùng thôi đọc từ lần thứ hai.
2. **Lượt tra lại không được xoá trắng dữ liệu đang có.** Hôm nay không tra ra mã số thuế không
   có nghĩa là công ty không còn mã số thuế.
3. **Lần tạo đầu tiên không phải lần làm mới.** So bản đầu với một hồ sơ nháp rỗng thì ra "mọi
   trường đều đổi" — đúng loại thông báo vô nghĩa làm hỏng cả tính năng.
"""

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.user import User
from app.repositories import company as company_repo
from app.schemas.company import CompanyProfileSchema, ProfileStatus
from app.services.normalize_company import normalize_company_name
from app.services.profile_diff import diff_profiles, merge_keeping_known
from tests.conftest import workspace_id_of

ClientFactory = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]

NOW = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)


def profile(**values: object) -> CompanyProfileSchema:
    base: dict[str, object] = {
        "legal_name": "Công ty Cổ phần FPT",
        "tax_code": "0101248141",
        "address": "Hà Nội",
        "industry": ["Công nghệ thông tin"],
    }
    base.update(values)
    return CompanyProfileSchema.model_validate(base)


async def make_company(
    db: AsyncSession,
    user: User,
    name: str = "FPT",
    *,
    values: CompanyProfileSchema | None = None,
    status: ProfileStatus = ProfileStatus.GENERATED,
    checked_at: datetime | None = None,
) -> Company:
    company = Company(
        id=uuid.uuid4(),
        workspace_id=await workspace_id_of(db, user),
        user_id=user.id,
        display_name=name,
        name_normalized=normalize_company_name(name),
        aliases=[name],
    )
    db.add(company)
    await db.flush()
    if values is not None:
        saved = await company_repo.save_profile(db, company.id, values, status=status)
        saved.last_checked_at = checked_at.replace(tzinfo=None) if checked_at else None
        await db.flush()
    return company


# --------------------------------------------------------------------------- so sánh


def test_ignores_wording_noise() -> None:
    """Hoa thường và khoảng trắng thừa không phải thay đổi."""
    old = profile(legal_name="Công ty Cổ phần FPT")
    new = profile(legal_name="  CÔNG TY  CỔ PHẦN FPT ")

    assert diff_profiles(old, new).is_empty()


def test_ignores_list_order() -> None:
    old = profile(industry=["Công nghệ thông tin", "Viễn thông"])
    new = profile(industry=["Viễn thông", "Công nghệ thông tin"])

    assert diff_profiles(old, new).is_empty()


def test_ignores_description() -> None:
    """Văn xuôi model viết lần nào cũng khác chữ — so nó là làm hỏng cả tính năng."""
    old = profile(description="Tập đoàn công nghệ hàng đầu Việt Nam.")
    new = profile(description="FPT là một tập đoàn công nghệ lớn của Việt Nam.")

    assert diff_profiles(old, new).is_empty()


def test_reports_a_real_change() -> None:
    old = profile(address="Hà Nội")
    new = profile(address="Số 10 Phạm Văn Bạch, Cầu Giấy, Hà Nội")

    diff = diff_profiles(old, new)

    assert [(c.field, c.old, c.new) for c in diff.changes] == [
        ("address", "Hà Nội", "Số 10 Phạm Văn Bạch, Cầu Giấy, Hà Nội")
    ]


def test_tax_code_change_is_notable() -> None:
    """Mã số thuế đổi nghĩa là pháp nhân đổi — không phải một dòng trong danh sách."""
    assert diff_profiles(profile(), profile(tax_code="0100109106")).notable is True


def test_phone_change_is_not_notable() -> None:
    assert diff_profiles(profile(), profile(phone="+842473007300")).notable is False


def test_a_field_that_could_not_be_found_is_not_a_change() -> None:
    """Hôm nay tra không ra ≠ công ty không còn mã số thuế."""
    diff = diff_profiles(profile(), profile(tax_code=None))

    assert diff.changes == []
    assert diff.missing == ["tax_code"]
    assert diff.is_empty() is False


def test_a_newly_found_field_is_a_change() -> None:
    diff = diff_profiles(profile(phone=None), profile(phone="+842473007300"))

    assert [(c.field, c.old) for c in diff.changes] == [("phone", None)]


# --------------------------------------------------------------------------- không mất dữ liệu


def test_merge_never_blanks_a_known_value() -> None:
    """Lượt tra lại **không được** biến một mã số thuế đang có thành rỗng."""
    old = profile(tax_code="0101248141", phone="+842473007300")
    new = profile(tax_code=None, phone=None, address="Địa chỉ mới")

    merged = merge_keeping_known(old, new)

    assert merged.tax_code == "0101248141"
    assert merged.phone == "+842473007300"
    assert merged.address == "Địa chỉ mới"


def test_merge_takes_the_new_description_when_there_is_one() -> None:
    """Mô tả là ảnh chụp của đúng lần tra ấy — trộn nửa cũ nửa mới ra thứ không ai từng nói."""
    merged = merge_keeping_known(profile(description="Cũ"), profile(description="Mới"))

    assert merged.description == "Mới"


def test_merge_keeps_the_old_description_when_the_new_run_has_none() -> None:
    merged = merge_keeping_known(profile(description="Cũ"), profile(description=None))

    assert merged.description == "Cũ"


# --------------------------------------------------------------------------- hồ sơ quá hạn


async def test_stale_lists_the_oldest_first(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    await make_company(
        db_session, user_a, "Cũ nhất", values=profile(), checked_at=NOW - timedelta(days=400)
    )
    await make_company(
        db_session, user_a, "Cũ vừa", values=profile(), checked_at=NOW - timedelta(days=120)
    )
    await make_company(
        db_session, user_a, "Mới tra", values=profile(), checked_at=datetime.now(UTC)
    )

    async with app_client(user_a) as http:
        body = (await http.get("/api/refresh/stale")).json()

    assert body["days"] == 90
    assert [item["display_name"] for item in body["items"]] == ["Cũ nhất", "Cũ vừa"]
    assert body["items"][0]["days_since"] > body["items"][1]["days_since"]


async def test_stale_skips_companies_without_a_finished_profile(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    """Hồ sơ nháp chưa từng tra xong thì chưa có gì để *làm mới*."""
    await make_company(db_session, user_a, "Chưa có hồ sơ")
    await make_company(
        db_session,
        user_a,
        "Còn nháp",
        values=profile(),
        status=ProfileStatus.DRAFT,
        checked_at=NOW - timedelta(days=400),
    )

    async with app_client(user_a) as http:
        body = (await http.get("/api/refresh/stale")).json()

    assert body["total"] == 0


async def test_stale_never_shows_another_users_companies(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User, user_b: User
) -> None:
    await make_company(
        db_session, user_b, "Của B", values=profile(), checked_at=NOW - timedelta(days=400)
    )

    async with app_client(user_a) as http:
        body = (await http.get("/api/refresh/stale")).json()

    assert body["total"] == 0


async def test_stale_window_is_adjustable(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    await make_company(
        db_session,
        user_a,
        "Tra 30 ngày trước",
        values=profile(),
        checked_at=datetime.now(UTC) - timedelta(days=30),
    )

    async with app_client(user_a) as http:
        wide = (await http.get("/api/refresh/stale", params={"days": 7})).json()
        narrow = (await http.get("/api/refresh/stale", params={"days": 365})).json()

    assert wide["total"] == 1
    assert narrow["total"] == 0


# --------------------------------------------------------------------------- nhật ký thay đổi


async def record(
    db: AsyncSession, company: Company, *, tax_code: str | None = None, missing: bool = False
) -> None:
    old = profile()
    new = profile(tax_code=tax_code) if tax_code else profile(phone="+842473007300")
    if missing:
        new = profile(tax_code=None)
    diff = diff_profiles(old, new)
    await company_repo.record_change(db, company.id, diff.as_json(), notable=diff.notable)
    await db.flush()


async def test_changes_put_the_notable_ones_first(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    ordinary = await make_company(db_session, user_a, "Đổi số điện thoại", values=profile())
    important = await make_company(db_session, user_a, "Đổi mã số thuế", values=profile())
    await record(db_session, ordinary)
    await record(db_session, important, tax_code="0100109106")

    async with app_client(user_a) as http:
        body = (await http.get("/api/refresh/changes")).json()

    assert body["total"] == 2
    assert body["notable"] == 1
    assert body["items"][0]["display_name"] == "Đổi mã số thuế"
    assert body["items"][0]["notable"] is True


async def test_changes_carry_readable_labels(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    company = await make_company(db_session, user_a, values=profile())
    await record(db_session, company, tax_code="0100109106")

    async with app_client(user_a) as http:
        entry = (await http.get("/api/refresh/changes")).json()["items"][0]

    change = entry["changes"][0]
    assert change["label"] == "Mã số thuế"
    assert (change["old"], change["new"]) == ("0101248141", "0100109106")


async def test_missing_fields_are_reported_apart_from_changes(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    company = await make_company(db_session, user_a, values=profile())
    await record(db_session, company, missing=True)

    async with app_client(user_a) as http:
        entry = (await http.get("/api/refresh/changes")).json()["items"][0]

    assert entry["changes"] == []
    assert entry["missing"] == ["Mã số thuế"]


async def test_acknowledged_changes_leave_the_list(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    company = await make_company(db_session, user_a, values=profile())
    await record(db_session, company, tax_code="0100109106")

    async with app_client(user_a) as http:
        entry = (await http.get("/api/refresh/changes")).json()["items"][0]
        ack = await http.post(f"/api/refresh/changes/{entry['id']}/ack")
        after = (await http.get("/api/refresh/changes")).json()
        kept = (await http.get("/api/refresh/changes", params={"unseen_only": False})).json()

    assert ack.status_code == 204
    assert after["total"] == 0
    assert kept["total"] == 1  # dòng nhật ký ở lại, chỉ thôi nằm trong danh sách cần đọc


async def test_acknowledging_twice_is_404(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    company = await make_company(db_session, user_a, values=profile())
    await record(db_session, company, tax_code="0100109106")

    async with app_client(user_a) as http:
        entry = (await http.get("/api/refresh/changes")).json()["items"][0]
        await http.post(f"/api/refresh/changes/{entry['id']}/ack")
        again = await http.post(f"/api/refresh/changes/{entry['id']}/ack")

    assert again.status_code == 404


async def test_cannot_acknowledge_another_users_change(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User, user_b: User
) -> None:
    company = await make_company(db_session, user_b, "Của B", values=profile())
    await record(db_session, company, tax_code="0100109106")

    async with app_client(user_b) as http:
        entry = (await http.get("/api/refresh/changes")).json()["items"][0]
    async with app_client(user_a) as http:
        res = await http.post(f"/api/refresh/changes/{entry['id']}/ack")
        mine = (await http.get("/api/refresh/changes")).json()

    assert res.status_code == 404
    assert mine["total"] == 0


# --------------------------------------------------------------------------- mốc đã kiểm


async def test_marking_checked_takes_a_profile_out_of_the_stale_list(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    """Lượt tra **không đổi gì** vẫn phải đóng mốc, nếu không hồ sơ ấy quá hạn mãi mãi."""
    company = await make_company(
        db_session, user_a, values=profile(), checked_at=NOW - timedelta(days=400)
    )

    async with app_client(user_a) as http:
        before = (await http.get("/api/refresh/stale")).json()["total"]

    await company_repo.mark_checked(db_session, company.id, at=datetime.now(UTC))
    await db_session.flush()

    async with app_client(user_a) as http:
        after = (await http.get("/api/refresh/stale")).json()["total"]

    assert (before, after) == (1, 0)


async def test_a_profile_never_checked_falls_back_to_when_it_was_generated(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User
) -> None:
    """Hồ sơ lập trước `0011` không có `last_checked_at` — lấy `generated_at` thay, không cần vá."""
    company = await make_company(db_session, user_a, values=profile(), checked_at=None)
    stored = await company_repo.get_profile(db_session, company.id)
    assert stored is not None
    stored.generated_at = (NOW - timedelta(days=400)).replace(tzinfo=None)
    await db_session.flush()

    async with app_client(user_a) as http:
        body = (await http.get("/api/refresh/stale")).json()

    assert body["total"] == 1
    assert body["items"][0]["days_since"] is not None
