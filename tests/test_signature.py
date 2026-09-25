"""Nhập liên hệ từ khối chữ ký email.

Chủ sở hữu: T | Task: NEXT-08 | xem Task.md

Ý chính của task là **đổi đầu vào, giữ nguyên mọi thứ còn lại**. Nên phần đáng kiểm nhất không
phải "model đọc có đúng không" (đó là việc của prompt) mà là **liên hệ dán vào có thật sự đi
chung một đường với thẻ quét hay không**:

1. Vẫn rơi vào `needs_review`, vẫn qua bước chuẩn hoá SĐT/email của `normalize.py`.
2. Vẫn dùng **chính** ràng buộc chống trùng `(user_id, image_hash)` của `3.1` — dán hai lần một
   chữ ký trả về đúng bản ghi cũ, không tạo thêm.
3. Không có ảnh thì `image_path` là `NULL` chứ không phải chuỗi rỗng, và mọi chỗ đọc cột ấy
   phải chịu được.
"""

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.card import BusinessCard, CardStatus
from app.models.user import User
from app.services import signature as signature_service
from tests.conftest import CliProxyStub

ClientFactory = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]

SIGNATURE = """Trân trọng,

Nguyễn Văn An
Trưởng phòng Kinh doanh | Công ty TNHH Ví Dụ
M: 0912 345 678 | E: An.Nguyen@ViDu.VN
www.vidu.vn

This email and any files transmitted with it are confidential.
"""

EXTRACTED: dict[str, Any] = {
    "full_name": "Nguyễn Văn An",
    "job_title": "Trưởng phòng Kinh doanh",
    "company_name_raw": "Công ty TNHH Ví Dụ",
    "email": "An.Nguyen@ViDu.VN",
    "phone": "0912 345 678",
    "phone_alt": None,
    "address": None,
    "website": "www.vidu.vn",
    "language_detected": "vi",
    "confidence": {"full_name": 0.95, "email": 0.9},
}


def reply(stub: CliProxyStub, payload: dict[str, Any] | None = None) -> None:
    """Model trả JSON trích xuất, rồi trả lượt Việt hoá (EX-02) ngay sau đó."""
    import json

    stub.reply(json.dumps(payload or EXTRACTED, ensure_ascii=False), "{}")


# --------------------------------------------------------------------------- khoá chống trùng


def test_content_hash_ignores_whitespace_noise() -> None:
    """Cùng chữ ký dán từ hai ứng dụng thư hay lệch nhau ở khoảng trắng — không phải hai người."""
    messy = "  Nguyễn Văn An  \n\n\n  Công ty Ví Dụ   \n"
    tidy = "Nguyễn Văn An\nCông ty Ví Dụ"

    assert signature_service.content_hash(messy) == signature_service.content_hash(tidy)


def test_content_hash_separates_different_people() -> None:
    a = signature_service.content_hash("Nguyễn Văn An\nvidu.vn")
    b = signature_service.content_hash("Trần Thị Bình\nvidu.vn")

    assert a != b


# --------------------------------------------------------------------------- trích xuất


async def test_extraction_goes_through_the_same_normalisation(cliproxy: CliProxyStub) -> None:
    """Dùng lại `ocr.parse_response()` nên SĐT và email được chuẩn hoá y như thẻ quét."""
    reply(cliproxy)

    result = await signature_service.extract_signature(SIGNATURE)

    assert result.extraction.full_name == "Nguyễn Văn An"
    assert result.extraction.phone == "+84912345678"  # normalize.py của Q
    assert result.extraction.email == "an.nguyen@vidu.vn"  # hạ về chữ thường
    assert result.attempts == 1


async def test_a_too_short_block_never_calls_the_model(cliproxy: CliProxyStub) -> None:
    """Tốn một lượt gọi model cho một dòng chữ vô nghĩa là lãng phí thấy rõ."""
    with pytest.raises(signature_service.EmptySignatureError):
        await signature_service.extract_signature("Trân trọng,")

    assert cliproxy.router.calls.call_count == 0


async def test_a_second_attempt_recovers_from_chatty_output(cliproxy: CliProxyStub) -> None:
    """Model hay trả chữ dẫn nhập trước khối JSON; lượt hai kèm lời nhắc thường ra đúng."""
    import json

    cliproxy.reply("Đây là kết quả:", json.dumps(EXTRACTED, ensure_ascii=False))

    result = await signature_service.extract_signature(SIGNATURE)

    assert result.attempts == 2
    assert result.extraction.full_name == "Nguyễn Văn An"


# --------------------------------------------------------------------------- endpoint


async def test_import_creates_a_card_waiting_for_review(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User, cliproxy: CliProxyStub
) -> None:
    """Đổ vào đúng màn hình review của `5.1` — không có luồng duyệt thứ hai."""
    reply(cliproxy)

    async with app_client(user_a) as http:
        res = await http.post("/api/cards/from-signature", json={"text": SIGNATURE})

    assert res.status_code == 201
    body = res.json()
    assert body["duplicate"] is False
    assert body["card"]["status"] == CardStatus.NEEDS_REVIEW
    assert body["card"]["source"] == "signature"
    assert body["card"]["phone"] == "+84912345678"


async def test_an_imported_contact_has_no_image_path(
    app_client: ClientFactory, db_session: AsyncSession, user_a: User, cliproxy: CliProxyStub
) -> None:
    """`NULL` chứ không phải chuỗi rỗng: chuỗi rỗng là một đường dẫn *hợp lệ* trỏ vào chỗ sai."""
    reply(cliproxy)

    async with app_client(user_a) as http:
        body = (await http.post("/api/cards/from-signature", json={"text": SIGNATURE})).json()

    card = await db_session.get(BusinessCard, uuid.UUID(body["card"]["id"]))
    assert card is not None
    assert card.image_path is None
    assert card.image_hash == signature_service.content_hash(SIGNATURE)


async def test_the_image_endpoint_says_why_there_is_no_image(
    app_client: ClientFactory, user_a: User, cliproxy: CliProxyStub
) -> None:
    """404 kèm lý do, chứ không phải lỗi kiểu ở `_resolve_image(None)`."""
    reply(cliproxy)

    async with app_client(user_a) as http:
        body = (await http.post("/api/cards/from-signature", json={"text": SIGNATURE})).json()
        res = await http.get(f"/api/cards/{body['card']['id']}/image")

    assert res.status_code == 404
    assert "chữ ký email" in res.json()["detail"]


async def test_pasting_the_same_signature_twice_returns_the_first_contact(
    app_client: ClientFactory, user_a: User, cliproxy: CliProxyStub
) -> None:
    """Dùng **chính** ràng buộc chống trùng của `3.1`, không thêm cơ chế thứ hai."""
    import json

    cliproxy.reply(json.dumps(EXTRACTED, ensure_ascii=False), "{}")

    async with app_client(user_a) as http:
        first = await http.post("/api/cards/from-signature", json={"text": SIGNATURE})
        second = await http.post("/api/cards/from-signature", json={"text": SIGNATURE})
        cards = (await http.get("/api/cards?size=50")).json()

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["card"]["id"] == first.json()["card"]["id"]
    assert cards["total"] == 1


async def test_two_users_may_paste_the_same_signature(
    app_client: ClientFactory, user_a: User, user_b: User, cliproxy: CliProxyStub
) -> None:
    """Chống trùng theo từng người dùng, cùng luật với ảnh (Plan.md mục 3)."""
    import json

    cliproxy.reply(*([json.dumps(EXTRACTED, ensure_ascii=False), "{}"] * 2))

    async with app_client(user_a) as http:
        mine = await http.post("/api/cards/from-signature", json={"text": SIGNATURE})
    async with app_client(user_b) as http:
        theirs = await http.post("/api/cards/from-signature", json={"text": SIGNATURE})

    assert (mine.status_code, theirs.status_code) == (201, 201)
    assert mine.json()["card"]["id"] != theirs.json()["card"]["id"]


async def test_a_short_block_is_rejected_before_anything_happens(
    app_client: ClientFactory, user_a: User, cliproxy: CliProxyStub
) -> None:
    async with app_client(user_a) as http:
        res = await http.post("/api/cards/from-signature", json={"text": "Trân trọng"})

    assert res.status_code == 422
    assert cliproxy.router.calls.call_count == 0


async def test_an_imported_contact_behaves_like_any_other(
    app_client: ClientFactory, user_a: User, cliproxy: CliProxyStub
) -> None:
    """Xuất được, theo dõi được, gộp trùng được — vì nó là cùng một bảng, cùng một luồng."""
    reply(cliproxy)

    async with app_client(user_a) as http:
        body = (await http.post("/api/cards/from-signature", json={"text": SIGNATURE})).json()
        card_id = body["card"]["id"]
        follow_up = await http.patch(
            f"/api/contacts/{card_id}", json={"relationship_status": "contacted"}
        )
        dupes = await http.get("/api/duplicates")
        listed = await http.get("/api/cards?size=50")

    assert follow_up.status_code == 200
    assert dupes.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [card_id]
    # Phần xuất file kiểm ở `tests/test_export.py`: router export tự mở `SessionLocal()`, chỉ
    # file kia mới có bộ vá cho nó.


async def test_the_paste_page_renders(app_client: ClientFactory, user_a: User) -> None:
    async with app_client(user_a) as http:
        res = await http.get("/paste")

    assert res.status_code == 200
    assert "Dán chữ ký email" in res.text
