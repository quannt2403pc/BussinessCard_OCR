import argparse
import sys
from pathlib import Path
from typing import Any

from PIL import Image
from PIL.Image import Resampling

SOURCE = Path("static/img/LogoOCRXimi.png")
OUT_DIR = Path("static/img")
ALPHA_FLOOR = 8
BRAND_BACKGROUND = (255, 255, 255)
MARK_PADDING = 0.08
OG_SIZE = (1200, 630)
OG_LOGO_WIDTH = 720


def content_rows(alpha: Image.Image) -> list[tuple[int, int]]:
    width, height = alpha.size
    bands: list[tuple[int, int]] = []
    start: int | None = None
    for y in range(height + 1):
        filled = y < height and max(alpha.crop((0, y, width, y + 1)).getdata()) > ALPHA_FLOOR
        if filled and start is None:
            start = y
        elif not filled and start is not None:
            bands.append((start, y - 1))
            start = None
    return bands


def band(image: Image.Image, top: int, bottom: int) -> Image.Image:
    cropped = image.crop((0, top, image.width, bottom + 1))
    box = cropped.getchannel("A").getbbox()
    if box is None:
        raise SystemExit("Dải ảnh rỗng — kiểm tra lại ảnh nguồn.")
    return cropped.crop(box)


def first_cluster(image: Image.Image) -> Image.Image:
    alpha = image.getchannel("A")
    width, height = image.size
    start: int | None = None
    for x in range(width + 1):
        filled = x < width and max(alpha.crop((x, 0, x + 1, height)).getdata()) > ALPHA_FLOOR
        if filled and start is None:
            start = x
        elif not filled and start is not None:
            return image.crop((start, 0, x, height))
    raise SystemExit("Không tách được cụm bên trái của biểu tượng.")


def square(image: Image.Image, size: int) -> Image.Image:
    inner = round(size * (1 - 2 * MARK_PADDING))
    scale = min(inner / image.width, inner / image.height)
    resized = image.resize(
        (round(image.width * scale), round(image.height * scale)), Resampling.LANCZOS
    )
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(resized, ((size - resized.width) // 2, (size - resized.height) // 2), resized)
    return canvas


def flatten(image: Image.Image) -> Image.Image:
    canvas = Image.new("RGB", image.size, BRAND_BACKGROUND)
    canvas.paste(image, (0, 0), image)
    return canvas


def on_background(image: Image.Image, size: tuple[int, int], width: int) -> Image.Image:
    scale = width / image.width
    resized = image.resize((width, round(image.height * scale)), Resampling.LANCZOS)
    canvas = Image.new("RGB", size, BRAND_BACKGROUND)
    canvas.paste(
        resized, ((size[0] - resized.width) // 2, (size[1] - resized.height) // 2), resized
    )
    return canvas


def write(image: Image.Image, name: str, **options: Any) -> None:
    target = OUT_DIR / name
    image.save(target, **options)
    print(f"{name:<24} {image.width:>4}x{image.height:<4} {target.stat().st_size / 1024:6.1f} KB")


def main() -> int:
    parser = argparse.ArgumentParser(description="Xuất bộ nhận diện từ logo gốc (task 14.3)")
    parser.add_argument("--source", type=Path, default=SOURCE)
    args = parser.parse_args()

    source = Image.open(args.source).convert("RGBA")
    bands = content_rows(source.getchannel("A"))
    if len(bands) != 2:
        raise SystemExit(f"Cần đúng 2 dải (biểu tượng + chữ), ảnh nguồn có {len(bands)}.")

    mark = band(source, *bands[0])
    full = source.crop(source.getchannel("A").getbbox())

    for size in (64, 128):
        write(square(mark, size), f"logo-mark-{size}.webp", format="WEBP", quality=92, method=6)
    write(
        full.resize((480, round(full.height * 480 / full.width)), Resampling.LANCZOS),
        "logo-full.webp",
        format="WEBP",
        quality=92,
        method=6,
    )
    write(flatten(square(mark, 180)), "apple-touch-icon.png", format="PNG", optimize=True)
    write(on_background(full, OG_SIZE, OG_LOGO_WIDTH), "og-image.png", format="PNG", optimize=True)

    compact = first_cluster(mark)
    compact = compact.crop(compact.getchannel("A").getbbox())
    square(compact, 64).save(
        OUT_DIR / "favicon.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48)]
    )
    print(f"{'favicon.ico':<24} 16/32/48 {(OUT_DIR / 'favicon.ico').stat().st_size / 1024:6.1f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
