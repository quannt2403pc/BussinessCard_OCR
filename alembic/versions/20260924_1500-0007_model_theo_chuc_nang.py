"""Bang user_model_prefs -- moi nguoi dung chon model rieng cho tung chuc nang

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-24 15:00:00.000000+07:00

Task EX-13 (Q). Quyet dinh thiet ke o `docs/adr-model-per-feature.md` muc 4.

RUI RO thap nhat co the: mot bang MOI, khong dong du lieu nao phai viet lai.

Bang rieng chu khong them ba cot vao `users` vi day la CAU HINH TUY CHON, khong phai thuoc tinh
cua mot con nguoi: phan lon nguoi dung khong chon gi va khong co dong nao o day.

Ca ba cot NULLABLE va KHONG dat `server_default`: NULL nghia la *dung `LLM_MODEL` cua he thong*,
khong phai *chua chon*. Chep gia tri mac dinh vao DB thi doi `LLM_MODEL` chi con anh huong nguoi
dung moi, con nguoi cu mac ket voi mot model ho chua he chon.

Khoa chinh la `user_id` chu khong `(user_id, feature)`: so chuc nang la hang so cua san pham.

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
