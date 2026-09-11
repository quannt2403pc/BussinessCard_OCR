# CI — hướng dẫn bật chặn merge

`ci.yml` chỉ **chạy kiểm tra và báo kết quả**. Nó không tự chặn merge.
Muốn PR đỏ không merge được vào `main` thì phải bật Branch protection một lần trên GitHub.

> 🔴 **Chưa bật được, và không phải vì ai quên** (I-14, kiểm lại 2026-09-11): repo đang `private`
> trên gói **GitHub Free**. API trả `403 "Upgrade to GitHub Pro or make this repository public"`
> cho `rulesets` và `404` cho `branches/main/protection`. Hướng dẫn dưới đây **chỉ chạy được sau
> khi** chọn một trong hai: chuyển repo sang **public**, hoặc nâng **GitHub Pro**.
> Chưa chọn thì luật "PR phải xanh CI" là **kỷ luật thủ công** — tự chạy kiểm tra ở máy.
> Phần còn lại của file vẫn đúng: check cần chọn tên là `CI success`, và nó đã tồn tại thật
> (I-11 đã đóng, run `34554835829` trên `main` → success).

## Bật một lần (cần quyền admin repo)

`Settings` → `Branches` → `Add branch ruleset` (hoặc `Add rule` ở giao diện cũ):

1. Branch name pattern: `main`
2. Tích **Require a pull request before merging** → Required approvals: `1`
   *(hai người, mỗi PR do người kia duyệt — khớp quy ước "PR nhỏ, merge trong ngày" ở Task.md)*
3. Tích **Require status checks to pass before merging**
   → tích thêm **Require branches to be up to date before merging**
   → ô tìm kiếm status check, chọn đúng **`CI success`**
4. Tích **Do not allow bypassing the above settings** nếu muốn áp cho cả admin

> Chỉ cần chọn **`CI success`** làm required check. Job này gom kết quả của cả 5 job kia,
> nên sau này thêm/bớt job không phải vào chỉnh lại cấu hình branch protection.

Status check chỉ xuất hiện trong danh sách sau khi workflow đã chạy ít nhất một lần.
Nếu chưa thấy `CI success`, mở một PR nháp cho nó chạy rồi quay lại bước 3.

## Các job

| Job | Kiểm gì | Hỏng thì làm gì |
|-----|---------|-----------------|
| **Lint & format (ruff)** | import thừa, biến chưa dùng, thứ tự import, định dạng | `ruff check --fix .` rồi `ruff format .` |
| **Type check (mypy)** | lỗi kiểu rõ ràng trong `app/`, `embedder/`, `scripts/` | Sửa annotation. Cấu hình cố ý **không** strict — xem `pyproject.toml` |
| **Tests (pytest + pgvector)** | chạy `tests/` với PostgreSQL 16 + pgvector thật | Test phải mock CLIProxy bằng `respx` — CI không có OAuth |
| **Secret scan (gitleaks)** | gitleaks trên toàn bộ lịch sử | Xoá secret, đổi khoá, allowlist giá trị giữ chỗ trong `.gitleaks.toml` |
| **Migrations (single Alembic head)** | đảm bảo `alembic heads` chỉ ra 1 head | Ép quy ước số 5 ở `Task.md`: **chỉ Q sinh revision**. Hai head thì `alembic merge` hoặc sinh lại trên đúng nhánh cha |

## Chạy đúng các kiểm tra đó ở máy mình

```bash
pip install ruff==0.14.2 mypy==1.18.2
ruff check . && ruff format --check . && mypy app embedder scripts && pytest
```

## Việc còn nợ

- **`ci.yml`, job `test`**: hiện `pytest` trả mã 5 (không thu thập được test nào) đang được
  bỏ qua vì `tests/` mới chỉ có stub. **Xoá nhánh xử lý mã 5 sau khi task 6.1 xong**,
  nếu không CI sẽ vẫn xanh kể cả khi toàn bộ test biến mất.
- Chưa có job build Docker image vì `Dockerfile` và `docker-compose.yml` còn là stub
  (task 1.4). Thêm sau khi hai file đó chạy được.
