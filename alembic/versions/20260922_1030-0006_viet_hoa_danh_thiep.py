"""Bon cot Viet hoa + translation_meta cho business_cards

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-22 10:30:00.000000+07:00

Task EX-03 (Q) -- chi Q duoc sinh revision (quy uoc so 5, Task.md; quy uoc nay KHONG duoc noi
tu D12). Thiet ke o Plan.md muc 3, phan "Viet hoa sau khi quet".

Revision NGUOC HAN voi 0005 ve muc do rui ro: nam cot moi, TAT CA deu NULLABLE, khong rang buoc
nao, khong index nao, khong dong du lieu nao phai vie lai. Chay tren bang dang co du lieu that
la mot cau ALTER TABLE ... ADD COLUMN cua Postgres 11+, khong viet lai bang, khong khoa lau.

VI SAO LA COT RIENG chu khong ghi de `full_name` / `company_name_raw`:

  1. Quy tac 3 cua `app/prompts/ocr.py` giu nguyen chu ban dia, va do la thu duy nhat doi chieu
     duoc voi anh khi nghi may doc sai. Ghi de la mat vinh vien.
  2. Giao dien in ban goc lam chu thich nho ngay duoi ban dich (EX-06) -- can ca hai cung luc.
  3. Phien am la viec model co the lam sai. Sai tren mot cot rieng thi sua lai duoc; sai tren
     cot goc thi khong con gi de sua lai theo.

VI SAO `translation_meta` LA MOT COT chu khong tach thanh vai cot nho: no la nhat ky cua mot buoc
xu ly (nguon ban dich, ngon ngu & he chu model nhan ra, cach phien am, co `stale`, loi neu co),
khong phai du lieu nghiep vu. Khong truy van nao loc theo no, khong index nao can den no.

NGHIA CUA NULL o bon cot `*_vi`: *ban goc dung duoc luon, khong can ban dich* -- the tieng Viet
va the tieng Anh roi het vao ca nay (`services/translate.py::_finalize` bo ban dich trung y het
ban goc). NULL **khong** co nghia "chua dich"; muon biet da dich hay chua thi doc
`translation_meta`.

DOWNGRADE that va sach: bo nam cot. Mat toan bo ban Viet hoa, nhung khong mat mot chu nao cua du
lieu goc -- dung nghia cua viec de chung o cot rieng. Quet lai khong can thiet, bam *Dich lai* la
dung lai het.

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
