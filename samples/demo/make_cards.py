import json
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
FONTS = Path("C:/Windows/Fonts")
SIZE = (1050, 600)


@dataclass(frozen=True)
class Card:
    file: str
    language: str
    brand: str
    color: str
    company: str
    full_name: str
    job_title: str
    phone: str
    email: str
    website: str
    address: str
    shows: str


CARDS = (
    Card(
        "vi-01-clear.png",
        "vi",
        "VINAMILK",
        "#0a3d91",
        "CÔNG TY CỔ PHẦN SỮA VIỆT NAM",
        "TRẦN MINH KHOA",
        "Trưởng phòng Kinh doanh",
        "0900 000 001",
        "khoa.tran@vinamilk.example.com",
        "www.vinamilk.com.vn",
        "Số 10 Tân Trào, P. Tân Phú, Quận 7, TP. Hồ Chí Minh",
        "Thẻ tiếng Việt chuẩn; tạo hồ sơ DN có nguồn (A5)",
    ),
    Card(
        "vi-02-clear.png",
        "vi",
        "HÒA PHÁT",
        "#b3261e",
        "CÔNG TY CỔ PHẦN TẬP ĐOÀN HÒA PHÁT",
        "LÊ THU HÀ",
        "Giám đốc Mua hàng",
        "0900 000 002",
        "ha.le@hoaphat.example.com",
        "www.hoaphat.com.vn",
        "66 Nguyễn Du, Q. Hai Bà Trưng, Hà Nội",
        "Cặp chống trùng với en-03: hai tên in khác nhau về cùng một công ty",
    ),
    Card(
        "en-01-clear.png",
        "en",
        "COTECCONS",
        "#f28c28",
        "COTECCONS CONSTRUCTION JOINT STOCK COMPANY",
        "NGUYEN DUC ANH",
        "Project Director",
        "+84 900 000 003",
        "anh.nguyen@coteccons.example.com",
        "www.coteccons.vn",
        "236/6 Dien Bien Phu St., Binh Thanh Dist., Ho Chi Minh City",
        "Thẻ tiếng Anh của công ty Việt Nam",
    ),
    Card(
        "en-02-clear.png",
        "en",
        "SAMSUNG",
        "#1428a0",
        "SAMSUNG ELECTRONICS VIETNAM CO., LTD.",
        "PARK JI-HOON",
        "Senior Sales Manager",
        "+84 900 000 004",
        "jihoon.park@samsung.example.com",
        "www.samsung.com",
        "Yen Phong Industrial Park, Bac Ninh, Vietnam",
        "Cặp cùng tên miền với ko-01: gợi ý cùng tập đoàn, không gộp",
    ),
    Card(
        "en-03-clear.png",
        "en",
        "HOA PHAT",
        "#b3261e",
        "HOA PHAT GROUP JSC",
        "PHAM QUOC BAO",
        "Export Sales Manager",
        "+84 900 000 007",
        "bao.pham@hoaphat.example.com",
        "www.hoaphat.com.vn",
        "66 Nguyen Du St., Hai Ba Trung Dist., Hanoi",
        "Cặp chống trùng với vi-02 (tên tiếng Anh + 'Group')",
    ),
    Card(
        "ja-01-clear.png",
        "ja",
        "HITACHI",
        "#c8102e",
        "株式会社日立製作所",
        "山田 花子",
        "営業統括本部 課長",
        "+81 3-0000-0005",
        "hanako.yamada@hitachi.example.com",
        "www.hitachi.co.jp",
        "東京都千代田区丸の内一丁目6番6号",
        "Thẻ tiếng Nhật (A4)",
    ),
    Card(
        "ko-01-clear.png",
        "ko",
        "SAMSUNG",
        "#1428a0",
        "삼성전자 주식회사",
        "김민수",
        "영업부 차장",
        "+82 2-0000-0006",
        "minsu.kim@samsung.example.com",
        "www.samsung.com",
        "경기도 수원시 영통구 삼성로 129",
        "Thẻ tiếng Hàn (A4); cặp cùng tên miền với en-02",
    ),
)


def font(language: str, bold: bool, size: int) -> ImageFont.FreeTypeFont:
    if language == "ja":
        return ImageFont.truetype(str(FONTS / ("YuGothB.ttc" if bold else "YuGothM.ttc")), size)
    if language == "ko":
        return ImageFont.truetype(str(FONTS / ("malgunbd.ttf" if bold else "malgun.ttf")), size)
    return ImageFont.truetype(str(FONTS / ("arialbd.ttf" if bold else "arial.ttf")), size)


def fit(draw: ImageDraw.ImageDraw, text: str, language: str, bold: bool, size: int, width: int):
    while size > 14:
        face = font(language, bold, size)
        if draw.textlength(text, font=face) <= width:
            return face
        size -= 2
    return font(language, bold, size)


def render(card: Card) -> Image.Image:
    image = Image.new("RGB", SIZE, "white")
    draw = ImageDraw.Draw(image)
    band = 300
    draw.rectangle((0, 0, band, SIZE[1]), fill=card.color)
    brand = fit(draw, card.brand, "en", True, 54, band - 40)
    box = draw.textbbox((0, 0), card.brand, font=brand)
    draw.text(
        ((band - (box[2] - box[0])) / 2, (SIZE[1] - (box[3] - box[1])) / 2 - 10),
        card.brand,
        font=brand,
        fill="white",
    )

    left, right = band + 50, SIZE[0] - 40
    width = right - left
    y = 50
    draw.text(
        (left, y),
        card.company,
        font=fit(draw, card.company, card.language, True, 30, width),
        fill=card.color,
    )
    y += 90
    draw.text(
        (left, y),
        card.full_name,
        font=fit(draw, card.full_name, card.language, True, 46, width),
        fill="#111111",
    )
    y += 62
    draw.text((left, y), card.job_title, font=font(card.language, False, 28), fill="#444444")
    y += 40
    draw.line((left, y + 20, right, y + 20), fill="#dddddd", width=2)
    y += 50
    labels = {
        "vi": ("ĐT", "Email", "Web", "Đ/c"),
        "en": ("Mobile", "Email", "Web", "Address"),
        "ja": ("TEL", "Email", "Web", "住所"),
        "ko": ("전화", "Email", "Web", "주소"),
    }[card.language]
    for label, value in zip(
        labels, (card.phone, card.email, card.website, card.address), strict=True
    ):
        text = f"{label}: {value}"
        draw.text(
            (left, y), text, font=fit(draw, text, card.language, False, 25, width), fill="#222222"
        )
        y += 44
    return image


def main() -> None:
    for card in CARDS:
        render(card).save(HERE / card.file, optimize=True)
    manifest = [
        {key: value for key, value in asdict(card).items() if key not in {"brand", "color"}}
        for card in CARDS
    ]
    (HERE / "cards.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{len(CARDS)} cards written to {HERE}")


if __name__ == "__main__":
    main()
