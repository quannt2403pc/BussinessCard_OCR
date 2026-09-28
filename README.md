# OCR Xì Mi

Quét và quản lý danh thiếp, theo dõi doanh nghiệp, tạo hồ sơ đối tác và tra cứu bằng trợ lý AI.
Ứng dụng production: [ocrximi.io.vn](https://ocrximi.io.vn).

## Chạy cục bộ

Cần Docker với Compose v2, khoảng 4 GB RAM và 15 GB dung lượng trống. Lần chạy đầu sẽ build
service `embedder`; quá trình build cần thêm dung lượng tạm cho PyTorch.

```bash
git clone https://github.com/quannt2403pc/BussinessCard_OCR.git
cd BussinessCard_OCR
docker compose up -d
```

Mở [localhost:8000](http://localhost:8000). Compose tự tạo cấu hình CLIProxy còn thiếu và API tự
chạy migration database khi khởi động. Không cần cài Go hay tải mã nguồn CLIProxy.

`.env` là tùy chọn. Để đổi cấu hình, sao chép `.env.example` thành `.env`. Nếu đổi
`CLIPROXY_MGMT_KEY`, cập nhật cả `remote-management.secret-key` trong `cliproxy/config.yaml`.

Swagger mặc định tắt. Bật trong `.env` khi phát triển:

```dotenv
DOCS_ENABLED=true
```

Sau đó mở [localhost:8000/docs](http://localhost:8000/docs). Không bật trên máy chủ production.

## Bắt đầu sử dụng

1. Đăng ký tài khoản tại [localhost:8000](http://localhost:8000).
2. Mở [localhost:8000/settings](http://localhost:8000/settings), chọn **Kết nối CLIProxy (OAuth)**
	 và đăng nhập Google.
3. Bấm **Kiểm tra kết nối**. Sau khi kết nối, có thể quét danh thiếp và tạo hồ sơ doanh nghiệp.

Token OAuth được giữ trong volume Docker qua các lần restart. `docker compose down -v` sẽ xóa
database, ảnh tải lên và token OAuth; chỉ dùng khi muốn xóa toàn bộ dữ liệu cục bộ.

## Dữ liệu mẫu

```bash
# Bộ dữ liệu kiểm thử: 3 công ty và 4 danh thiếp
docker compose exec api python -m scripts.seed

# Bộ demo: 7 danh thiếp, không gọi model
docker compose exec api python -m scripts.seed --demo

# Nạp dữ liệu cho một tài khoản cụ thể
docker compose exec api python -m scripts.seed --user ban@example.com --password matkhau123
```

Lệnh mặc định dùng tài khoản `demo@bizcard.local` với mật khẩu `demo12345`; script sẽ in thông tin
đăng nhập nếu cần tạo tài khoản mới. Bộ `--demo` dùng ảnh có sẵn trong hệ thống. Để quét lại các
ảnh đó, chạy `docker compose exec api python -m scripts.seed --reset --demo` trước.

## Xử lý sự cố

- **OAuth không hoàn tất:** kiểm tra `cliproxy` đang chạy và cổng `51121` được publish.
- **`remote management disabled`:** đặt `allow-remote: true` trong `cliproxy/config.yaml`.
- **Không thấy danh mục model:** kết nối OAuth trước, sau đó bấm **Làm mới** trong `/settings`.
- **Google yêu cầu xác minh tài khoản:** mở liên kết xác minh trong thông báo ở `/settings`, rồi
	làm mới trạng thái kết nối.
- **Model trả `429`:** Google đang giới hạn tạm thời; chờ hoặc chọn model khác trong `/settings`.
- **Danh thiếp không xuất hiện trong trợ lý:** chờ `embedder` healthy rồi chạy lại index tại
	`POST /api/kb/reindex`.
- **Script Python lỗi mã hóa tiếng Việt trên Windows:** đặt `PYTHONIOENCODING=utf-8` hoặc chạy
	script trong container.

## Phát triển

Yêu cầu Python 3.12. Cài dependencies rồi chạy các kiểm tra của CI:

```bash
python -m venv .venv
pip install -r requirements.txt
ruff check .
ruff format --check .
mypy app embedder scripts
pytest
```

Test tích hợp cần PostgreSQL với pgvector. Khởi động database bằng `docker compose up -d db`;
test tự bỏ qua nếu database không khả dụng.

Tài liệu khác: [hướng dẫn sử dụng](docs/user-guide.md), [thiết lập OAuth](docs/oauth-setup.md),
[tài liệu dự án](docs/README.md), [kế hoạch](Plan.md) và [công việc](Task.md).
