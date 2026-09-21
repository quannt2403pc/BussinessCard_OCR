"""Kiểm luồng OCR đa ngôn ngữ (Hàn / Nhật / Trung) đầu–cuối trên model thật.

Chủ sở hữu: Q | Task: 9.3 | xem Task.md

    python -m scripts.check_multilang_ocr            # chạy ở MÁY, không phải trong container
    python -m scripts.check_multilang_ocr --keep     # giữ lại thẻ đã tạo để xem trên UI

⚠️ **Đây KHÔNG phải phép đo của tiêu chí A3.** A3 đòi 30 ảnh *chụp thật* do T chuẩn bị ở task
3.9, và phép đo đó là task **7.8** — vẫn đang ⏸️ vì `samples/` chưa có ảnh nào. Script này sinh
danh thiếp **dựng bằng phông chữ**, tức chữ sắc nét tuyệt đối, không nhoè, không nghiêng, không
loá. Nó trả lời đúng một câu hỏi và chỉ một câu: *khi chữ đọc được rõ ràng, prompt có xử lý
đúng chữ Hàn/Nhật/Trung không* — giữ nguyên chữ bản địa hay tự dịch sang Latin, `language_detected`
có đúng không, thẻ song ngữ thì lấy mặt nào. Những lỗi đó là lỗi **của prompt**, tách được khỏi
lỗi *đọc ảnh mờ*, và sửa được ngay hôm nay mà không cần chờ T.

Vì sao chạy ở máy chứ không trong container: image `api` là `python:3.12-slim`, **không có phông
chữ CJK nào**. Dựng ảnh trong đó thì mọi chữ Hàn/Nhật/Trung ra một hàng ô vuông — và tệ hơn cả
hỏng, nó *trông như* đang chạy. Máy Windows có sẵn `malgun.ttf`, `msgothic.ttc`, `msyh.ttc`.

Ảnh sinh ra nằm ở thư mục tạm, **không ghi vào `samples/`** — thư mục đó của T (quy ước số 2),
và trộn ảnh dựng-bằng-phông vào bộ ảnh chụp thật sẽ làm hỏng chính phép đo A3 sau này.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import unicodedata
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# Windows: stdout mặc định cp1252 khi không phải console (pipe, `> log.txt`, CI), nên dòng in
# đầu tiên có chữ "đã" là `UnicodeEncodeError` và script chết trước khi gọi API lần nào. Bắt
# được ở task 10.1 lúc chạy `... | tail`. Cùng cách xử lý với `.claude/hooks/*.py`.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

#: API của ứng dụng đang chạy trong Docker.
BASE_URL = "http://localhost:8000"

#: Phông có sẵn trên Windows cho từng ngôn ngữ. Thiếu phông thì bỏ qua ca đó và nói rõ, chứ
#: không vẽ bằng phông Latin — chữ CJK vẽ bằng phông Latin ra ô vuông, và ô vuông thì model
#: đọc được đúng thứ nó nhìn thấy: không có gì.
FONTS: dict[str, tuple[str, ...]] = {
    "ko": ("C:/Windows/Fonts/malgun.ttf",),
    "ja": ("C:/Windows/Fonts/msgothic.ttc", "C:/Windows/Fonts/meiryo.ttc"),
    "zh": ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simsun.ttc"),
    "vi": ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf"),
    "en": ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf"),
    "latin": ("C:/Windows/Fonts/arial.ttf",),
}


#: ⚠️ Đáp án của `website` ghi kèm `https://` dù **thẻ không in scheme**. Đây không phải sơ suất:
#: `services/normalize.py::normalize_website()` (task 3.6) cố ý thêm scheme khi thiếu, và giá trị
#: trả về từ API là bản **đã chuẩn hoá**. Lượt chạy đầu ngày 2026-09-18 báo TRƯỢT ở đúng hai ô
#: này — hoá ra là bài test sai kỳ vọng chứ không phải model bịa thêm chữ.


@dataclass(frozen=True)
class Card:
    """Một danh thiếp mẫu: các dòng đem vẽ + đáp án đúng của từng trường."""

    key: str
    language: str
    lines: tuple[tuple[str, int], ...]
    expected: dict[str, str]
    note: str = ""
    #: Trường chỉ kiểm "có giữ nguyên chữ bản địa không", không so khớp tuyệt đối.
    script_fields: tuple[str, ...] = ("full_name", "company_name_raw")


CARDS: tuple[Card, ...] = (
    Card(
        key="ko",
        language="ko",
        lines=(
            ("한화정밀기계", 34),
            ("Hanwha Precision Machinery", 20),
            ("", 10),
            ("김민준", 30),
            ("영업 과장 / Sales Manager", 20),
            ("", 10),
            ("Tel. +82-2-1234-5678", 20),
            ("minjun.kim@hanwha.co.kr", 20),
            ("서울특별시 중구 청계천로 86", 20),
            ("www.hanwha.co.kr", 20),
        ),
        expected={
            "full_name": "김민준",
            "company_name_raw": "한화정밀기계",
            "email": "minjun.kim@hanwha.co.kr",
            "website": "https://www.hanwha.co.kr",
        },
        note="thẻ song ngữ Hàn–Anh: phải lấy mặt chữ Hàn cho tên và công ty",
    ),
    Card(
        key="ja",
        language="ja",
        lines=(
            ("東京テック株式会社", 32),
            ("", 10),
            ("営業部長", 22),
            ("田中 太郎", 30),
            ("", 10),
            ("TEL: 03-1234-5678", 20),
            ("携帯: 090-8765-4321", 20),
            ("tanaka@tokyotech.co.jp", 20),
            ("東京都千代田区丸の内1-2-3", 19),
        ),
        expected={
            "full_name": "田中 太郎",
            "company_name_raw": "東京テック株式会社",
            "email": "tanaka@tokyotech.co.jp",
            "job_title": "営業部長",
        },
        note="thẻ thuần Nhật, hai số điện thoại → phone + phone_alt",
    ),
    Card(
        key="zh",
        language="zh",
        lines=(
            ("深圳市远景电子有限公司", 30),
            ("", 10),
            ("李伟", 30),
            ("市场部经理", 22),
            ("", 10),
            ("电话: +86 755 8888 6666", 20),
            ("邮箱: li.wei@yuanjing-elec.cn", 19),
            ("地址: 深圳市南山区科技园南路 18 号", 18),
            ("www.yuanjing-elec.cn", 20),
        ),
        expected={
            "full_name": "李伟",
            "company_name_raw": "深圳市远景电子有限公司",
            "email": "li.wei@yuanjing-elec.cn",
            "website": "https://www.yuanjing-elec.cn",
        },
        note="thẻ thuần Trung, nhãn trường viết bằng chữ Hán (电话/邮箱/地址)",
    ),
    # Hai thẻ dưới thêm ở task 10.8: 9.3 chỉ cần ba thẻ CJK vì nó hỏi riêng về chữ bản địa,
    # còn 10.8 đo *độ chính xác trường* nên phải phủ cả hai ngôn ngữ chính của sản phẩm.
    Card(
        key="vi",
        language="vi",
        lines=(
            ("CÔNG TY CỔ PHẦN LOGISTICS HẢI ĐĂNG", 28),
            ("", 8),
            ("Nguyễn Thị Mai Anh", 30),
            ("Giám đốc Kinh doanh", 22),
            ("", 8),
            ("ĐT: 0912 345 678", 20),
            ("Email: maianh.nguyen@haidang-logistics.vn", 19),
            ("Địa chỉ: 145 Nguyễn Văn Linh, Quận 7, TP. Hồ Chí Minh", 18),
            ("www.haidang-logistics.vn", 20),
        ),
        expected={
            "full_name": "Nguyễn Thị Mai Anh",
            "company_name_raw": "CÔNG TY CỔ PHẦN LOGISTICS HẢI ĐĂNG",
            "job_title": "Giám đốc Kinh doanh",
            "email": "maianh.nguyen@haidang-logistics.vn",
            "website": "https://www.haidang-logistics.vn",
        },
        note="thẻ tiếng Việt có dấu, nhãn trường viết tắt (ĐT:)",
        script_fields=(),
    ),
    Card(
        key="en",
        language="en",
        lines=(
            ("NORTHWIND MARINE SUPPLIES LTD.", 28),
            ("", 8),
            ("Daniel Whitmore", 30),
            ("Chief Technology Officer", 22),
            ("", 8),
            ("Mobile: +44 7700 900123", 20),
            ("daniel.whitmore@northwind-marine.co.uk", 19),
            ("22 Harbour Road, Southampton SO14 3QT, United Kingdom", 18),
            ("northwind-marine.co.uk", 20),
        ),
        expected={
            "full_name": "Daniel Whitmore",
            "company_name_raw": "NORTHWIND MARINE SUPPLIES LTD.",
            "job_title": "Chief Technology Officer",
            "email": "daniel.whitmore@northwind-marine.co.uk",
            "website": "https://northwind-marine.co.uk",
        },
        note="thẻ tiếng Anh, nhãn Mobile: → quy tắc 4a đưa số di động vào phone",
        script_fields=(),
    ),
)


@dataclass
class Result:
    card: Card
    ocr: dict = field(default_factory=dict)
    card_id: str | None = None
    error: str | None = None
    ocr_ms: int | None = None


# --------------------------------------------------------------------------- vẽ ảnh


def load_font(language: str, size: int) -> ImageFont.FreeTypeFont:
    for path in (*FONTS[language], *FONTS["latin"]):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise FileNotFoundError(f"Không có phông nào cho {language!r}: {FONTS[language]}")


def render(card: Card) -> bytes:
    """Vẽ danh thiếp 1000×600 trên nền trắng, chữ đen — dễ đọc nhất có thể."""
    image = Image.new("RGB", (1000, 600), "white")
    draw = ImageDraw.Draw(image)
    y = 60
    for text, size in card.lines:
        if text:
            draw.text((60, y), text, font=load_font(card.language, size), fill="black")
        y += size + 14

    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


# --------------------------------------------------------------------------- chạy OCR


def _post(http: httpx.Client, card: Card, image: bytes) -> httpx.Response:
    return http.post(
        "/api/cards/upload",
        files={"file": (f"multilang-{card.key}.jpg", image, "image/jpeg")},
        timeout=180.0,
    )


def upload(http: httpx.Client, card: Card, image: bytes) -> Result:
    """Quét một ảnh. Ảnh đã có trong DB thì **xoá bản cũ rồi quét lại**, không chấm lại bản cũ.

    Chống trùng của task 3.1 trả **200 + `duplicate: true`** và cố tình không gọi lại model —
    đúng cho người dùng, sai cho một phép đo: chấm trên bản cũ nghĩa là tưởng mình vừa đo prompt
    hiện tại trong khi thật ra đang đọc lại kết quả của prompt hôm trước. Đúng chuyện đã xảy ra
    ở lượt chạy 10.8 đầu tiên (2026-09-21): một lượt crash giữa chừng nên `cleanup()` không chạy,
    lượt sau gặp 5 ảnh trùng và báo "LỖI: HTTP 200" cho cả năm thẻ.
    """
    try:
        response = _post(http, card, image)
        if response.status_code == 200 and response.json().get("duplicate"):
            stale = response.json()["card"]["id"]
            http.delete(f"/api/cards/{stale}", timeout=30.0)
            response = _post(http, card, image)
    except httpx.HTTPError as exc:
        return Result(card=card, error=f"không gọi được API: {exc}")

    if response.status_code != 201:
        return Result(card=card, error=f"HTTP {response.status_code}: {response.text[:200]}")

    body = response.json()
    if body.get("ocr_error"):
        return Result(card=card, card_id=body["card"]["id"], error=body["ocr_error"])
    return Result(
        card=card,
        ocr=body["card"],
        card_id=body["card"]["id"],
        ocr_ms=body.get("ocr_ms"),
    )


# --------------------------------------------------------------------------- chấm điểm


def is_native_script(value: str, language: str) -> bool:
    """Chuỗi có chứa chữ bản địa không — dùng để bắt lỗi model phiên âm sang Latin.

    Kiểm theo **tên Unicode của ký tự**, không theo khoảng mã tự gõ tay: tên ký tự do chuẩn
    Unicode đặt, còn khoảng mã chép tay là chỗ sai lặng lẽ (Hangul nằm ở ba khối rời nhau).
    """
    marks = {"ko": "HANGUL", "ja": ("CJK", "HIRAGANA", "KATAKANA"), "zh": "CJK"}
    wanted = marks[language]
    prefixes = (wanted,) if isinstance(wanted, str) else wanted
    for char in value:
        try:
            name = unicodedata.name(char)
        except ValueError:
            continue
        if any(name.startswith(prefix) for prefix in prefixes):
            return True
    return False


def grade(result: Result) -> list[tuple[str, str, str, bool]]:
    """(trường, mong đợi, nhận được, đạt) cho từng trường có đáp án."""
    rows = []
    for name, want in result.card.expected.items():
        got = (result.ocr.get(name) or "").strip()
        if name in result.card.script_fields:
            # Hai điều kiện: khớp chữ, **và** còn là chữ bản địa. Model trả "Kim Min-jun" thay
            # cho "김민준" là sai đúng thứ quy tắc 3 của prompt cấm, nhưng nhìn qua vẫn "có vẻ đúng".
            ok = got == want and is_native_script(got, result.card.language)
        else:
            ok = got.casefold() == want.casefold()
        rows.append((name, want, got or "(trống)", ok))
    return rows


def report(results: list[Result]) -> tuple[int, int, int]:
    """In chi tiết từng thẻ. Trả `(điểm trượt, số trường đã chấm, số trường đúng)` cho 10.8."""
    failures = 0
    graded = 0
    correct = 0
    for result in results:
        print(f"\n=== {result.card.key.upper()} — {result.card.note} ===")
        if result.error:
            print(f"  LỖI: {result.error}")
            failures += 1
            # Thẻ quét hỏng vẫn vào mẫu số. Bỏ ra thì càng nhiều ảnh trượt tỉ lệ càng đẹp —
            # đúng kiểu con số nói ngược lại sự thật nó được sinh ra để đo.
            graded += len(result.card.expected) + 1
            continue

        detected = result.ocr.get("language_detected")
        language_ok = detected == result.card.language
        failures += 0 if language_ok else 1
        graded += 1
        correct += 1 if language_ok else 0
        print(
            f"  {'ĐẠT ' if language_ok else 'TRƯỢT'} language_detected: "
            f"mong đợi {result.card.language!r}, nhận {detected!r}"
        )

        for name, want, got, ok in grade(result):
            failures += 0 if ok else 1
            graded += 1
            correct += 1 if ok else 0
            print(f"  {'ĐẠT ' if ok else 'TRƯỢT'} {name:<18} mong đợi {want!r} · nhận {got!r}")

        # Hai số trên thẻ Nhật: `phone_alt` phải có, không được gộp vào `phone`.
        if result.card.key == "ja":
            alt = result.ocr.get("phone_alt")
            ok = bool(alt)
            failures += 0 if ok else 1
            graded += 1
            correct += 1 if ok else 0
            print(
                f"  {'ĐẠT ' if ok else 'TRƯỢT'} {'phone_alt':<18} phone={result.ocr.get('phone')!r} alt={alt!r}"
            )

        print(f"  (ocr {result.ocr_ms} ms)")
    return failures, graded, correct


def cleanup(http: httpx.Client, results: list[Result]) -> None:
    for result in results:
        if result.card_id:
            http.delete(f"/api/cards/{result.card_id}", timeout=30.0)


def blur(data: bytes, radius: float) -> bytes:
    """Làm nhoè ảnh đã dựng — mô phỏng mức khó `blur` trong `samples/expected.json`.

    Thêm ở task 10.8. Ảnh dựng bằng phông luôn sắc nét tuyệt đối, nên đo trên nó xong mà kết
    luận "OCR chính xác 100%" là kết luận về một thứ người dùng không bao giờ chụp được. Nhoè
    có kiểm soát chưa thay được ảnh chụp thật (task 3.9 của T) nhưng ít nhất cho biết prompt
    còn đứng được tới đâu khi chữ bắt đầu mất nét.
    """
    with Image.open(BytesIO(data)) as image:
        out = image.convert("RGB").filter(ImageFilter.GaussianBlur(radius))
        buffer = BytesIO()
        out.save(buffer, format="JPEG", quality=90)
        return buffer.getvalue()


def run_round(
    http: httpx.Client, cards: list[Card], out_dir: Path, *, radius: float, keep: bool
) -> tuple[int, int, int]:
    """Một lượt đo trên toàn bộ thẻ. Trả `(điểm trượt, số trường đã chấm, số trường đúng)`."""
    label = "sac net" if radius <= 0 else f"mo (ban kinh {radius:g})"
    print(f"\n########## LUOT: {label} ##########")

    results: list[Result] = []
    for card in cards:
        image = render(card)
        suffix = "" if radius <= 0 else f"-blur{radius:g}"
        if radius > 0:
            image = blur(image, radius)
        (out_dir / f"{card.key}{suffix}.jpg").write_bytes(image)
        print(f"[{card.key}{suffix}] da dung anh, dang goi OCR...", flush=True)
        results.append(upload(http, card, image))

    failures, graded, correct = report(results)
    if not keep:
        cleanup(http, results)
    return failures, graded, correct


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Kiểm OCR đa ngôn ngữ (9.3) + đo độ chính xác trường (10.8)"
    )
    parser.add_argument("--keep", action="store_true", help="Giữ lại thẻ đã tạo trong DB")
    parser.add_argument("--only", help="Chỉ chạy một ca: vi | en | ko | ja | zh")
    parser.add_argument(
        "--blur",
        type=float,
        default=0.0,
        metavar="R",
        help="Chạy thêm một lượt ảnh nhoè bán kính R (10.8); 2.5 là mức chữ bắt đầu mất nét rõ",
    )
    args = parser.parse_args()

    cards = [c for c in CARDS if not args.only or c.key == args.only]
    out_dir = Path(tempfile.gettempdir()) / "bizcard-multilang"
    out_dir.mkdir(parents=True, exist_ok=True)

    rounds: list[tuple[str, int, int, int]] = []
    with httpx.Client(base_url=BASE_URL) as http:
        failures, graded, correct = run_round(http, cards, out_dir, radius=0.0, keep=args.keep)
        rounds.append(("sắc nét", failures, graded, correct))

        if args.blur > 0:
            blurred = run_round(http, cards, out_dir, radius=args.blur, keep=args.keep)
            rounds.append((f"mờ r={args.blur:g}", *blurred))

    print(f"\nẢnh đã dựng: {out_dir}")
    print("\n=========== ĐỘ CHÍNH XÁC TRƯỜNG ===========")
    print(f"  {'Lượt':<12} {'Đúng/Tổng':>11}  {'Tỉ lệ':>7}  {'Điểm TRƯỢT':>11}")
    for label, failed, graded, correct in rounds:
        rate = (correct / graded * 100) if graded else 0.0
        print(f"  {label:<12} {f'{correct}/{graded}':>11}  {rate:>6.1f}%  {failed:>11}")
    print(
        "\n  ⚠️ KHÔNG phải phép đo tiêu chí A3 (≥85% trên 30 ảnh CHỤP thật). Ảnh ở đây dựng\n"
        "     bằng phông chữ; phép đo A3 là task 7.8, vẫn ⏸️ vì `samples/cards/` chưa có ảnh nào."
    )
    # Mã thoát chỉ tính lượt sắc nét: lượt nhoè là số đo để theo dõi, không phải điều kiện đạt.
    return 0 if rounds[0][1] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
