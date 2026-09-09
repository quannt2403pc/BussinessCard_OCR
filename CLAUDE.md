# BusinessCard_OCR

Số hoá danh thiếp & hồ sơ doanh nghiệp đối tác. Demo localhost, quản lý bằng Docker.
Nhóm 2 người: **Q** (Quân) và **T** (Tùng).

Quy ước làm việc bắt buộc: @AGENTS.md

---

## Ràng buộc được thực thi tự động

Ba hook trong `.claude/settings.json` (mã nguồn ở `.claude/hooks/`):

| Hook | Khi nào | Làm gì |
|------|---------|--------|
| `session_digest.py` | SessionStart | Nạp sẵn tiến độ + danh sách task đang dở vào ngữ cảnh |
| `require_plan_read.py` | PreToolUse trên Edit/Write | **Chặn** sửa `app/`, `templates/`, `embedder/`, `tests/`, `scripts/`, `alembic/`, `static/` nếu phiên này chưa Read cả `Plan.md` lẫn `Task.md` |
| `require_task_update.py` | Stop | **Chặn** kết thúc phiên nếu `git status` cho thấy đã sửa mã nguồn mà `Task.md` không đổi |

Sửa `Plan.md`, `Task.md`, `docs/` thì không bị chặn — nếu không sẽ không cập nhật được trạng thái task.

Bị hook chặn nghĩa là **thiếu bước, không phải lỗi kỹ thuật**: đọc file còn thiếu hoặc cập nhật
`Task.md` rồi làm lại. Đừng tìm cách đi vòng.

## Tài liệu

| File | Nội dung |
|------|----------|
| `Plan.md` | Kiến trúc, thiết kế DB, phân công, rủi ro, tiêu chí nghiệm thu |
| `Task.md` | 103 task theo ngày D1–D11 + **bảng sở hữu file/module** + quy ước chống xung đột Git |
| `docs/README.md` | Chỉ mục tài liệu theo chủ sở hữu và task |
| `.github/workflows/README.md` | CI và cách bật chặn merge |

## Trạng thái hiện tại

Mới có **khung xương thư mục**. Toàn bộ file trong `app/`, `templates/`, `embedder/` là stub,
mỗi file ghi rõ chủ sở hữu và task tạo ra nó ngay trên đầu. Chưa chạy được.
`Dockerfile`, `docker-compose.yml`, `alembic.ini` cũng là stub (task `1.4`, `1.6` của Q).
