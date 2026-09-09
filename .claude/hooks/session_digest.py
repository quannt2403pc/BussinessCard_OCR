#!/usr/bin/env python3
"""SessionStart hook — nạp sẵn tình trạng task vào ngữ cảnh đầu phiên.

Không thay cho việc đọc Plan.md / Task.md (hook PreToolUse vẫn bắt buộc), nhưng
cho agent biết ngay đang dở việc gì để khỏi chọn nhầm task.
"""

import os
import re
import sys

# Windows: stdout mac dinh cp1252, tieng Viet co dau se lam hook crash.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

ROW = re.compile(
    r"^\|\s*(\d+\.\d+)\s*\|(.+?)\|\s*([QT])\s*\|\s*([MSC])\s*\|\s*([\d.]+)h\s*\|(.*)\|\s*$"
)


def main() -> None:
    project = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    task_md = os.path.join(project, "Task.md")
    if not os.path.exists(task_md):
        sys.exit(0)

    try:
        with open(task_md, encoding="utf-8") as fh:
            lines = fh.readlines()
    except Exception:
        sys.exit(0)

    doing, blocked, done, total = [], [], 0, 0
    for line in lines:
        m = ROW.match(line.rstrip("\n"))
        if not m:
            continue
        task_id, desc, owner, prio, _hrs, status = m.groups()
        if "❌" in status:
            continue
        total += 1
        label = "{} [{}] {}".format(task_id, owner, desc.strip().strip("`")[:110])
        if "✅" in status:
            done += 1
        elif "🔄" in status:
            doing.append(label + "  — " + status.strip())
        elif "⏸️" in status:
            blocked.append(label + "  — " + status.strip())

    out = [
        "## Tình trạng dự án BusinessCard_OCR (tự động nạp đầu phiên)",
        "",
        f"Tiến độ: **{done}/{total} task xong**.",
        "",
        "**Bắt buộc trước khi sửa mã nguồn:** đọc `Plan.md` và `Task.md`. "
        "Có hook chặn thao tác Edit/Write vào `app/`, `templates/`, `embedder/`, "
        "`tests/`, `scripts/`, `alembic/`, `static/` nếu chưa đọc.",
        "**Bắt buộc sau khi code xong:** cập nhật trạng thái task trong `Task.md`. "
        "Có hook chặn kết thúc phiên nếu quên.",
    ]
    if doing:
        out += ["", "### Đang làm dở"] + ["- " + x for x in doing]
    if blocked:
        out += ["", "### Đang bị chặn"] + ["- " + x for x in blocked]
    if not doing and not blocked:
        out += [
            "",
            "Không có task nào đang dở. Hỏi người dùng task nào tiếp theo trước khi tự chọn.",
        ]

    sys.stdout.write("\n".join(out) + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
