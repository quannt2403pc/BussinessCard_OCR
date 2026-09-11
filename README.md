# BusinessCard_OCR

Số hoá danh thiếp và lập hồ sơ doanh nghiệp đối tác. Bản demo chạy localhost, quản lý bằng Docker.

- Kế hoạch dự án: [Plan.md](./Plan.md)
- Nhiệm vụ từng ngày: [Task.md](./Task.md)
- Chỉ mục tài liệu: [docs/README.md](./docs/README.md)
- Quy ước cho AI agent: [AGENTS.md](./AGENTS.md)

> **Trạng thái: xong D1 (task 1.1–1.10), đang vào D2.** `docker compose up -d` khởi động
> `api` + `db` + `cliproxy` + `adminer`, `alembic upgrade head` tạo đủ 6 nhóm bảng. Đăng nhập
> OAuth và client LLM (task 2.2–2.5) chưa làm; các màn hình và API nghiệp vụ (F1/F2/F3) vẫn là
> stub — xem `Task.md` để biết thứ tự triển khai.

## Ba chức năng

1. **Số hoá danh thiếp qua ảnh** — Gemini Flash Vision trích xuất công ty, họ tên, chức vụ, email, SĐT, địa chỉ…
2. **Hồ sơ doanh nghiệp đối tác** — người dùng **chủ động tích chọn** công ty rồi bấm tạo; hệ thống tìm kiếm Google + LLM tổng hợp, mọi trường kèm URL nguồn.
3. **Trợ lý AI hỏi–đáp (RAG)** — hỏi đáp trên danh thiếp + hồ sơ đã tạo, trả lời kèm trích dẫn.

## Chạy

```bash
cp .env.example .env                                   # điền CLIPROXY_MGMT_KEY
cp cliproxy/config.example.yaml cliproxy/config.yaml   # secret-key đặt TRÙNG key trên
docker compose up -d
docker compose exec api alembic upgrade head   # tự động hoá ở task 9.2

curl localhost:8000/health   # -> {"status":"ok"}
```

Cần đúng hai lệnh `cp` đó, không cần gì thêm — **không phải cài Go, không phải clone
CLIProxyAPI về máy**: `cliproxy` chạy bằng image công khai `eceasy/cli-proxy-api`, Docker tự pull.

> ⚠️ Bỏ lệnh `cp` thứ hai thì Docker thấy đường dẫn bind mount không tồn tại và **tạo một thư
> mục** tên `config.yaml`, `cliproxy` khởi động lỗi với thông báo rất khó đoán. `api` và `db`
> vẫn lên bình thường, chỉ phần OAuth là chết.
>
> `cliproxy/config.yaml` **không commit** (đã nằm trong `.gitignore`) vì chứa management key.
> CLIProxy băm key đó lúc khởi động rồi ghi đè lại chính file — sau lần `up` đầu tiên thấy giá
> trị biến thành chuỗi hash là bình thường.

| Địa chỉ | Là gì |
|---------|-------|
| http://localhost:8000 | Web UI |
| http://localhost:8000/docs | Swagger tự sinh — tài liệu API chính sau D1 |
| http://localhost:8080 | Adminer, xem DB khi debug (server `db`) |
| http://localhost:8317 | CLIProxyAPI — Management API + gateway tới Gemini |
| localhost:51121 | Callback OAuth của Antigravity; không mở tay, trình duyệt tự quay về |

Service `embedder` (:8001) đang nằm trong profile vì `embedder/` chưa dựng (task 3.10):
`docker compose --profile embedder up -d embedder`.

## Phát triển ở máy (không qua Docker)

```bash
python -m venv .venv && .venv/Scripts/activate      # Windows
pip install -r requirements.txt
ruff check . && ruff format --check . && mypy app embedder scripts && pytest
```

Đúng bốn lệnh trên là những gì CI chạy trên PR — chạy trước ở máy cho đỡ mất lượt.
Chi tiết CI: [.github/workflows/README.md](./.github/workflows/README.md).

## Quy ước làm việc

**Sở hữu file.** Mỗi file có đúng một chủ (Q hoặc T) — bảng sở hữu nằm ở đầu
[Task.md](./Task.md). Không sửa file của người kia; cần đổi thì báo chủ file.
Riêng **Alembic revision chỉ Q sinh** (hai người cùng sinh sẽ tạo 2 head phải merge tay; CI có
job bắt lỗi này).

**Nhánh.** `main` luôn ở trạng thái chạy được, chỉ nhận PR. Nhánh làm việc đặt tên
`<loại>/<module>-<việc>`:

```
feat/cards-upload        fix/ocr-json-hong        docs/erd
feat/companies-enrich    chore/ci-gitleaks        refactor/kb-chunking
```

PR nhỏ, **merge trong ngày**, rebase lên `main` trước khi merge, không để nhánh sống qua đêm.
PR phải **xanh CI** mới được merge (`main` bật branch protection, required check = `CI success`).

**Commit.** Theo [Conventional Commits](https://www.conventionalcommits.org/):

```
<loại>(<phạm vi>): <mô tả ngắn, chữ thường, không dấu chấm cuối>
```

| Loại | Dùng khi |
|------|----------|
| `feat` | Thêm chức năng |
| `fix` | Sửa lỗi |
| `docs` | Chỉ đổi tài liệu |
| `refactor` | Đổi cấu trúc, không đổi hành vi |
| `test` | Thêm/sửa test |
| `chore` | Hạ tầng, CI, cấu hình |

Phạm vi dùng tên module: `cards`, `companies`, `kb`, `chat`, `integration`, `embedder`, `db`, `ci`.
Ví dụ: `feat(cards): them endpoint upload va tinh image_hash`.

**Trạng thái task.** Code xong phải cập nhật đúng dòng trong `Task.md` (⬜ / 🔄 / ✅ / ⏸️ / ❌)
và dòng `**Tổng quan:** x / 103 task`. Chưa đạt DoD (chạy trong Docker + đã test + đã merge
`main`) thì **không** đánh ✅.

## Lưu ý kiến trúc

- **Sinh nội dung** đi qua CLIProxyAPI bằng OAuth — không nhúng API key.
- **Embedding KHÔNG đi qua CLIProxy** (CLIProxy không có endpoint embedding). Dùng service `embedder` cục bộ, model nhúng sẵn trong image. Chi tiết: `Plan.md` mục 2.6.
- Router khai sẵn cả 7 module từ D1: chủ sở hữu chỉ cần khai biến `router = APIRouter(...)` trong
  file của mình là tự được gắn vào app — không ai phải sửa `app/main.py`. Xem
  `app/routers/__init__.py`.
