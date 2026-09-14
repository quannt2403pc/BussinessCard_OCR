"""Tiền xử lý ảnh: auto-orient, resize <=1600px, nén JPEG.

Chủ sở hữu: Q | Task: 3.2 | xem Task.md

Chạy giữa bước nhận file (task 3.1) và bước gọi model (task 3.4). Ba việc, mỗi việc một lý do
đo được:

1. **Auto-orient theo EXIF.** Ảnh chụp bằng điện thoại (`<input capture>`, task 4.5) rất hay có
   `Orientation=6`: pixel nằm ngang, chỉ có thẻ EXIF nói "xoay 90°". Trình duyệt tôn trọng thẻ
   đó nên người dùng thấy ảnh đúng chiều, còn model nhận đúng mảng pixel **nằm ngang** — chữ
   xoay 90° làm OCR sai hàng loạt trong khi ảnh nhìn vẫn đẹp. Đây là lỗi âm thầm điển hình.
2. **Resize cạnh dài ≤ 1600px.** Danh thiếp 12MP không cho chữ nét hơn, chỉ làm ảnh base64
   phình lên (mỗi 1MB ảnh thành ~1.37MB trong payload) và kéo dài thời gian gọi LLM.
3. **Nén JPEG.** Chuẩn hoá về một định dạng duy nhất để `services/ocr.py` không phải đoán
   `mime_type`, và để ảnh lưu trong volume `uploads` nhẹ.

Ảnh lưu xuống volume **là bản đã xử lý** — trang chi tiết (task 5.1) hiển thị đúng bản mà model
đã nhìn, tiện đối chiếu khi đo độ chính xác (task 7.8). Bản gốc không giữ lại: demo 11 ngày,
giữ hai bản chỉ tốn dung lượng mà không dùng vào việc gì.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import NoReturn

from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)

#: Cạnh dài tối đa sau khi resize (task 3.2). Ảnh nhỏ hơn thì giữ nguyên, không phóng to.
MAX_EDGE = 1600

#: Chất lượng JPEG. 85 là mức chữ nhỏ trên danh thiếp còn nét mà dung lượng đã giảm rõ.
JPEG_QUALITY = 85

#: Mọi ảnh ra khỏi module này đều là JPEG — hợp đồng với `services/ocr.py` và `llm.generate_vision`.
OUTPUT_MIME = "image/jpeg"

#: Định dạng nhận vào. HEIC **không** có trong danh sách: Pillow không đọc được nếu thiếu
#: `pillow-heif`, và đó là định dạng mặc định của iPhone → thông báo lỗi phải nói rõ (xem
#: `_open`). `llm.SUPPORTED_IMAGE_TYPES` rộng hơn vì nó tả cái model nhận, không phải cái ta gửi.
ACCEPTED_FORMATS: dict[str, str] = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "BMP": "image/bmp",
    "TIFF": "image/tiff",
    "GIF": "image/gif",
}

#: Chặn "decompression bomb": ảnh 100k×100k khai báo hợp lệ nhưng nổ RAM khi giải nén. Kiểm
#: bằng kích thước khai trong header — `Image.open()` chưa giải nén pixel nên rẻ.
MAX_PIXELS = 50_000_000


class ImageError(ValueError):
    """Ảnh không đọc được hoặc không xử lý được. Router dịch thành HTTP 400 (task 3.1)."""


@dataclass(frozen=True, slots=True)
class ProcessedImage:
    """Ảnh JPEG đã sẵn sàng gửi cho model và ghi xuống đĩa."""

    data: bytes
    mime_type: str
    width: int
    height: int
    source_format: str
    source_width: int
    source_height: int

    @property
    def resized(self) -> bool:
        return (self.width, self.height) != (self.source_width, self.source_height)

    @property
    def size_bytes(self) -> int:
        return len(self.data)


def preprocess(raw: bytes) -> ProcessedImage:
    """Chuẩn hoá một ảnh tải lên: auto-orient → resize → JPEG.

    Ném `ImageError` cho mọi đầu vào hỏng; gọi ở router thì chỉ cần bắt đúng loại này.
    """
    with _open(raw) as image:
        source_format = image.format or "UNKNOWN"
        source_size = image.size

        # exif_transpose xoay pixel theo thẻ EXIF **và** gỡ luôn thẻ đó, nên không bị xoay hai lần.
        oriented = ImageOps.exif_transpose(image) or image
        flat = _flatten(oriented)
        flat.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)

        buffer = io.BytesIO()
        flat.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
        width, height = flat.size

    result = ProcessedImage(
        data=buffer.getvalue(),
        mime_type=OUTPUT_MIME,
        width=width,
        height=height,
        source_format=source_format,
        source_width=source_size[0],
        source_height=source_size[1],
    )
    logger.debug(
        "Tiền xử lý ảnh: %s %dx%d (%d byte) -> JPEG %dx%d (%d byte)",
        source_format,
        source_size[0],
        source_size[1],
        len(raw),
        result.width,
        result.height,
        result.size_bytes,
    )
    return result


def sniff_mime(raw: bytes) -> str:
    """Định dạng thật đọc từ nội dung file, **không tin `Content-Type` của client**.

    Trình duyệt gửi `application/octet-stream` cho file lạ, và một file `.jpg` đổi tên từ `.pdf`
    vẫn khai `image/jpeg`. Chỉ nội dung mới nói thật.
    """
    with _open(raw) as image:
        return ACCEPTED_FORMATS[image.format or ""]


# --------------------------------------------------------------------------- nội bộ


def _open(raw: bytes) -> Image.Image:
    """Mở ảnh và kiểm ba điều kiện chặn: đọc được, đúng định dạng, không quá lớn."""
    if not raw:
        return _fail("File rỗng.")

    try:
        image = Image.open(io.BytesIO(raw))
    except UnidentifiedImageError:
        return _fail(
            "Không đọc được file này như một ảnh. Nếu ảnh chụp từ iPhone, rất có thể là định "
            "dạng HEIC — đổi sang JPEG hoặc PNG trước khi tải lên."
        )
    except OSError as exc:  # file cụt, header hỏng
        return _fail(f"Ảnh hỏng hoặc không đầy đủ: {exc}")

    fmt = image.format or ""
    if fmt not in ACCEPTED_FORMATS:
        image.close()
        return _fail(
            f"Định dạng {fmt or 'không rõ'} không được hỗ trợ. "
            f"Chấp nhận: {', '.join(sorted(ACCEPTED_FORMATS))}."
        )

    width, height = image.size
    if width * height > MAX_PIXELS:
        image.close()
        return _fail(
            f"Ảnh quá lớn ({width}x{height} pixel). Giới hạn {MAX_PIXELS // 1_000_000} megapixel."
        )
    return image


def _flatten(image: Image.Image) -> Image.Image:
    """Về RGB, nền trong suốt tô trắng. JPEG không có kênh alpha, để nguyên sẽ ra nền đen."""
    if image.mode == "RGB":
        return image
    if image.mode in ("RGBA", "LA", "P"):
        rgba = image.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, (255, 255, 255))
        canvas.paste(rgba, mask=rgba.split()[-1])
        return canvas
    return image.convert("RGB")


def _fail(message: str) -> NoReturn:
    """Gộp chỗ ném lỗi để `_open` chỉ có một kiểu thoát lỗi duy nhất."""
    raise ImageError(message)
