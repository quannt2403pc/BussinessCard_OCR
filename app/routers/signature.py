"""Nhập liên hệ từ khối chữ ký email.

Chủ sở hữu: T | Task: NEXT-08 | xem Task.md

Phần lớn liên hệ ngày nay đến qua email chứ không qua thẻ giấy. Việc phải làm với chúng thì y
hệt, nên luồng này **đổ vào đúng màn hình review của `5.1`**: tạo bản ghi ở trạng thái
`needs_review` rồi trả về id để giao diện chuyển thẳng sang `/cards/{id}`.

Không có endpoint xác nhận riêng, không có màn hình sửa riêng, không có bảng riêng. Thứ duy
nhất khác một lượt quét ảnh là **nguồn dữ liệu**, và nó nằm ở `business_cards.source`.
"""

import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import CurrentUser
from app.core.templates import templates
from app.repositories import card as card_repo
from app.schemas.card import CardOut
from app.schemas.signature import SignatureIn, SignatureOut
from app.services import ocr, user_credentials
from app.services import signature as signature_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["signature"])

Session = Annotated[AsyncSession, Depends(get_db)]


@router.get("/paste", response_class=HTMLResponse, tags=["ui"])
async def paste_page(request: Request) -> HTMLResponse:
    """Trang dán chữ ký. Ở **ngoài** `/cards/…` chứ không phải `/cards/paste`, có lý do.

    `routers/cards.py` của Q khai `GET /cards/{card_id}` và được gắn vào app **trước** router
    này (thứ tự `ROUTER_MODULES`). FastAPI so khớp theo thứ tự đăng ký, nên một đường dẫn chữ
    `/cards/paste` sẽ rơi vào route kia với `card_id="paste"` và trả `422` — `/cards/upload`
    thoát được chỉ vì nó nằm **cùng file, phía trên** route động ấy.

    Đổi thứ tự router để giành lại `/cards/paste` là một thay đổi toàn cục ảnh hưởng mọi đường
    dẫn khác, đắt hơn hẳn việc chọn một đường dẫn khác. `active_nav` vẫn trỏ *Danh thiếp* nên
    thanh điều hướng không đổi theo.
    """
    return templates.TemplateResponse(
        request, "cards/paste.html", {"active_nav": "cards", "title": "Dán chữ ký email"}
    )


@router.post(
    "/api/cards/from-signature",
    response_model=SignatureOut,
    status_code=status.HTTP_201_CREATED,
    tags=["cards"],
)
async def import_signature(
    body: SignatureIn, response: Response, db: Session, user: CurrentUser
) -> SignatureOut:
    """Đọc một khối chữ ký email thành một liên hệ chờ duyệt.

    Trả **200** thay vì 201 khi cùng khối chữ ký đã được dán trước đó, và trả lại đúng bản ghi
    cũ — cùng hợp đồng mà `POST /api/cards/upload` dùng cho ảnh trùng (task 3.1), vì đây đúng
    là cùng một tình huống: người dùng đưa lại một thứ hệ thống đã có.
    """
    started = time.perf_counter()
    content_hash = signature_service.content_hash(body.text)

    existing = await card_repo.get_by_hash(db, content_hash, user_id=user.id)
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return SignatureOut(
            card=CardOut.model_validate(existing),
            duplicate=True,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )

    model = await user_credentials.model_for(db, user, "ocr")
    try:
        result = await signature_service.extract_and_translate(body.text, model=model)
    except signature_service.EmptySignatureError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    card_status, notes = ocr.status_and_notes(result, None)
    try:
        card = await card_repo.create_card(
            db,
            user_id=user.id,
            image_path=None,
            image_hash=content_hash,
            fields=result.card_fields(),
            ocr_raw_json=result.raw_json,
            status=card_status,
            notes=notes,
            source="signature",
        )
    except card_repo.DuplicateImageError:
        # Hai tab cùng dán một chữ ký trong một giây; lượt kia đã ghi xong.
        raced = await card_repo.get_by_hash(db, content_hash, user_id=user.id)
        if raced is None:  # không xảy ra trên PostgreSQL, nhưng đừng trả None cho client
            raise
        response.status_code = status.HTTP_200_OK
        return SignatureOut(
            card=CardOut.model_validate(raced),
            duplicate=True,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )

    logger.info(
        "Nhập liên hệ từ chữ ký: card %s, %d lượt gọi model, %dms",
        card.id,
        result.attempts,
        result.elapsed_ms,
    )
    return SignatureOut(
        card=CardOut.model_validate(card),
        duplicate=False,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )
