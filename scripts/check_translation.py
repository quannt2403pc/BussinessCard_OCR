"""Kiểm lượt Việt hoá sau khi quét, chạy qua ĐÚNG API mà giao diện gọi, với model thật.

Chủ sở hữu: Q | Task: EX-07

    docker compose up -d
    .venv/Scripts/python -m scripts.check_translation
    .venv/Scripts/python -m scripts.check_translation --only th --keep

Dùng lại phần dựng ảnh của `scripts/check_multilang_ocr.py`, và cùng lý do phải **chạy ở máy chứ
không trong container**: image `api` không có phông CJK/Thái/Kirin nào.

Ba thứ được kiểm, đúng ba yêu cầu của EX:

1. **Chức vụ và loại hình pháp nhân được DỊCH** — có đáp án đúng nên chấm tự động được.
2. **Tên riêng được PHIÊN ÂM chứ không dịch nghĩa.** Máy chỉ chấm được phần kiểm được: bản Việt
   hoá phải là chữ Latin và bản gốc phải còn nguyên. `李伟` nên là *Lý Vĩ* hay *Li Wei* thì in ra
   cho người đọc.
3. **Ngôn ngữ ngoài 5 thứ tiếng chính** (Thái, Nga, Đức) — ca mà bản trước EX-05 không làm nổi.

Script tự đăng ký một tài khoản riêng để thẻ dựng ra ở đây không trộn vào dữ liệu demo của ai.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from scripts.check_multilang_ocr import BASE_URL, FONTS, Card, render

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

#: Tài khoản riêng của phép đo này. Mật khẩu chỉ dùng trên máy dev, không phải secret —
#: `.gitleaks.toml` không bắt vì nó không phải khoá của dịch vụ nào.
CHECK_EMAIL = "ex-check@bizcard.local"
CHECK_PASSWORD = "excheck12345"

#: Phông bổ sung cho ba ngôn ngữ ngoài 5 thứ tiếng chính (EX-05). Arial phủ được Kirin và chữ
#: Latin có dấu của tiếng Đức; tiếng Thái cần phông riêng.
EXTRA_FONTS: dict[str, tuple[str, ...]] = {
    "th": ("C:/Windows/Fonts/leelawui.ttf", "C:/Windows/Fonts/tahoma.ttf"),
    "ru": ("C:/Windows/Fonts/arial.ttf",),
    "de": ("C:/Windows/Fonts/arial.ttf",),
}


@dataclass(frozen=True)
class ViCase:
    """Một thẻ mẫu + những gì bản Việt hoá của nó **bắt buộc** phải đạt."""

    card: Card
    #: Chức vụ phải dịch ra đúng chuỗi này (bảng tra cứu hoặc model đều phải ra cùng kết quả).
    job_title_vi: str | None = None
    #: `company_name_vi` phải bắt đầu bằng loại hình pháp nhân tiếng Việt này.
    company_prefix: str | None = None
    #: Mã ngôn ngữ mong đợi ở `language_detected`.
    language: str = ""
    #: Tên người phải được phiên âm (thẻ chữ Latin sẵn thì không cần bản dịch nào).
    expect_name_vi: bool = True


CASES: tuple[ViCase, ...] = (
    ViCase(
        card=Card(
            key="ja",
            language="ja",
            lines=(
                ("東京テック株式会社", 32),
                ("", 10),
                ("営業部長", 22),
                ("田中 太郎", 30),
                ("", 10),
                ("TEL: 03-1234-5678", 20),
                ("tanaka@tokyotech.co.jp", 20),
                ("東京都千代田区丸の内1-2-3", 19),
            ),
            expected={},
        ),
        job_title_vi="Trưởng phòng Kinh doanh",
        company_prefix="Công ty Cổ phần",
        language="ja",
    ),
    ViCase(
        card=Card(
            key="zh",
            language="zh",
            lines=(
                ("深圳市远景电子有限公司", 30),
                ("", 10),
                ("李伟", 30),
                ("销售经理", 22),
                ("", 10),
                ("电话: 138 0013 8000", 20),
                ("liwei@yuanjing.com.cn", 20),
                ("广东省深圳市南山区科技园路 18 号", 19),
            ),
            expected={},
        ),
        job_title_vi="Trưởng phòng Kinh doanh",
        company_prefix="Công ty TNHH",
        language="zh",
    ),
    ViCase(
        card=Card(
            key="ko",
            language="ko",
            lines=(
                ("한화정밀기계 주식회사", 30),
                ("", 10),
                ("김민준", 30),
                ("부장", 22),
                ("", 10),
                ("Tel. +82-2-1234-5678", 20),
                ("minjun.kim@hanwha.co.kr", 20),
                ("서울특별시 중구 청계천로 86", 20),
            ),
            expected={},
        ),
        job_title_vi="Trưởng phòng",
        company_prefix="Công ty Cổ phần",
        language="ko",
    ),
    # --- ba ca ngoài 5 ngôn ngữ chính: đây là phần EX-05 mở ra ---
    ViCase(
        card=Card(
            key="th",
            language="th",
            lines=(
                ("บริษัท สยามเทค จำกัด", 30),
                ("", 10),
                ("สมชาย ใจดี", 28),
                ("ผู้จัดการฝ่ายขาย", 22),
                ("", 10),
                ("โทร: 02-123-4567", 20),
                ("somchai@siamtech.co.th", 20),
                ("199 ถนนสีลม กรุงเทพมหานคร", 20),
            ),
            expected={},
        ),
        job_title_vi=None,  # không có trong bảng tra cứu — model phải tự dịch
        company_prefix="Công ty TNHH",
        language="th",
    ),
    ViCase(
        card=Card(
            key="ru",
            language="ru",
            lines=(
                ("ООО Яндекс Технологии", 30),
                ("", 10),
                ("Иван Петров", 28),
                ("Генеральный директор", 22),
                ("", 10),
                ("Тел: +7 495 123-45-67", 20),
                ("ivan.petrov@yandex.ru", 20),
                ("Москва, ул. Льва Толстого, 16", 20),
            ),
            expected={},
        ),
        job_title_vi="Tổng giám đốc",
        company_prefix="Công ty TNHH",
        language="ru",
    ),
    ViCase(
        card=Card(
            key="de",
            language="de",
            lines=(
                ("Müller Maschinenbau GmbH", 30),
                ("", 10),
                ("Hans Müller", 28),
                ("Vertriebsleiter", 22),
                ("", 10),
                ("Tel: +49 30 123456", 20),
                ("hans.mueller@mueller-mb.de", 20),
                ("Hauptstraße 12, 10115 Berlin", 20),
            ),
            expected={},
        ),
        job_title_vi="Trưởng phòng Kinh doanh",
        company_prefix="Công ty TNHH",
        language="de",
        # Thẻ đã viết bằng chữ Latin: tên người KHÔNG cần bản dịch nào, và đúng là không nên có.
        expect_name_vi=False,
    ),
)


@dataclass
class Outcome:
    case: ViCase
    card: dict = field(default_factory=dict)
    card_id: str | None = None
    error: str | None = None
    checks: list[tuple[str, str, bool]] = field(default_factory=list)


# --------------------------------------------------------------------------- hạ tầng


def login(http: httpx.Client) -> None:
    """Đăng nhập, chưa có tài khoản thì đăng ký. Từ D12 mọi route đều đòi đăng nhập."""
    form = {"email": CHECK_EMAIL, "password": CHECK_PASSWORD}
    response = http.post("/auth/login", data=form, follow_redirects=False, timeout=30.0)
    if response.status_code in (302, 303):
        return

    response = http.post(
        "/auth/register",
        data={**form, "password_confirm": CHECK_PASSWORD, "display_name": "EX check"},
        follow_redirects=False,
        timeout=30.0,
    )
    if response.status_code not in (302, 303):
        raise SystemExit(
            f"Không đăng nhập được bằng {CHECK_EMAIL}: HTTP {response.status_code} "
            f"{response.text[:300]}"
        )


def upload(http: httpx.Client, card: Card, image: bytes) -> tuple[dict, str | None]:
    """Quét một ảnh qua `POST /api/cards/upload`. Ảnh trùng thì xoá bản cũ rồi quét lại.

    Cùng lý lẽ với `check_multilang_ocr.upload()`: chống trùng của 3.1 trả 200 + `duplicate` và
    **không gọi lại model**, chấm trên bản cũ là tưởng mình đang đo bản hôm nay.
    """
    files = {"file": (f"ex-{card.key}.jpg", image, "image/jpeg")}
    response = http.post("/api/cards/upload", files=files, timeout=240.0)
    if response.status_code == 200 and response.json().get("duplicate"):
        http.delete(f"/api/cards/{response.json()['card']['id']}", timeout=30.0)
        response = http.post("/api/cards/upload", files=files, timeout=240.0)

    if response.status_code != 201:
        return {}, f"HTTP {response.status_code}: {response.text[:200]}"
    body = response.json()
    if body.get("ocr_error"):
        return body["card"], body["ocr_error"]
    return body["card"], None


def has_native_script(value: str) -> bool:
    """Chuỗi còn sót ký tự ngoài chữ Latin (Hán, Kana, Hangul, Kirin, Thái…)?

    Kiểm theo **tên Unicode**, không theo khoảng mã tự gõ tay — cùng lối với
    `check_multilang_ocr.is_native_script()`, vì Hangul nằm ở ba khối rời nhau.
    """
    for char in value:
        if not char.isalpha():
            continue
        try:
            name = unicodedata.name(char)
        except ValueError:
            continue
        if not name.startswith("LATIN"):
            return True
    return False


# --------------------------------------------------------------------------- chấm điểm


def grade(case: ViCase, card: dict) -> list[tuple[str, str, bool]]:
    """`[(tên phép kiểm, giá trị thật, đạt?)]` — chỉ chấm những gì có đáp án đúng."""
    checks: list[tuple[str, str, bool]] = []

    language = card.get("language_detected") or "—"
    checks.append(("ngôn ngữ", language, language == case.language))

    # Bản gốc KHÔNG được đụng vào: đây là phần bảo vệ quy tắc 3 của prompt OCR.
    raw_name = card.get("full_name") or ""
    expect_raw_native = case.language not in ("de",)
    checks.append(
        (
            "giữ nguyên chữ gốc",
            raw_name or "—",
            bool(raw_name) and has_native_script(raw_name) == expect_raw_native,
        )
    )

    name_vi: str | None = card.get("full_name_vi")
    if case.expect_name_vi:
        checks.append(
            (
                "tên riêng phiên âm",
                name_vi or "—",
                bool(name_vi) and not has_native_script(name_vi or ""),
            )
        )
    else:
        # Thẻ Latin sẵn: có bản dịch nghĩa là hệ thống đang bịa ra một cách viết thứ hai.
        checks.append(("tên riêng giữ nguyên", name_vi or "(không dịch)", name_vi is None))

    title_vi: str | None = card.get("job_title_vi")
    if case.job_title_vi:
        checks.append(("chức vụ dịch đúng", title_vi or "—", title_vi == case.job_title_vi))
    else:
        checks.append(
            (
                "chức vụ có dịch",
                title_vi or "—",
                bool(title_vi) and not has_native_script(title_vi or ""),
            )
        )

    company_vi = card.get("company_name_vi") or ""
    checks.append(
        (
            "loại hình pháp nhân",
            company_vi or "—",
            bool(case.company_prefix) and company_vi.startswith(case.company_prefix or ""),
        )
    )

    meta = card.get("translation_meta") or {}
    checks.append(("nguồn bản dịch", str(meta.get("source")), meta.get("source") == "llm"))
    return checks


def report(outcomes: list[Outcome]) -> int:
    failures = 0
    for outcome in outcomes:
        case = outcome.case
        print(f"\n─── {case.card.key.upper()} ({case.language}) " + "─" * 40)
        if outcome.error:
            print(f"  LỖI: {outcome.error}")
            failures += 1
            continue

        card = outcome.card
        print(
            f"  gốc:  {card.get('full_name')} · {card.get('job_title')} · "
            f"{card.get('company_name_raw')}"
        )
        print(
            f"  Việt: {card.get('full_name_vi') or '—'} · {card.get('job_title_vi') or '—'} · "
            f"{card.get('company_name_vi') or '—'}"
        )
        print(f"  địa chỉ: {card.get('address')}")
        print(f"        → {card.get('address_vi') or '—'}")
        for name, value, ok in outcome.checks:
            mark = "OK  " if ok else "SAI "
            print(f"    [{mark}] {name:<22} {value}")
            if not ok:
                failures += 1
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Kiểm Việt hoá sau khi quét (EX-07)")
    parser.add_argument("--only", help="Chỉ chạy một ca: ja | zh | ko | th | ru | de")
    parser.add_argument("--keep", action="store_true", help="Giữ lại thẻ đã tạo trong DB")
    args = parser.parse_args()

    FONTS.update(EXTRA_FONTS)
    cases = [c for c in CASES if not args.only or c.card.key == args.only]
    out_dir = Path(tempfile.gettempdir()) / "bizcard-ex-translation"
    out_dir.mkdir(parents=True, exist_ok=True)

    outcomes: list[Outcome] = []
    with httpx.Client(base_url=BASE_URL) as http:
        login(http)
        for case in cases:
            try:
                image = render(case.card)
            except FileNotFoundError as exc:
                outcomes.append(Outcome(case=case, error=f"thiếu phông: {exc}"))
                continue
            (out_dir / f"{case.card.key}.jpg").write_bytes(image)

            card, error = upload(http, case.card, image)
            outcome = Outcome(case=case, card=card, card_id=card.get("id"), error=error)
            if not error:
                outcome.checks = grade(case, card)
            outcomes.append(outcome)

        failures = report(outcomes)

        if not args.keep:
            for outcome in outcomes:
                if outcome.card_id:
                    http.delete(f"/api/cards/{outcome.card_id}", timeout=30.0)

    total = sum(len(o.checks) for o in outcomes)
    print(f"\nẢnh đã dựng: {out_dir}")
    print(f"\nKẾT QUẢ: {total - failures}/{total} phép kiểm đạt, {failures} điểm trượt.")
    print(
        "  Cách phiên âm đúng hay không thì ĐỌC bảng ở trên — máy không chấm hộ được\n"
        "  `李伟` nên là *Lý Vĩ* (Hán Việt) hay *Li Wei* (bính âm)."
    )
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
