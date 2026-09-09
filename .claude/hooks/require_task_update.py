#!/usr/bin/env python3
"""Stop hook — chặn kết thúc phiên nếu đã sửa mã nguồn mà chưa cập nhật Task.md.

Quy ước dự án: code xong phải đổi trạng thái task (⬜ → 🔄 → ✅) trong Task.md.
Hook nhìn `git status` để so: có file mã nguồn đổi mà Task.md không đổi thì chặn.
"""

import json
import os
import subprocess
import sys

# Windows: stdout mac dinh cp1252, tieng Viet co dau se lam hook crash.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

GUARDED = ("app/", "templates/", "embedder/", "tests/", "scripts/", "alembic/", "static/")


def allow() -> None:
    sys.exit(0)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        allow()

    # Đã chặn một lần rồi thì thôi, tránh lặp vô hạn.
    if payload.get("stop_hook_active"):
        allow()

    project = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception:
        allow()
    if out.returncode != 0:
        allow()  # không phải git repo thì bỏ qua

    changed_code = []
    task_touched = False
    for line in out.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip().strip('"')
        if " -> " in path:  # file đổi tên
            path = path.split(" -> ", 1)[1]
        path = path.replace("\\", "/")
        if path.endswith("Task.md"):
            task_touched = True
        elif path.startswith(GUARDED):
            changed_code.append(path)

    if not changed_code or task_touched:
        allow()

    preview = ", ".join(sorted(changed_code)[:5])
    if len(changed_code) > 5:
        preview += f" …(+{len(changed_code) - 5} file nữa)"

    print(
        json.dumps(
            {
                "decision": "block",
                "reason": (
                    f"Đã sửa mã nguồn nhưng chưa cập nhật Task.md: {preview}\n\n"
                    "Quy ước dự án: code xong phải đổi trạng thái task trong Task.md "
                    "(⬜ Chưa làm → 🔄 Đang làm → ✅ Xong) và cập nhật dòng "
                    "'Tổng quan: x / 103 task'.\n"
                    "Nếu chưa xong hẳn thì đánh 🔄 kèm phần trăm, ví dụ '🔄 Đang làm — 60%'.\n"
                    "Nếu thay đổi này thật sự không thuộc task nào, ghi một dòng lý do cho "
                    "người dùng rồi dừng lại."
                ),
            },
            ensure_ascii=False,
        )
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
