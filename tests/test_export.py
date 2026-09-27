import csv
import io
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import current_user
from app.core.workspace import ActiveWorkspace, require_workspace
from app.models.card import BusinessCard, CardStatus, RelationshipStatus
from app.models.company import Company, CompanyProfile
from app.models.privacy import PrivacyAction, PrivacyLog
from app.models.user import User
from app.models.workspace import Role
from app.routers import export
from app.schemas.export import CardExportRow
from tests.conftest import make_user, workspace_id_of

#: Chủ sở hữu của mọi bản ghi trong file này (task 12.8). `0005` đặt `user_id` là NOT NULL
#: trên cả 6 bảng dữ liệu, nên object ORM nào ghi xuống DB cũng phải có nó. Test ở đây không
#: kiểm việc tách dữ liệu (đó là 12.6/12.7) nên một chủ sở hữu duy nhất là đủ.
OWNER_ID = uuid.uuid4()
#: Không gian của `OWNER_ID` (task NEXT-05) — khoá tách dữ liệu của mọi bản ghi ở đây.
WORKSPACE_ID = uuid.uuid4()
OWNER = User(id=OWNER_ID, email="owner-export@example.com", password_hash="!")
#: `require_workspace` tra bảng thành viên bằng `get_db`, mà app nhỏ trong file này không
#: khai `get_db` — nó chỉ vá `export.SessionLocal`. Ghi đè thẳng dependency, vừa khỏi phải
#: dựng session thứ hai vừa giữ đúng ý test: ở đây kiểm nội dung file xuất, không kiểm quyền.
ACTIVE_WORKSPACE = ActiveWorkspace(id=WORKSPACE_ID, name="Không gian test", role=Role.ADMIN)


@pytest.fixture
async def owner(db_session: AsyncSession) -> User:
    """Hàng `users` cho `OWNER_ID` (task 12.8).

    Khoá ngoại `business_cards.user_id` / `companies.user_id` (revision `0005`) đòi chủ sở hữu
    tồn tại thật, nên test nào **ghi xuống DB** cũng phải dựng hàng này trước. Test chạy trên
    `FakeSession` thì không cần — vì thế fixture này không autouse.
    """
    return await make_user(
        db_session,
        "owner-export@example.com",
        "Chủ sở hữu dữ liệu test",
        workspace_id=WORKSPACE_ID,
        user_id=OWNER_ID,
    )


class SessionFactory:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def __call__(self) -> "SessionFactory":
        return self

    async def __aenter__(self) -> AsyncSession:
        return self.session

    async def __aexit__(self, *exc: object) -> bool:
        return False


@pytest.fixture
async def client(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setattr(export, "SessionLocal", SessionFactory(db_session))
    app = FastAPI()
    app.include_router(export.router)
    app.dependency_overrides[current_user] = lambda: OWNER
    app.dependency_overrides[require_workspace] = lambda: ACTIVE_WORKSPACE
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


def card(**kwargs: object) -> BusinessCard:
    values: dict[str, object] = {
        "workspace_id": WORKSPACE_ID,
        "user_id": OWNER_ID,
        "image_path": f"export/{uuid.uuid4().hex[:8]}.jpg",
        "image_hash": uuid.uuid4().hex * 2,
        "status": CardStatus.CONFIRMED,
    }
    values.update(kwargs)
    return BusinessCard(**values)


def read_csv(response: httpx.Response) -> list[list[str]]:
    text = response.content.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


async def test_cards_csv_keeps_bom_header_and_vietnamese(
    db_session: AsyncSession, owner: User, client: httpx.AsyncClient
) -> None:
    company = Company(
        workspace_id=WORKSPACE_ID,
        user_id=OWNER_ID,
        display_name="Công ty FPT",
        name_normalized="fpt",
    )
    db_session.add(company)
    await db_session.flush()
    db_session.add(card(full_name="Nguyễn Văn A", job_title="Giám đốc", company_id=company.id))
    await db_session.flush()

    response = await client.get("/api/export/cards.csv")

    assert response.status_code == 200
    assert response.content.startswith(b"\xef\xbb\xbf")
    assert "attachment" in response.headers["content-disposition"]
    rows = read_csv(response)
    assert tuple(rows[0]) == export.CardExportRow.columns()
    assert rows[1][rows[0].index("full_name")] == "Nguyễn Văn A"
    assert rows[1][rows[0].index("company_name")] == "Công ty FPT"


async def test_cards_csv_exports_card_without_company(
    db_session: AsyncSession, owner: User, client: httpx.AsyncClient
) -> None:
    db_session.add(card(full_name="Trần Thị B", company_name_raw="Cty chưa gắn"))
    await db_session.flush()

    rows = read_csv(await client.get("/api/export/cards.csv"))

    assert len(rows) == 2
    assert rows[1][rows[0].index("company_name")] == ""
    assert rows[1][rows[0].index("company_name_raw")] == "Cty chưa gắn"


async def test_export_is_written_to_the_privacy_log(
    db_session: AsyncSession, owner: User, client: httpx.AsyncClient
) -> None:
    """Mỗi lượt xuất file là một lần dữ liệu cá nhân ra khỏi hệ thống (task NEXT-07).

    Con số ghi lại là số bản ghi **thật sự chảy ra**, đếm ngay trên dòng chảy chứ không hỏi
    lại bằng một câu `COUNT` thứ hai — hai câu riêng có thể lệch nhau, mà một nhật ký bảo vệ
    dữ liệu cá nhân nói sai số bản ghi thì còn tệ hơn không có nhật ký.
    """
    db_session.add_all([card(full_name="Một"), card(full_name="Hai")])
    await db_session.flush()

    response = await client.get("/api/export/cards.csv")
    assert response.status_code == 200

    rows = await db_session.scalars(select(PrivacyLog).where(PrivacyLog.user_id == OWNER_ID))
    entries = list(rows.all())
    assert len(entries) == 1
    assert entries[0].action == PrivacyAction.EXPORT
    assert entries[0].record_count == 2
    assert entries[0].detail["format"] == "csv"


async def test_a_filtered_export_records_its_filters(
    db_session: AsyncSession, owner: User, client: httpx.AsyncClient
) -> None:
    """Nhật ký phải nói rõ **phần nào** của dữ liệu đã ra ngoài, không chỉ nói là có."""
    db_session.add_all(
        [
            card(full_name="Đã xác nhận", status=CardStatus.CONFIRMED),
            card(full_name="Chờ duyệt", status=CardStatus.NEEDS_REVIEW),
        ]
    )
    await db_session.flush()

    await client.get("/api/export/cards.json?status=confirmed")

    entry = await db_session.scalar(select(PrivacyLog).where(PrivacyLog.user_id == OWNER_ID))
    assert entry is not None
    assert entry.detail["filters"] == {"status": "confirmed"}
    assert entry.record_count == 1


async def test_merged_cards_are_left_out(
    db_session: AsyncSession, owner: User, client: httpx.AsyncClient
) -> None:
    """Bản trùng đã gộp không nằm trong bản xuất (task NEXT-04).

    Xuất ra thì công cụ nhận file lại dựng lại đúng cặp trùng mà người dùng vừa gộp xong —
    và nó phải đúng ở **cả** câu đếm lẫn câu lấy dòng, lệch nhau thì `total` nói một đằng,
    `items` một nẻo.
    """
    primary = card(full_name="Giữ lại")
    db_session.add(primary)
    await db_session.flush()
    db_session.add(card(full_name="Đã gộp", merged_into_id=primary.id))
    await db_session.flush()

    payload = json.loads((await client.get("/api/export/cards.json")).text)

    assert payload["total"] == 1
    assert [row["full_name"] for row in payload["items"]] == ["Giữ lại"]


async def test_cards_filter_by_relationship(
    db_session: AsyncSession, owner: User, client: httpx.AsyncClient
) -> None:
    """Lọc theo **vòng đời quan hệ** (task NEXT-01), độc lập với `status` của việc quét.

    Ca dùng thật: xuất riêng những người đang trao đổi để mang sang công cụ gửi thư.
    """
    db_session.add_all(
        [
            card(full_name="Đang trao đổi", relationship_status=RelationshipStatus.TALKING),
            card(full_name="Mới quét"),
        ]
    )
    await db_session.flush()

    payload = json.loads((await client.get("/api/export/cards.json?relationship=talking")).text)

    assert payload["filters"] == {"relationship": "talking"}
    assert [row["full_name"] for row in payload["items"]] == ["Đang trao đổi"]
    assert payload["items"][0]["relationship_status"] == "talking"


async def test_cards_reject_unknown_relationship(owner: User, client: httpx.AsyncClient) -> None:
    response = await client.get("/api/export/cards.csv?relationship=co-le")

    assert response.status_code == 400


async def test_cards_filter_by_status(
    db_session: AsyncSession, owner: User, client: httpx.AsyncClient
) -> None:
    db_session.add_all(
        [
            card(full_name="Đã xác nhận", status=CardStatus.CONFIRMED),
            card(full_name="Chờ duyệt", status=CardStatus.NEEDS_REVIEW),
        ]
    )
    await db_session.flush()

    payload = json.loads((await client.get("/api/export/cards.json?status=confirmed")).text)

    assert payload["total"] == 1
    assert payload["filters"] == {"status": "confirmed"}
    assert [item["full_name"] for item in payload["items"]] == ["Đã xác nhận"]


async def test_cards_invalid_status_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/export/cards.csv?status=xong")

    assert response.status_code == 400
    assert "pending" in response.json()["detail"]


async def test_companies_export_includes_company_without_profile(
    db_session: AsyncSession, owner: User, client: httpx.AsyncClient
) -> None:
    company = Company(
        workspace_id=WORKSPACE_ID,
        user_id=OWNER_ID,
        display_name="Chưa có hồ sơ",
        name_normalized="chua co ho so",
    )
    db_session.add(company)
    await db_session.flush()

    rows = read_csv(await client.get("/api/export/companies.csv"))

    assert len(rows) == 2
    assert rows[1][rows[0].index("display_name")] == "Chưa có hồ sơ"
    assert rows[1][rows[0].index("profile_status")] == ""
    assert rows[1][rows[0].index("legal_name")] == ""
    assert rows[1][rows[0].index("sources")] == ""
    assert rows[1][rows[0].index("contact_count")] == "0"


async def test_companies_export_profile_lists_and_sources(
    db_session: AsyncSession, owner: User, client: httpx.AsyncClient
) -> None:
    company = Company(
        workspace_id=WORKSPACE_ID,
        user_id=OWNER_ID,
        display_name="FPT Software",
        name_normalized="fpt software",
    )
    db_session.add(company)
    await db_session.flush()
    db_session.add_all(
        [
            card(company_id=company.id),
            card(company_id=company.id),
            CompanyProfile(
                workspace_id=WORKSPACE_ID,
                user_id=OWNER_ID,
                company_id=company.id,
                status="generated",
                legal_name="Công ty TNHH Phần mềm FPT",
                industry=["Phần mềm", "Dịch vụ CNTT"],
                sources={"legal_name": [{"url": "https://masothue.com/abc"}]},
                generated_at=datetime.now(UTC).replace(tzinfo=None),
            ),
        ]
    )
    await db_session.flush()

    rows = read_csv(await client.get("/api/export/companies.csv"))
    cells = dict(zip(rows[0], rows[1], strict=True))

    assert cells["contact_count"] == "2"
    assert cells["industry"] == "Phần mềm; Dịch vụ CNTT"
    assert json.loads(cells["sources"])["legal_name"][0]["url"] == "https://masothue.com/abc"

    payload = json.loads((await client.get("/api/export/companies.json")).text)
    item = payload["items"][0]

    assert payload["total"] == 1
    assert item["industry"] == ["Phần mềm", "Dịch vụ CNTT"]
    assert item["sources"]["legal_name"][0]["url"] == "https://masothue.com/abc"
    assert item["profile_status"] == "generated"


async def test_export_reads_every_row_across_batches(
    db_session: AsyncSession,
    owner: User,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(export, "EXPORT_BATCH_SIZE", 2)
    db_session.add_all([card(full_name=f"Người {index}") for index in range(5)])
    await db_session.flush()

    payload = json.loads((await client.get("/api/export/cards.json")).text)
    names = sorted(item["full_name"] for item in payload["items"])

    assert payload["total"] == 5
    assert names == [f"Người {index}" for index in range(5)]
    assert len({item["id"] for item in payload["items"]}) == 5


# --------------------------------------------------------------------------- vCard (NEXT-02)


def vcard_lines(text: str) -> list[str]:
    """Bỏ gập dòng rồi tách — kiểm nội dung thì phải so trên dòng đã nối lại."""
    return text.replace("\r\n ", "").rstrip("\r\n").split("\r\n")


def field(text: str, name: str) -> str:
    return next(line.split(":", 1)[1] for line in vcard_lines(text) if line.startswith(name))


def test_vcard_escapes_and_folds() -> None:
    row = CardExportRow(
        id=uuid.uuid4(),
        full_name="Nguyễn Văn A",
        company_name="Công ty; Cổ phần, ABC",
        notes="Dòng một\nDòng hai",
        status=CardStatus.CONFIRMED,
        uploaded_at=datetime(2026, 9, 20, 3, 0, tzinfo=UTC),
        updated_at=datetime(2026, 9, 21, 7, 30, tzinfo=UTC),
    )

    text = row.vcard()

    assert text.startswith("BEGIN:VCARD\r\nVERSION:3.0\r\n")
    assert text.endswith("END:VCARD\r\n")
    # Dấu `;` và `,` trong tên công ty phải được thoát, nếu không chúng cắt giá trị thành nhiều trường.
    assert field(text, "ORG") == r"Công ty\; Cổ phần\, ABC"
    # vCard mã hoá xuống dòng thành **hai ký tự** `\` và `n`, không phải một ký tự xuống dòng.
    assert field(text, "NOTE") == r"Dòng một\nDòng hai"
    assert field(text, "N") == "Nguyễn;Văn A;;;"
    assert field(text, "REV") == "20260921T073000Z"
    # Mọi dòng thật (sau khi bỏ gập) đều ≤ 75 octet, kể cả chữ có dấu.
    for line in text.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75


def test_vcard_folding_never_splits_a_character() -> None:
    row = CardExportRow(
        id=uuid.uuid4(),
        full_name="Nguyễn Văn A",
        notes="ữ" * 120,
        status=CardStatus.CONFIRMED,
        uploaded_at=datetime(2026, 9, 20, 3, 0, tzinfo=UTC),
        updated_at=datetime(2026, 9, 20, 3, 0, tzinfo=UTC),
    )

    text = row.vcard()

    assert text.encode("utf-8").decode("utf-8")
    assert field(text, "NOTE") == "ữ" * 120


def test_vcard_prefers_vietnamese_name_and_keeps_the_original() -> None:
    row = CardExportRow(
        id=uuid.uuid4(),
        full_name="김민수",
        full_name_vi="Kim Min-su",
        company_name="삼성전자 주식회사",
        company_name_vi="Công ty Cổ phần Điện tử Samsung",
        job_title="영업부 차장",
        job_title_vi="Phó phòng Kinh doanh",
        status=CardStatus.CONFIRMED,
        uploaded_at=datetime(2026, 9, 20, 3, 0, tzinfo=UTC),
        updated_at=datetime(2026, 9, 20, 3, 0, tzinfo=UTC),
    )

    text = row.vcard()

    # Danh bạ tìm bằng bàn phím Latin, nên `FN` lấy bản Việt hoá…
    assert field(text, "FN") == "Kim Min-su"
    assert field(text, "ORG") == "Công ty Cổ phần Điện tử Samsung"
    assert field(text, "TITLE") == "Phó phòng Kinh doanh"
    # …nhưng bản in trên thẻ không được mất, vì nó là thứ duy nhất đối chiếu lại được với ảnh.
    assert "Tên trên thẻ: 김민수" in field(text, "NOTE")
    assert "Công ty trên thẻ: 삼성전자 주식회사" in field(text, "NOTE")


async def test_cards_vcf_exports_every_card(
    db_session: AsyncSession, owner: User, client: httpx.AsyncClient
) -> None:
    company = Company(
        workspace_id=WORKSPACE_ID,
        user_id=OWNER_ID,
        display_name="Công ty FPT",
        name_normalized="fpt",
    )
    db_session.add(company)
    await db_session.flush()
    db_session.add_all(
        [
            card(full_name="Nguyễn Văn A", company_id=company.id, email="a@fpt.vn"),
            card(full_name="Trần Thị B", status=CardStatus.NEEDS_REVIEW),
        ]
    )
    await db_session.flush()

    response = await client.get("/api/export/cards.vcf")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/vcard")
    assert ".vcf" in response.headers["content-disposition"]
    text = response.text
    # BOM là mẹo cho Excel; trình đọc vCard coi nó là rác ngay dòng đầu.
    assert not text.startswith("﻿")
    assert text.count("BEGIN:VCARD") == 2
    assert "FN:Nguyễn Văn A" in text
    assert "ORG:Công ty FPT" in text


async def test_cards_vcf_honours_the_status_filter(
    db_session: AsyncSession, owner: User, client: httpx.AsyncClient
) -> None:
    db_session.add_all(
        [
            card(full_name="Đã xác nhận", status=CardStatus.CONFIRMED),
            card(full_name="Chờ duyệt", status=CardStatus.NEEDS_REVIEW),
        ]
    )
    await db_session.flush()

    text = (await client.get("/api/export/cards.vcf?status=confirmed")).text

    assert text.count("BEGIN:VCARD") == 1
    assert "FN:Đã xác nhận" in text


async def test_one_card_vcf_and_another_workspaces_card_is_404(
    db_session: AsyncSession, owner: User, client: httpx.AsyncClient
) -> None:
    mine = card(full_name="Của tôi")
    stranger = await make_user(db_session, "nguoi-khac@example.com")
    theirs = BusinessCard(
        workspace_id=await workspace_id_of(db_session, stranger),
        user_id=stranger.id,
        image_path="export/khac.jpg",
        image_hash=uuid.uuid4().hex * 2,
        status=CardStatus.CONFIRMED,
        full_name="Của người khác",
    )
    db_session.add_all([mine, theirs])
    await db_session.flush()

    ok = await client.get(f"/api/export/cards/{mine.id}.vcf")
    forbidden = await client.get(f"/api/export/cards/{theirs.id}.vcf")
    missing = await client.get(f"/api/export/cards/{uuid.uuid4()}.vcf")

    assert ok.status_code == 200
    assert "FN:Của tôi" in ok.text
    assert (forbidden.status_code, missing.status_code) == (404, 404)
    assert "Của người khác" not in forbidden.text
