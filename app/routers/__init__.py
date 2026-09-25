"""Router stub khai báo sẵn từ D1: cards, companies, integration, chat, kb, stats, export.

Chủ sở hữu: Q | Task: 1.2 | xem Task.md

Quy ước số 4 (Task.md): `app/main.py` và file này khai sẵn TOÀN BỘ router ngay từ D1 để về sau
không ai phải sửa hai file dùng chung khi thêm tính năng.

Cách hoạt động: `iter_routers()` duyệt đúng 7 module dưới đây và **chỉ nạp module nào đã khai
biến `router` là một `APIRouter`**. Module còn là stub thì bỏ qua, không lỗi.

→ Chủ sở hữu mỗi router chỉ cần thêm vào file của mình:

    from fastapi import APIRouter
    router = APIRouter(prefix="/api/cards", tags=["cards"])

là router tự được gắn vào app. Không ai phải chạm `main.py` hay file này nữa.
Prefix và tags do chủ sở hữu router tự quyết trong file của mình (xem Plan.md mục 4).
"""

from collections.abc import Iterator
from importlib import import_module

from fastapi import APIRouter

#: Bảy router của dự án, kèm chủ sở hữu — xem bảng sở hữu ở đầu Task.md.
ROUTER_MODULES: tuple[str, ...] = (
    "cards",  # Q — task 3.1
    "companies",  # T — task 5.4
    "integration",  # Q — task 2.4
    "chat",  # Q — task 8.2
    "kb",  # Q — task 6.4
    "stats",  # T — task 7.7
    "export",  # T — task 7.6
    "auth",  # T — task 12.2
    "contacts",  # T — task NEXT-01
    "events",  # T — task NEXT-03
    "duplicates",  # T — task NEXT-04
    "refresh",  # T — task NEXT-06
    "privacy",  # T — task NEXT-07
    "signature",  # T — task NEXT-08
)


def iter_routers() -> Iterator[tuple[str, APIRouter]]:
    """Trả về (tên module, router) cho những router đã được triển khai."""
    for name in ROUTER_MODULES:
        module = import_module(f"{__name__}.{name}")
        router = getattr(module, "router", None)
        if isinstance(router, APIRouter):
            yield name, router
