# BusinessCard_OCR

Truy cập trực tiếp tại địa chỉ: **https://ocrximi.io.vn**

---

Phần dưới đây là hướng dẫn cài đặt và chạy ở máy cá nhân.

## Yêu cầu môi trường

| | Tối thiểu | Vì sao |
|---|---|---|
| Docker | Docker Desktop hoặc Docker Engine + Compose v2 | Toàn bộ hệ thống chạy bằng Compose |
| RAM trống | **4 GB** | 5 container lúc nhàn rỗi tốn ~1 GB, riêng `embedder` ~760 MB sau khi nạp model |
| Đĩa trống | **~15 GB** | Image tổng ~4,1 GB, nhưng lúc **build** `embedder` cần thêm ~8 GB tạm cho `torch` |
| Mạng | Chỉ cần lúc build + lúc gọi model | Model embedding nằm sẵn trong image nên RAG chạy được cả khi mất mạng |
| Tài khoản | Một Gmail để bấm OAuth | Không cần API key, không cần thẻ tín dụng |

Chạy ngoài Docker thì thêm Python 3.12 (xem mục *Phát triển ở máy*).

## Cài đặt & chạy

```bash
git clone <repo> && cd BusinessCard_OCR
docker compose up -d
curl localhost:8000/health   # -> {"status":"ok"}
```

**Đúng một lệnh, không phải chép file nào trước** — và cũng **không cần cài Go hay clone
CLIProxyAPI về máy**:

- `cliproxy-init` tự tạo `cliproxy/config.yaml` từ `config.example.yaml` nếu máy chưa có;
- entrypoint của `api` tự chạy `alembic upgrade head` trước khi mở cổng;
- mọi biến môi trường đều có mặc định trong `docker-compose.yml`, `.env` là **tuỳ chọn**.

> **Lần đầu mất vài phút** vì phải build `embedder`: image kéo `torch` CPU và nhúng sẵn model
> ~470 MB vào trong. Cố ý làm vậy — máy không có mạng vẫn chạy được.

Đổi cấu hình thì `cp .env.example .env`, sửa, rồi `docker compose up -d api`. Đổi
`CLIPROXY_MGMT_KEY` thì phải đổi **cả** `remote-management.secret-key` trong
`cliproxy/config.yaml` cho trùng.

| Địa chỉ | Là gì |
|---------|-------|
| http://localhost:8000 | Web UI |
| http://localhost:8000/docs | Swagger tự sinh — bật bằng `DOCS_ENABLED=true` trong `.env` (mặc định tắt) |
| http://localhost:8000/settings | Kết nối OAuth, chọn model, kiểm tra kết nối |
| http://localhost:8080 | Adminer, xem DB khi debug (server `db`, user/pass `bizcard`) |
| http://localhost:8317 | CLIProxyAPI — Management API + gateway tới Gemini |
| localhost:51121 | Callback OAuth; không mở tay, trình duyệt tự quay về |
| http://localhost:8001 | Service `embedder` (`/health`, `/embed`) |

## Tạo tài khoản

Hệ thống có đăng nhập nhiều người dùng, và **dữ liệu của mỗi tài khoản là riêng**. Chưa đăng nhập
thì mọi đường dẫn đều bị chặn, trừ `/auth/*` và `/health`:

1. Mở **http://localhost:8000** → bị chuyển sang trang đăng nhập.
2. Bấm **Đăng ký**, nhập email + mật khẩu (tối thiểu 8 ký tự, có cả chữ lẫn số).
3. Xong là vào thẳng ứng dụng; đổi tên hiển thị hoặc mật khẩu ở **/account**.

Máy mới dựng từ `docker compose up -d` thì **không có tài khoản nào sẵn** — người đầu tiên tự đăng
ký.

## Kết nối AI (bắt buộc trước khi quét hay tạo hồ sơ)

1. Mở **http://localhost:8000/settings**.
2. Bấm **Kết nối CLIProxy (OAuth)** → tab mới mở trang đăng nhập Google → đồng ý.
3. Trình duyệt tự quay về, badge chuyển **Đã kết nối** kèm địa chỉ Gmail và danh sách model.
4. Bấm **Kiểm tra kết nối** để gọi thử một prompt ngắn.

Token nằm trong volume `cliproxy_auths` nên sống qua `docker compose restart`. `docker compose
down -v` xoá volume đó → **phải bấm kết nối lại**. Chi tiết và các bẫy đã gặp:
[docs/oauth-setup.md](./docs/oauth-setup.md).

## Nạp dữ liệu mẫu

```bash
# Bộ dữ liệu cố định (3 công ty + 4 danh thiếp)
docker compose exec api python -m scripts.seed

# Bộ demo: 7 thẻ thật ở trạng thái cuối buổi demo, KHÔNG gọi model lần nào
docker compose exec api python -m scripts.seed --demo

# Nạp vào đúng tài khoản của bạn (mặc định là demo@bizcard.local / demo12345)
docker compose exec api python -m scripts.seed --user ban@example.com --password matkhau123
```

Dữ liệu seed **thuộc về một tài khoản cụ thể**. Không truyền `--user` thì script dùng
`demo@bizcard.local`, tự tạo nếu chưa có và in mật khẩu ra màn hình — đăng nhập bằng tài khoản đó
mới thấy dữ liệu vừa nạp. Tài khoản đã tồn tại thì script **không đổi mật khẩu** của nó.

Nạp bộ `--demo` rồi thì **không quét lại được đúng những tấm ảnh đó** (hệ thống chặn trùng ảnh),
dọn bằng `samples/demo/reset_demo.sql` hoặc `--reset --demo`.

## Troubleshooting

| Triệu chứng | Nguyên nhân | Cách xử lý |
|-------------|-------------|------------|
| Mọi trang đều nhảy về `/auth/login`, API trả `401` | Chưa đăng nhập thì chỉ `/auth/*` và `/health` mở | Đăng ký một tài khoản (xem *Tạo tài khoản*). Cả `/docs` cũng cần đăng nhập |
| `/settings` báo **Chưa kết nối** dù vừa bấm OAuth xong | Cổng `51121` không được publish → Google gọi callback về hư không, token không bao giờ được lưu | Kiểm `docker compose ps` thấy `51121` trong danh sách cổng của `cliproxy`; bấm kết nối lại |
| Gọi model trả `403 remote management disabled` | `allow-remote: false` — CLIProxy hiểu "localhost" đúng nghĩa `127.0.0.1`, còn `api` gọi qua mạng bridge của Docker | Đặt `allow-remote: true` trong `cliproxy/config.yaml` |
| Gọi model trả `400 unknown provider for model …` | Chưa kết nối OAuth, `LLM_MODEL` không có trong channel, hoặc tiền tố credential không ai nhận | Xem badge ở `/settings`; danh mục model thật nằm ngay trong ba ô chọn model trên trang đó |
| Ô chọn model trống, báo *"Chưa lấy được danh mục model"* | Danh mục chỉ đọc được **sau khi** có credential OAuth | Kết nối AI trước, rồi bấm **Làm mới** |
| Model trả `429 cooldown` | Tài khoản Google bị giới hạn tạm thời | Đổi sang model khác ở khối **Model cho từng chức năng** trên `/settings` |
| `cliproxy` khởi động lỗi, `config.yaml` là một **thư mục** | Bind mount kiểu file: Docker dựng đường dẫn lúc *create*, trước khi `cliproxy-init` kịp chạy | Xoá thư mục rỗng đó rồi `docker compose up -d` |
| Container `api` chết ngay khi khởi động, log báo `No module named 'argon2'` | Image cũ, dựng trước khi thêm `argon2-cffi` + `itsdangerous` vào `requirements.txt` | `docker compose build api && docker compose up -d api` |
| Mọi lệnh ghi báo `null value in column "user_id" … violates not-null constraint` | DB đã lên revision `0005` nhưng mã ứng dụng còn cũ hơn nó | `git pull` rồi `docker compose up -d api` |
| Upload lại đúng tấm ảnh cũ → **200** kèm `duplicate: true`, không quét lại | Chống trùng theo `image_hash`, cố ý không gọi lại model | Muốn quét lại thì xoá bản ghi cũ, hoặc dùng `reset_demo.sql` với bộ ảnh demo |
| Xác nhận thẻ xong nhưng trợ lý AI không thấy | `embedder` còn đang nạp model lúc bấm Xác nhận | Chờ `embedder` healthy rồi bấm **Index lại** ở trang Trợ lý AI (`POST /api/kb/reindex`) |
| Trợ lý trả "không có thông tin" dù dữ liệu có trong DB | Index `ivfflat` học phân cụm từ dữ liệu *lúc tạo index*; nạp dữ liệu sau đó thì centroid vô nghĩa | `scripts/seed.py` đã tự `REINDEX`; nạp tay thì gọi `POST /api/kb/reindex` |
| Câu hỏi tiếng Việt **gõ không dấu** không ra gì | Hạn chế đã biết, chưa xử lý | Gõ có dấu |
| Script Python chết ngay dòng in đầu tiên trên Windows | stdout về `cp1252` khi không phải console, chữ Việt/Nhật không mã hoá được | Đặt `PYTHONIOENCODING=utf-8`, hoặc chạy trong container |
| Sau `docker compose down -v` mọi thứ trống trơn | `-v` xoá cả `pgdata`, `uploads` **và** `cliproxy_auths` | Kết nối lại OAuth rồi `python -m scripts.seed` |

## Phát triển ở máy (không qua Docker)

```bash
python -m venv .venv && .venv/Scripts/activate      # Windows
pip install -r requirements.txt
ruff check . && ruff format --check . && mypy app embedder scripts && pytest
```

Đúng bốn lệnh kiểm tra trên là những gì CI chạy trên PR — chạy trước ở máy cho đỡ mất lượt.
`pytest` cần một PostgreSQL có pgvector: mặc định dùng service `db` của Compose, nên cứ
`docker compose up -d db` trước.
