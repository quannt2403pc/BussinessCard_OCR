"""Nạp dữ liệu mẫu cho demo rồi đánh chỉ mục vào Knowledge Base.

Chủ sở hữu: Q | Task: 9.2 | xem Task.md

Chạy trong Docker (cần `db` và `embedder` phân giải được):

    docker compose exec api python -m scripts.seed
    docker compose exec api python -m scripts.seed --reset    # nạp lại từ đầu

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
import sys
import uuid
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models.card import BusinessCard
from app.models.company import Company
from app.models.kb import KBChunk
from app.repositories import kb as kb_repo
from app.services.normalize_company import normalize_company_name
from scripts.eval_retrieval import SEED_CARD_EMAILS, SEED_COMPANY_NAMES, seed

#: Khoá nhận dạng công ty mẫu. Cùng hàm chuẩn hoá mà `upsert_company()` của T dùng, nên nhận ra
#: được cả bản ghi do lượt seed trước tạo lẫn bản do người dùng xác nhận danh thiếp tạo ra.
SEED_COMPANY_KEYS: tuple[str, ...] = tuple(
    normalize_company_name(name) for name in SEED_COMPANY_NAMES
)


async def existing_ids(db: AsyncSession) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """(id công ty mẫu, id danh thiếp mẫu) đang có trong DB."""
    companies = list(
        (await db.execute(select(Company.id).where(Company.name_normalized.in_(SEED_COMPANY_KEYS))))
        .scalars()
        .all()
    )
    cards = list(
        (await db.execute(select(BusinessCard.id).where(BusinessCard.email.in_(SEED_CARD_EMAILS))))
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


async def run(reset: bool) -> int:
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            companies, cards = await existing_ids(db)
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

            corpus = await seed(db)

            # Đối chiếu lại với dữ liệu vừa tạo: nếu ai đó đổi tên công ty trong
            # `eval_retrieval.py` mà quên sửa `SEED_COMPANY_NAMES` thì bước chống-nạp-chồng ở
            # trên sẽ âm thầm mất tác dụng. Thà chết ở đây còn hơn để lộ ra sau vài lượt seed.
            created = set(
                (
                    await db.execute(
                        select(Company.display_name).where(Company.id.in_(corpus.labels))
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
            print("Thử ngay: mở /assistant và hỏi 'Công ty nào làm về logistics?'")
    finally:
        await engine.dispose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Nạp dữ liệu mẫu cho demo (task 9.2)")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Xoá dữ liệu mẫu của lượt trước rồi nạp lại (chỉ đụng đúng bộ mẫu)",
    )
    args = parser.parse_args()
    return asyncio.run(run(reset=args.reset))


if __name__ == "__main__":
    raise SystemExit(main())
