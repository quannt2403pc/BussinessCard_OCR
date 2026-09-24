"""Jinja2Templates dùng chung cho mọi router trả HTML.

Chủ sở hữu: Q | Task: 2.4 | xem Task.md

Vì sao có file này: `app/main.py` cũng tạo một `Jinja2Templates`, nhưng router **không import
được từ `main.py`** — `main.py` đã import ngược lại `app.routers`, vòng import sẽ vỡ. Đặt ở
`app/core/` để mọi router (settings, cards, companies…) dùng chung một cấu hình, và giữ đúng
quy ước số 4: không ai phải sửa `main.py` khi thêm trang mới.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.core.security import template_context

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


def _asset_version() -> str:
    """Chuỗi đổi mỗi khi có file trong `static/` đổi — gắn vào URL dạng `?v=…` (task 14.10).

    Vì sao cần: `StaticFiles` không gửi `Cache-Control`, nên Chrome tự suy ra thời hạn bằng
    **10% tuổi của file** (heuristic freshness của RFC 9111). File `app.css` sửa lần cuối vài
    tháng trước thì bản cũ được giữ lại hàng ngày — đúng lỗi đã bắt được lúc nghiệm thu D14:
    trang tải xong mà `.icon` chưa có, icon phình thành 270×150px và trang tràn ngang. Trên máy
    dev thì Ctrl+Shift+R là xong; sau khi deploy lên `ocrximi.io.vn` thì **người dùng không
    biết phải làm thế** — họ chỉ thấy giao diện vỡ.

    Tính **một lần lúc import**, không phải mỗi lượt render — quét cả `static/` cho từng request
    là trả giá thật để chống một lỗi chỉ xảy ra lúc deploy.

    Hệ quả phải biết: `uvicorn --reload` chỉ theo dõi mã Python, nên ở máy dev **sửa mỗi file
    trong `static/` thì con số không đổi** cho tới khi tiến trình khởi động lại — lúc đó vẫn phải
    Ctrl+Shift+R như cũ. Chỗ nó thực sự có tác dụng là production: mỗi lần deploy là một container
    mới, tức một `ASSET_VERSION` mới, tức không một người dùng nào giữ lại CSS của bản trước.
    """
    newest = max((p.stat().st_mtime for p in STATIC_DIR.rglob("*") if p.is_file()), default=0.0)
    return format(int(newest), "x")


ASSET_VERSION = _asset_version()


#: Câu hỏi gợi ý hiện trong bong bóng chat khi hội thoại còn trống.
#:
#: Cố ý chọn câu **không nêu tên riêng nào**: tên công ty trong KB thay đổi theo dữ liệu người
#: dùng nhập, nên gợi ý cứng một cái tên là mời người ta bấm vào một câu chắc chắn không có đáp
#: án. Ba câu này phủ ba kiểu truy hồi khác nhau (ngữ nghĩa, danh thiếp theo người, định danh).
#:
#: Nằm ở đây chứ không ở `routers/chat.py` như trước EX-09: bong bóng chat sống trong `base.html`,
#: tức **mọi** trang render nó, kể cả trang lỗi và trang của T — không route nào truyền nổi biến
#: cho nó. Trước EX-09 hằng số này ở `routers/chat.py` và `ui_context()` phải `import` trong thân
#: hàm để né vòng import; gỡ trang `/assistant` xong thì chỗ đúng của nó là đây, và vòng import
#: biến mất theo.
SAMPLE_QUESTIONS: tuple[str, ...] = (
    "Công ty nào làm về logistics?",
    "Có những ai làm ở vị trí giám đốc kinh doanh?",
    "Danh sách công ty đã có hồ sơ và mã số thuế của họ?",
)


def ui_context(request: Request) -> dict[str, Any]:
    """Biến dùng chung của khung giao diện: câu gợi ý cho bong bóng chat + năm ở chân trang.

    Bong bóng nằm trong `base.html` nên nó hiện trên **mọi** trang — kể cả trang lỗi và các trang
    của T. Không route nào truyền nổi biến cho nó, nên nguồn chữ phải nằm ở một context processor.
    """
    # `year` tính mỗi lượt render chứ không đóng băng lúc import: tiến trình production sống
    # qua giao thừa thì chân trang phải đổi theo, không chờ ai đi restart container.
    return {
        "widget_questions": SAMPLE_QUESTIONS,
        "year": datetime.now(UTC).year,
    }


#: `context_processors` chạy cho **mọi** lượt render, nên `current_user` luôn có trong template
#: (task 12.4). Không dùng nó thì mỗi route HTML — của cả Q lẫn T — phải tự truyền biến người
#: dùng xuống, và route nào quên thì `base.html` mất nút Đăng xuất mà không báo gì.
templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR),
    context_processors=[template_context, ui_context],
)

#: `env.globals` chứ không context processor: `_macros.html` được **import** chứ không include,
#: mà macro đã import thì không nhìn thấy context của trang gọi nó. Sprite icon nằm trong macro
#: nên chỉ có đường này.
templates.env.globals["asset_v"] = ASSET_VERSION
