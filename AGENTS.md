# Quy ước cho AI agent làm việc trên repo này

Áp dụng cho mọi trợ lý code (Claude Code, Cursor, Copilot, Codex…).
Với Claude Code, các quy ước 1 và 3 còn được **hook chặn tự động** — xem `.claude/settings.json`.

---

## 1. TRƯỚC khi viết code — bắt buộc đọc kế hoạch

Đọc **`Plan.md`** và **`Task.md`** trước mọi thay đổi trong `app/`, `templates/`, `embedder/`,
`tests/`, `scripts/`, `alembic/`, `static/`.

Đọc xong phải trả lời được ba câu, nêu rõ trong phản hồi trước khi sửa file:

1. **Task số mấy?** Mọi thay đổi phải gắn với một task có sẵn trong `Task.md` (ví dụ `3.4`, `6.5`).
   Không có task tương ứng → hỏi người dùng, **không tự mở phạm vi mới**.
2. **Chủ sở hữu là Q hay T?** Mỗi task có đúng một người chịu trách nhiệm.
3. **File có đúng chủ không?** Đối chiếu *bảng sở hữu file/module* ở đầu `Task.md`.

## 2. Không sửa file của người kia

Quy ước chống xung đột Git nằm ở đầu `Task.md`. Điểm hay vi phạm nhất:

- `app/services/normalize.py` là của **Q** (chuẩn hoá SĐT/email), `normalize_company.py` là của **T**.
- `app/main.py`, `app/routers/__init__.py`, `docker-compose.yml`, `templates/base.html` — của **Q**,
  đã khai stub sẵn từ D1 nên **không cần sửa** khi thêm tính năng.
- **Chỉ Q được sinh Alembic revision.** Hai người cùng sinh sẽ tạo 2 head phải merge tay
  (CI có job bắt lỗi này).
- Cần thay đổi file của người kia → dừng lại, báo người dùng, đừng tự sửa.

## 3. SAU khi code xong — bắt buộc cập nhật `Task.md`

Đổi trạng thái đúng dòng task và cập nhật dòng `**Tổng quan:** x / 103 task`:

| Ký hiệu | Nghĩa |
|---------|-------|
| ⬜ | Chưa làm |
| 🔄 | Đang làm — ghi thêm phần trăm, ví dụ `🔄 Đang làm — 60%` |
| ✅ | Xong — đạt DoD: chạy trong Docker + đã test + đã merge vào `main` |
| ⏸️ | Tạm dừng — **ghi rõ lý do và ai gỡ vướng** ngay trong ô trạng thái |
| ❌ | Cắt khỏi phạm vi — ghi rõ lý do |

Chưa đạt DoD thì **không được đánh ✅**. Làm dở thì để 🔄.

## 4. Trước khi kết thúc, chạy đúng các kiểm tra của CI

```bash
ruff check . && ruff format --check . && mypy app embedder scripts && pytest
```

CI trên PR chạy đúng những lệnh này cộng gitleaks và kiểm tra một head Alembic.
Chi tiết: `.github/workflows/README.md`.

## 5. Ranh giới phạm vi

- Dự án là **bản demo 11 ngày**, chạy localhost. Không thêm production concern
  (HTTPS, auto-scaling, CD) — `Plan.md` mục 1.4 đã ghi ngoài phạm vi.
- **Embedding không đi qua CLIProxy** (CLIProxy không có endpoint embedding — đã kiểm chứng
  mã nguồn). Dùng service `embedder` cục bộ. Xem `Plan.md` mục 2.6.
- **Không tự sinh hồ sơ doanh nghiệp** sau khi quét danh thiếp. Người dùng chủ động tích chọn
  công ty rồi bấm nút. Xem `Plan.md` mục 2.2, sơ đồ Luồng 1 / Luồng 2.
- Toàn bộ phạm vi đã biết nằm trong D1–D11. **Không xếp việc vào D12–D15.**
