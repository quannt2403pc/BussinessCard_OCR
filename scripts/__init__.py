"""Script chạy tay: seed dữ liệu demo, đo truy hồi, spike khảo sát.

Chủ sở hữu: Q | Task: 9.2 | xem Task.md

Có file này để `scripts` là **một package thật**, vì hai lý do:

1. `scripts/seed.py` (9.2) import lại bộ dữ liệu cố định từ `scripts.eval_retrieval` thay vì
   chép sang một bản thứ hai.
2. Không có `__init__.py` thì mypy nhìn cùng một file dưới hai tên module (`eval_retrieval` khi
   quét thư mục, `scripts.eval_retrieval` khi lần theo import) và dừng với
   *"Source file found twice under different module names"* — CI đỏ ngay ở bước `mypy`.

Chạy bằng `python -m scripts.<tên>` từ thư mục gốc (trong container là `/app`), không chạy thẳng
`python scripts/<tên>.py` — chạy thẳng thì `sys.path[0]` là `scripts/` và gói `app` biến mất.
"""
