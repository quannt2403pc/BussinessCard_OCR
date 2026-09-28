"""Jinja2Templates dùng chung cho mọi router trả HTML.

Chủ sở hữu: Q | Task: 2.4

Router **không import được từ `main.py`** (`main.py` đã import ngược lại `app.routers`, vòng
import sẽ vỡ), nên cấu hình chung đặt ở đây.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.core.config import settings
from app.core.security import template_context

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


def _asset_version() -> str:
    """Chuỗi đổi mỗi khi có file trong `static/` đổi — gắn vào URL dạng `?v=…`.

    `StaticFiles` không gửi `Cache-Control` nên Chrome tự suy thời hạn bằng **10% tuổi của file**:
    `app.css` sửa lần cuối vài tháng trước thì bản cũ được giữ lại hàng ngày. Trên máy dev thì
    Ctrl+Shift+R là xong, sau khi deploy thì người dùng chỉ thấy giao diện vỡ.

    Tính **một lần lúc import**, không phải mỗi lượt render. Hệ quả: ở máy dev sửa file trong
    `static/` thì con số không đổi cho tới khi tiến trình khởi động lại. Chỗ nó thực sự có tác
    dụng là production — mỗi lần deploy là một `ASSET_VERSION` mới.
    """
    newest = max((p.stat().st_mtime for p in STATIC_DIR.rglob("*") if p.is_file()), default=0.0)
    return format(int(newest), "x")


ASSET_VERSION = _asset_version()


#: Câu hỏi gợi ý hiện trong bong bóng chat khi hội thoại còn trống.
#:
#: Cố ý chọn câu **không nêu tên riêng nào**: tên công ty trong KB thay đổi theo dữ liệu người
#: dùng nhập, nên gợi ý cứng một cái tên là mời người ta bấm vào câu chắc chắn không có đáp án.
#: Ba câu phủ ba kiểu truy hồi khác nhau (ngữ nghĩa, danh thiếp theo người, định danh).
SAMPLE_QUESTIONS: tuple[str, ...] = (
    "Công ty nào làm về logistics?",
    "Có những ai làm ở vị trí giám đốc kinh doanh?",
    "Danh sách công ty đã có hồ sơ và mã số thuế của họ?",
)


def ui_context(request: Request) -> dict[str, Any]:
    """Biến dùng chung của khung giao diện: câu gợi ý cho bong bóng chat + năm ở chân trang.

    Bong bóng nằm trong `base.html` nên nó hiện trên **mọi** trang; không route nào truyền nổi
    biến cho nó, nên nguồn chữ phải nằm ở một context processor.
    """
    # `year` tính mỗi lượt render chứ không đóng băng lúc import: tiến trình sống qua giao thừa
    # thì chân trang phải đổi theo.
    return {
        "widget_questions": SAMPLE_QUESTIONS,
        "year": datetime.now(UTC).year,
    }


#: `context_processors` chạy cho **mọi** lượt render, nên `current_user` luôn có trong template.
#: Không dùng nó thì route nào quên truyền biến sẽ mất nút Đăng xuất mà không báo gì.
templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR),
    context_processors=[template_context, ui_context],
)

#: `env.globals` chứ không context processor: `_macros.html` được **import** chứ không include,
#: mà macro đã import thì không nhìn thấy context của trang gọi nó.
templates.env.globals["asset_v"] = ASSET_VERSION

#: `base.html` ẩn liên kết *API* khi Swagger bị tắt. Global chứ không truyền qua từng
#: `TemplateResponse`: thêm một khoá vào 14 chỗ render là 14 cơ hội quên một chỗ.
templates.env.globals["docs_enabled"] = settings.docs_enabled
