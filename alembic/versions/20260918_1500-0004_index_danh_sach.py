"""Hai index btree cho danh sach the va danh sach cong ty

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-18 15:00:00.000000+07:00

Task 9.2 (Q), theo do dac cua T o task 9.8 (`docs/db-tuning.md`).

Hai index thuong, khong extension, khong doi code:

  1. business_cards (uploaded_at, id) -- mot index phuc vu CA HAI chieu doc: `GET /api/cards`
     sap xep `uploaded_at DESC, id DESC`, con export cua T duyet keyset `(uploaded_at, id)`
     tang dan. Postgres quet btree duoc theo ca hai chieu.
  2. companies (display_name, id)

So do cua T tren 20 000 the + 3 000 cong ty (trung vi 7 luot EXPLAIN ANALYZE):

  | Truy van                  | Truoc   | Sau     |
  |---------------------------|---------|---------|
  | cards list trang 1        | 2.46 ms | 0.02 ms |
  | export cards moi lo       | 6.62 ms | 0.35 ms |
  | companies list trang 1    | 2.13 ms | 0.11 ms |
  | export companies moi lo   | 5.18 ms | 0.76 ms |

NOI THANG VE QUY MO: du lieu demo chi vai tram dong, o do moi truy van deu duoi 1 ms va KHONG
index nao tao khac biet nguoi dung cam nhan duoc. Day la viec re va an toan, khong phai viec
cap bach -- xem `docs/db-tuning.md` muc 1.

KHONG tao index trigram cho o tim kiem: T da tao thu, planner khong chon o truy van nao vi
nhanh tim theo *ten khac* dung `array_to_string` (ham STABLE, khong danh index duoc), ma `OR`
co mot nhanh khong index thi Postgres quet ca bang. Chi tiet o `docs/db-tuning.md` muc 4.

File nay khong dau tieng Viet: xem canh bao o dau alembic.ini (I-07).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_business_cards_uploaded_at_id", "business_cards", ["uploaded_at", "id"])
    op.create_index("ix_companies_display_name_id", "companies", ["display_name", "id"])


def downgrade() -> None:
    op.drop_index("ix_companies_display_name_id", table_name="companies")
    op.drop_index("ix_business_cards_uploaded_at_id", table_name="business_cards")
