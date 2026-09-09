# BusinessCard_OCR

Số hoá danh thiếp và lập hồ sơ doanh nghiệp đối tác. Bản demo chạy localhost, quản lý bằng Docker.

- Kế hoạch dự án: [Plan.md](./Plan.md)
- Nhiệm vụ từng ngày: [Task.md](./Task.md)
- Chỉ mục tài liệu: [docs/README.md](./docs/README.md)

> **Trạng thái: mới có khung xương thư mục.** Toàn bộ file mã nguồn hiện là stub, mỗi file ghi rõ
> chủ sở hữu và task tạo ra nó. Chưa chạy được — xem `Task.md` để biết thứ tự triển khai.

## Ba chức năng

1. **Số hoá danh thiếp qua ảnh** — Gemini Flash Vision trích xuất công ty, họ tên, chức vụ, email, SĐT, địa chỉ…
2. **Hồ sơ doanh nghiệp đối tác** — người dùng **chủ động tích chọn** công ty rồi bấm tạo; hệ thống tìm kiếm Google + LLM tổng hợp, mọi trường kèm URL nguồn.
3. **Trợ lý AI hỏi–đáp (RAG)** — hỏi đáp trên danh thiếp + hồ sơ đã tạo, trả lời kèm trích dẫn.

## Chạy (khi đã triển khai xong)

```bash
cp .env.example .env      # điền CLIPROXY_MGMT_KEY
docker compose up -d
# http://localhost:8000
```

## Lưu ý kiến trúc

- **Sinh nội dung** đi qua CLIProxyAPI bằng OAuth — không nhúng API key.
- **Embedding KHÔNG đi qua CLIProxy** (CLIProxy không có endpoint embedding). Dùng service `embedder` cục bộ, model nhúng sẵn trong image. Chi tiết: `Plan.md` mục 2.6.
