"""Them companies.display_name_vi va va lai du lieu dang co

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-27 16:00:00.000000+07:00

Task I-36. Danh sach cong ty hien ten nhu in tren the (Han, Trung, A Rap, Thai) trong khi ban
Viet hoa da nam san o business_cards.company_name_vi tu EX-02.

⚠️ Revision nay do T sinh. Luat "chi Q sinh revision" KHONG duoc noi; chu du an duyet rieng cho
lan nay (2026-09-27), va Q phai xac nhan o daily.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("display_name_vi", sa.String(255), nullable=True))

    # Va lai tu chinh ban dich da co tren the. Lay ban dich NGAN NHAT trong so cac the gan voi
    # cong ty do, khong phai ban bat ky: `min()` cho ket qua tat dinh, chay lai van ra the ay.
    #
    # Bo qua cac the ma ban dich trung y het ten goc (`company_name_vi = display_name`): ten von
    # da la tieng Viet thi mot cot phu chep lai y nguyen chi lam giao dien in hai dong giong het
    # nhau.
    op.execute(
        "UPDATE companies c SET display_name_vi = sub.vi FROM ("
        "  SELECT company_id, min(company_name_vi) AS vi FROM business_cards"
        "  WHERE company_id IS NOT NULL AND company_name_vi IS NOT NULL"
        "    AND btrim(company_name_vi) <> ''"
        "  GROUP BY company_id"
        ") sub WHERE sub.company_id = c.id AND btrim(sub.vi) <> btrim(c.display_name)"
    )


def downgrade() -> None:
    """Bo cot. Khong mat gi that: moi gia tri trong do deu chep tu the va sinh lai duoc."""
    op.drop_column("companies", "display_name_vi")
