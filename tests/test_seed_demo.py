"""Bộ dữ liệu demo của `scripts/seed.py --demo` có còn khớp với `samples/demo/` không.

Chủ sở hữu: Q | Task: 11.2 | xem Task.md

Không đụng DB và không gọi model — toàn bộ file này chỉ kiểm **tính nhất quán giữa hai nguồn
dữ liệu do hai người khác nhau giữ**: bộ thẻ `samples/demo/` là của T (task 11.7), còn ảnh chụp
hồ sơ `scripts/demo_profiles.json` là của Q (task 11.2, sinh bằng `--capture-profiles`).

Đây đúng là chỗ dễ lệch nhất mà không ai biết: T thêm một thẻ thứ tám hoặc đổi tên công ty trên
một thẻ, lượt seed vẫn chạy xanh, chỉ có điều hồ sơ không gắn vào đâu cả và buổi demo dự phòng
mất một màn hình. Trượt ở đây thì cách sửa luôn là **chụp lại fixture**, không phải sửa test.
"""

from __future__ import annotations

import json

import pytest

from app.services.normalize_company import normalize_company_name
from scripts.seed import (
    DEMO_DIR,
    DEMO_PROFILES_JSON,
    demo_card_emails,
    load_demo_cards,
)

REQUIRED_KEYS = ("file", "language", "company", "full_name", "email")


def test_moi_the_demo_du_truong_va_co_anh_that() -> None:
    cards = load_demo_cards()
    assert len(cards) >= 5, "bộ demo mỏng quá thì không đủ kể chuyện trong 10 phút"

    for card in cards:
        missing = [key for key in REQUIRED_KEYS if not card.get(key)]
        assert not missing, f"thẻ {card.get('file')} thiếu trường {missing}"
        assert (DEMO_DIR / card["file"]).exists(), f"không có file ảnh {card['file']}"


def test_email_demo_khong_trung_va_da_viet_thuong() -> None:
    """`--reset --demo` nhận dữ liệu cũ theo email, nên email trùng nhau là xoá hụt."""
    emails = demo_card_emails()
    assert len(emails) == len(set(emails)), "hai thẻ demo trùng email"
    assert all(email == email.lower() for email in emails)


def test_bo_demo_con_giu_duoc_cap_chong_trung() -> None:
    """Hai thẻ in tên khác nhau về cùng một công ty — màn hình gộp công ty ở phút 3:40.

    Mất cặp này thì lượt seed vẫn chạy, nhưng `/companies` không còn gì để chỉ.
    """
    keys = [normalize_company_name(card["company"]) for card in load_demo_cards()]
    assert len(keys) > len(set(keys)), "không còn cặp thẻ nào trỏ về cùng một công ty"


@pytest.mark.skipif(
    not DEMO_PROFILES_JSON.exists(),
    reason="chưa chụp scripts/demo_profiles.json (chạy `seed --capture-profiles`)",
)
class TestAnhChupHoSo:
    @staticmethod
    def _profiles() -> list[dict]:
        payload = json.loads(DEMO_PROFILES_JSON.read_text(encoding="utf-8"))
        return list(payload.get("profiles", []))

    def test_moi_ho_so_gan_duoc_vao_mot_cong_ty_cua_bo_the(self) -> None:
        card_keys = {normalize_company_name(card["company"]) for card in load_demo_cards()}
        for profile in self._profiles():
            key = normalize_company_name(profile["company"])
            assert key in card_keys, (
                f"hồ sơ {profile['company']} không khớp công ty nào trong samples/demo/cards.json "
                "— chụp lại fixture bằng `seed --capture-profiles`"
            )

    def test_khong_ho_so_nao_khong_nguon(self) -> None:
        """Rủi ro **R4**: trường không có nguồn phải để trống, không bịa.

        Fixture là dữ liệu sẽ hiện nguyên xi trên màn hình khi mạng hỏng, nên nó phải chịu đúng
        luật mà luồng enrich thật chịu.
        """
        for profile in self._profiles():
            sources = profile.get("sources") or {}
            assert sources, f"hồ sơ {profile['company']} không có nguồn nào"
            assert profile.get("status") != "draft", f"hồ sơ {profile['company']} còn dở"

    def test_du_5_truong_co_nguon_theo_tieu_chi_A5(self) -> None:
        for profile in self._profiles():
            sources = profile.get("sources") or {}
            assert len(sources) >= 5, (
                f"hồ sơ {profile['company']} chỉ có {len(sources)} trường có nguồn, "
                "tiêu chí A5 đòi ≥ 5"
            )
