"""Bang user_model_prefs -- moi nguoi dung chon model rieng cho tung chuc nang

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-24 15:00:00.000000+07:00

Task EX-13 (Q) -- chi Q duoc sinh revision (quy uoc so 5, Task.md; quy uoc nay KHONG duoc noi
tu D12). Quyet dinh thiet ke o `docs/adr-model-per-feature.md` muc 4.

Rui ro cua revision nay o muc THAP NHAT co the: mot bang MOI, khong dong du lieu nao phai viet
lai, khong rang buoc nao them vao bang dang co. Chay tren DB dang co du lieu chi la mot cau
CREATE TABLE.

VI SAO LA BANG RIENG chu khong them ba cot vao `users`:

  1. `models/user.py` la file cua T. Luat noi tu D12 cho phep cham, nhung cham ma khong can thi
     van la tao viec review cho nguoi kia.
  2. Ba cot nay la CAU HINH TUY CHON, khong phai thuoc tinh cua mot con nguoi. Phan lon nguoi
     dung se khong bao gio chon gi va khong co dong nao o day; `users` thi ai cung co dung mot
     dong. Bang rieng giu dung su that do.

VI SAO CA BA COT DEU NULLABLE, va vi sao KHONG dat server_default = 'gemini-3-flash':

  NULL nghia la *dung `LLM_MODEL` cua he thong*, khong phai *chua chon*. Neu chep gia tri mac
  dinh vao DB thi doi `LLM_MODEL` trong `.env` chi con anh huong nguoi dung moi -- nguoi cu mac
  ket voi model cua ngay ho bam nut, mot model ma ho chua he chon. Do la lech khong ai debug ra
  vi DB "co du lieu dung", chi la dung mot thoi diem khac.

VI SAO KHOA CHINH LA `user_id` chu khong phai `(user_id, feature)`:

  So chuc nang la hang so cua san pham (ba -- ADR muc 4, M2), khong phai du lieu nguoi dung sinh
  ra. Bang khoa kep cho ba gia tri co dinh chi doi lay viec moi lan doc phai gom ba dong.

ON DELETE CASCADE: xoa tai khoan thi lua chon model di theo. Khong co gi dang giu lai -- no chi
co nghia khi gan voi credential OAuth cua chinh nguoi do.

DOWNGRADE that va sach: bo bang. Mat lua chon cua moi nguoi, he thong quay ve dung `LLM_MODEL`
cho ca ba chuc nang -- dung hanh vi truoc EX-13, khong hong gi.

File nay khong dau tieng Viet: xem canh bao o dau alembic.ini (I-07).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Ba chuc nang, dung ba khoa chot o ADR muc 4 (M2). Do dai 128 khop `company_profiles.llm_model`
#: da co tu 0001 -- lech kieu giua model voi migration la I-16.
_MODEL_COLUMNS: tuple[str, ...] = ("ocr_model", "enrich_model", "chat_model")


def upgrade() -> None:
    op.create_table(
        "user_model_prefs",
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        *(sa.Column(name, sa.String(length=128), nullable=True) for name in _MODEL_COLUMNS),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("user_model_prefs")
