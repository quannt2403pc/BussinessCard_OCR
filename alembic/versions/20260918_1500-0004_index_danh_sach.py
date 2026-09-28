"""Hai index btree cho danh sach the va danh sach cong ty

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-18 15:00:00.000000+07:00

Task 9.2 (Q), theo do dac o `docs/db-tuning.md`.

`business_cards (uploaded_at, id)` phuc vu CA HAI chieu doc: danh sach the sap xep giam dan, con
export duyet keyset tang dan -- Postgres quet btree duoc theo ca hai chieu. Them
`companies (display_name, id)`.

O quy mo du lieu demo moi truy van deu duoi 1 ms; day la viec re va an toan, khong cap bach.

Khong tao index trigram cho o tim kiem: planner khong chon, vi nhanh tim theo *ten khac* dung
`array_to_string` (ham STABLE, khong danh index duoc) va `OR` co mot nhanh khong index thi
Postgres quet ca bang.

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
