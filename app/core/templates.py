"""Jinja2Templates dùng chung cho mọi router trả HTML.

Chủ sở hữu: Q | Task: 2.4 | xem Task.md

Vì sao có file này: `app/main.py` cũng tạo một `Jinja2Templates`, nhưng router **không import
được từ `main.py`** — `main.py` đã import ngược lại `app.routers`, vòng import sẽ vỡ. Đặt ở
`app/core/` để mọi router (settings, cards, companies…) dùng chung một cấu hình, và giữ đúng
quy ước số 4: không ai phải sửa `main.py` khi thêm trang mới.
"""

from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.core.security import template_context

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"

#: `context_processors` chạy cho **mọi** lượt render, nên `current_user` luôn có trong template
#: (task 12.4). Không dùng nó thì mỗi route HTML — của cả Q lẫn T — phải tự truyền biến người
#: dùng xuống, và route nào quên thì `base.html` mất nút Đăng xuất mà không báo gì.
templates = Jinja2Templates(directory=str(TEMPLATES_DIR), context_processors=[template_context])
