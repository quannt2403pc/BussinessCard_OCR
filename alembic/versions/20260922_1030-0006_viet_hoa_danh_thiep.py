"""Bon cot Viet hoa + translation_meta cho business_cards

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-22 10:30:00.000000+07:00

Task EX-03 (Q). Thiet ke o Plan.md muc 3.

RUI RO thap: nam cot moi, tat ca NULLABLE, khong rang buoc, khong index, khong viet lai bang.

VI SAO LA COT RIENG chu khong ghi de `full_name` / `company_name_raw`: quy tac 3 cua
`app/prompts/ocr.py` giu nguyen chu ban dia va do la thu duy nhat doi chieu duoc voi anh; giao
dien con in ban goc lam chu thich nho duoi ban dich; va phien am sai tren cot rieng thi sua lai
duoc, sai tren cot goc thi khong.

NGHIA CUA NULL o bon cot `*_vi`: *ban goc dung duoc luon*. NULL **khong** co nghia "chua dich" --
muon biet da dich hay chua thi doc `translation_meta`.

File nay khong dau tieng Viet: xem canh bao o dau alembic.ini (I-07).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Bon cot noi dung. Do dai khop y het cot goc tuong ung trong `app/models/card.py`: ban dich
#: khong bao gio dai hon nhieu so voi ban goc, va lech kieu giua model voi migration la I-16.
_VI_COLUMNS: tuple[tuple[str, sa.types.TypeEngine[str]], ...] = (
    ("full_name_vi", sa.String(length=255)),
    ("job_title_vi", sa.String(length=255)),
    ("company_name_vi", sa.String(length=255)),
    ("address_vi", sa.Text()),
)


def upgrade() -> None:
    for name, column_type in _VI_COLUMNS:
        op.add_column("business_cards", sa.Column(name, column_type, nullable=True))
    op.add_column(
        "business_cards",
        sa.Column("translation_meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("business_cards", "translation_meta")
    for name, _ in reversed(_VI_COLUMNS):
        op.drop_column("business_cards", name)
