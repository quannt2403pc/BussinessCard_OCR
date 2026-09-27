"""Việt hoá tên công ty: `companies.display_name_vi` (`I-36`).

Chủ sở hữu: T | Task: I-36 | xem Task.md

Danh sách `/companies` hiện tên **như in trên thẻ** — `삼성전자주식회사`, `华为技术有限公司` —
trong khi bản Việt hoá đã nằm sẵn ở `business_cards.company_name_vi` từ `EX-02`. File này khoá
lại bốn điều dễ hỏng của việc chép bản dịch ấy sang bảng `companies`:

1. Chép đúng lúc tạo công ty, **không gọi thêm lượt model nào**.
2. Tên vốn đã là tiếng Việt thì **không lưu bản trùng** — lưu thì giao diện in hai dòng y hệt.
3. Công ty tạo từ thẻ chưa dịch được **lấp** khi thẻ sau có bản dịch, nhưng bản đã có thì
   **không bị ghi đè**.
4. Tìm kiếm khớp cả bản Việt — gõ đúng cái tên đang nhìn thấy mà không ra là lỗi nặng nhất ở đây.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.user import User
from app.repositories import company as company_repo
from app.services.company_matching import upsert_company
from tests.conftest import workspace_id_of

KOREAN = "삼성전자주식회사"
KOREAN_VI = "Công ty Cổ phần Điện tử Samsung"


async def add_company(
    db: AsyncSession, user: User, raw_name: str, name_vi: str | None = None
) -> Company:
    company_id = await upsert_company(
        db,
        raw_name,
        workspace_id=await workspace_id_of(db, user),
        user_id=user.id,
        display_name_vi=name_vi,
    )
    await db.flush()
    return await db.get_one(Company, company_id)


async def test_vietnamese_name_is_copied_from_the_card(
    db_session: AsyncSession, user_a: User
) -> None:
    company = await add_company(db_session, user_a, KOREAN, KOREAN_VI)

    assert company.display_name == KOREAN  # bản gốc không bị ghi đè
    assert company.display_name_vi == KOREAN_VI
    assert company.vi_name == KOREAN_VI


async def test_name_already_in_vietnamese_stores_no_duplicate(
    db_session: AsyncSession, user_a: User
) -> None:
    """Bản dịch trùng y tên gốc thì bỏ, nếu không giao diện in hai dòng giống hệt nhau."""
    company = await add_company(db_session, user_a, "Tập đoàn FPT", "tập đoàn  FPT")

    assert company.display_name_vi is None
    assert company.vi_name == "Tập đoàn FPT"


async def test_a_card_without_a_translation_leaves_the_original(
    db_session: AsyncSession, user_a: User
) -> None:
    company = await add_company(db_session, user_a, KOREAN, None)

    assert company.display_name_vi is None
    assert company.vi_name == KOREAN  # không bao giờ hiện ô trống


async def test_a_later_translated_card_fills_the_gap(
    db_session: AsyncSession, user_a: User
) -> None:
    """Thẻ đầu chưa dịch, thẻ sau đã dịch — cái tên chữ Hàn không được nằm lại mãi."""
    first = await add_company(db_session, user_a, KOREAN, None)
    assert first.display_name_vi is None

    again = await add_company(db_session, user_a, KOREAN, KOREAN_VI)

    assert again.id == first.id  # vẫn đúng một công ty, không đẻ thêm bản ghi
    assert again.display_name_vi == KOREAN_VI


async def test_an_existing_translation_is_never_overwritten(
    db_session: AsyncSession, user_a: User
) -> None:
    """Bản đầu là bản người dùng đã nhìn thấy trong danh sách; thẻ sau không được đổi nó."""
    first = await add_company(db_session, user_a, KOREAN, KOREAN_VI)
    again = await add_company(db_session, user_a, KOREAN, "Samsung Electronics")

    assert again.id == first.id
    assert again.display_name_vi == KOREAN_VI


async def test_search_matches_the_vietnamese_name(db_session: AsyncSession, user_a: User) -> None:
    """Gõ đúng cái tên đang hiện mà không ra thì người dùng kết luận công ty chưa có trong hệ thống."""
    company = await add_company(db_session, user_a, KOREAN, KOREAN_VI)
    workspace_id = await workspace_id_of(db_session, user_a)

    rows, total = await company_repo.list_companies(
        db_session, workspace_id=workspace_id, q="Samsung"
    )

    assert total == 1
    assert [row.company.id for row in rows] == [company.id]


async def test_search_still_matches_the_original_script(
    db_session: AsyncSession, user_a: User
) -> None:
    """Chiều ngược lại: người quen chữ Hàn vẫn phải tra được."""
    company = await add_company(db_session, user_a, KOREAN, KOREAN_VI)
    workspace_id = await workspace_id_of(db_session, user_a)

    rows, total = await company_repo.list_companies(db_session, workspace_id=workspace_id, q="삼성")

    assert total == 1
    assert [row.company.id for row in rows] == [company.id]


async def test_the_list_is_ordered_by_the_name_people_see(
    db_session: AsyncSession, user_a: User
) -> None:
    """Sắp theo `display_name` thì danh sách tiếng Việt xếp theo bảng chữ cái Hàn — trông như loạn."""
    await add_company(db_session, user_a, KOREAN, "Zeta Việt Nam")
    await add_company(db_session, user_a, "华为技术有限公司", "Alpha Việt Nam")
    workspace_id = await workspace_id_of(db_session, user_a)

    rows, _ = await company_repo.list_companies(db_session, workspace_id=workspace_id)

    assert [row.company.vi_name for row in rows] == ["Alpha Việt Nam", "Zeta Việt Nam"]


@pytest.mark.parametrize("name_vi", ["", "   ", None])
async def test_blank_translations_are_treated_as_missing(
    db_session: AsyncSession, user_a: User, name_vi: str | None
) -> None:
    company = await add_company(db_session, user_a, f"Công ty {uuid.uuid4().hex[:6]}", name_vi)

    assert company.display_name_vi is None
