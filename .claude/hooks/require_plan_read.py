#!/usr/bin/env python3
"""PreToolUse hook — chặn sửa mã nguồn nếu chưa đọc Plan.md và Task.md.

Đọc JSON của hook trên stdin, soi transcript của phiên xem đã có lần Read nào
nhắm vào Plan.md / Task.md chưa. Chưa thì từ chối kèm lý do.

Chỉ gác các thư mục mã nguồn. Sửa chính Plan.md / Task.md / docs/ thì luôn cho qua,
nếu không agent sẽ không cập nhật được trạng thái task.
"""

import json
import os
import sys

# Windows: stdout mac dinh cp1252, tieng Viet co dau se lam hook crash.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Thư mục bị gác. Sửa file trong đây phải đọc kế hoạch trước.
GUARDED = ("app/", "templates/", "embedder/", "tests/", "scripts/", "alembic/", "static/")
REQUIRED = ("Plan.md", "Task.md")


def allow() -> None:
    sys.exit(0)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        allow()  # hook hỏng thì không được chặn công việc

    tool_input = payload.get("tool_input") or {}
    raw_path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    if not raw_path:
        allow()

    rel = raw_path.replace("\\", "/")
    project = (os.environ.get("CLAUDE_PROJECT_DIR") or "").replace("\\", "/").rstrip("/")
    if project and rel.startswith(project + "/"):
        rel = rel[len(project) + 1 :]
    rel = rel.lstrip("./")

    if not rel.startswith(GUARDED):
        allow()

    # Không đọc được transcript thì cho qua. Một hook chặn sạch mọi thao tác vì lý do
    # kỹ thuật còn tệ hơn là không có hook — lớp nhắc trong CLAUDE.md vẫn còn đó.
    transcript = payload.get("transcript_path") or ""
    if not transcript or not os.path.exists(transcript):
        allow()

    seen = set()
    try:
        with open(transcript, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                for name in REQUIRED:
                    # Đủ dùng: transcript ghi lại lời gọi Read kèm đường dẫn.
                    if name in line and '"Read"' in line:
                        seen.add(name)
    except Exception:
        allow()

    missing = [n for n in REQUIRED if n not in seen]
    if not missing:
        allow()

    reason = (
        "Chưa đọc {} trong phiên này. Quy ước dự án: đọc kế hoạch trước khi code.\n"
        "Hãy Read {} rồi mới sửa `{}`.\n"
        "Cần xác định: task này số mấy, chủ sở hữu là Q hay T, và file có thuộc quyền "
        "sở hữu của mình không (bảng sở hữu ở đầu Task.md)."
    ).format(" và ".join(missing), " và ".join(missing), rel)

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            },
            ensure_ascii=False,
        )
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
