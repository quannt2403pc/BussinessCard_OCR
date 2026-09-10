# Biên bản chốt phạm vi & danh sách trường dữ liệu

> Chủ sở hữu: **T** · Task **1.7** · Họp đầu D1 (60') · Ngày: **2026-09-10**
> Thành phần: Quân (Q), Tùng (T) · Người ghi: T
> Nguồn gốc yêu cầu: `requirement/requirement.md` + `requirement/prompt.txt`
> Kế hoạch chi tiết: [Plan.md](../Plan.md) · Nhiệm vụ: [Task.md](../Task.md)

Biên bản này là **bản chốt tại D1**. Thay đổi phạm vi sau D1 phải được cả hai đồng ý và ghi
vào mục 6 (Nhật ký thay đổi) — không sửa lịch sử phía trên.

---

## 1. Phạm vi đã chốt

### 1.1 Trong phạm vi

| # | Hạng mục | Ghi chú chốt tại buổi họp |
|---|----------|---------------------------|
| 1 | Web app FastAPI chạy localhost qua Docker Compose | Một lệnh `docker compose up -d` |
| 2 | Upload **1 ảnh** và **upload hàng loạt** danh thiếp | Batch là ưu tiên S, cắt được nếu tràn giờ |
| 3 | Màn hình danh sách danh thiếp đã quét (tìm kiếm, lọc, phân trang) | |
| 4 | Màn hình review/sửa từng danh thiếp trước khi xác nhận | Bắt buộc — chống rủi ro R3 (OCR sai) |
| 5 | Danh thiếp **đa ngôn ngữ**: Anh, Việt, Hàn, Nhật, Trung | Tiêu chí A4 chỉ đòi Anh/Việt + ≥1 ngôn ngữ CJK |
| 6 | Lập hồ sơ doanh nghiệp **kích hoạt thủ công** | Tích chọn 1..n công ty → bấm 1 nút → chạy nền |
| 7 | Trợ lý AI hỏi–đáp RAG trên danh thiếp + hồ sơ DN, có trích dẫn | |
| 8 | **Nút bấm kết nối OAuth với CLIProxy** + hiển thị trạng thái | Yêu cầu bắt buộc từ đề bài |
| 9 | Export CSV/JSON | Ưu tiên S |
| 10 | Dashboard thống kê | Ưu tiên S |

### 1.2 Ngoài phạm vi *(chốt dứt điểm, không mở lại trong D1–D11)*

| Hạng mục | Lý do |
|----------|-------|
| Triển khai production, CD tự động, HTTPS, domain thật, auto-scaling | Đề bài ghi rõ "demo, chỉ môi trường dev, chạy localhost" |
| App mobile native | Dùng web + thuộc tính `capture` của trình duyệt để chụp ảnh |
| Quản lý người dùng / phân quyền | Demo single-user |
| Tích hợp CRM bên ngoài | Chỉ export CSV/JSON |
| Danh thiếp 2 mặt ghép thành 1 bản ghi | Ghi nhận cho giai đoạn sau |
| Nhập Knowledge Base từ file CSV có sẵn | Ghi nhận cho giai đoạn sau |
| Dark mode / responsive mobile | Ghi nhận cho giai đoạn sau |

> **Có CI** (lint, kiểu, test, quét secret, một head Alembic) — CI *không* phải CD và nằm
> trong phạm vi. Xem `.github/workflows/README.md`.

### 1.3 Ưu tiên khi phải cắt

- **MUST:** F1 + F2 + F3 luồng cơ bản, nút OAuth, Docker Compose.
- **SHOULD:** batch upload, export CSV, dashboard thống kê.
- **COULD:** gộp công ty thủ công *(đã cắt ở task 8.10)*, chat streaming, gợi ý câu hỏi mẫu.

---

## 2. Danh sách trường trích xuất từ danh thiếp (F1)

Nguồn sự thật của schema là migration `alembic/versions/20260910_0900-0001_khoi_tao_schema.py`.
Bảng dưới đây chốt **ý nghĩa nghiệp vụ** và **quy tắc điền** của từng trường.

### 2.1 Bảy trường bắt buộc *(tiêu chí nghiệm thu A3)*

| # | Trường | Cột DB | Kiểu | Quy tắc |
|---|--------|--------|------|---------|
| 1 | Họ tên người đưa danh thiếp | `full_name` | `varchar(255)` | Giữ nguyên thứ tự như in trên thiếp; không tự đảo họ/tên |
| 2 | Chức vụ | `job_title` | `varchar(255)` | Giữ nguyên ngôn ngữ gốc, không dịch |
| 3 | Công ty | `company_name_raw` | `varchar(255)` | **Nguyên văn như in**; bản chuẩn hoá nằm ở `companies.name_normalized` |
| 4 | Email | `email` | `varchar(255)` | Lowercase, bỏ khoảng trắng (task 3.6) |
| 5 | Số điện thoại | `phone` | `varchar(64)` | Chuẩn hoá E.164 khi đoán được quốc gia (task 3.6) |
| 6 | Địa chỉ | `address` | `text` | Gộp nhiều dòng thành một chuỗi, phân tách bằng `, ` |
| 7 | Ngày upload | `uploaded_at` | `datetime` | **Hệ thống sinh**, không trích từ ảnh |

### 2.2 Trường bổ sung

| Trường | Cột DB | Kiểu | Quy tắc |
|--------|--------|------|---------|
| SĐT phụ | `phone_alt` | `varchar(64)` | Danh thiếp thường có 2–3 số (di động / bàn / fax). Lấy số thứ hai; fax bỏ qua nếu nhãn ghi rõ |
| Website | `website` | `varchar(255)` | Thêm `https://` nếu thiếu scheme |
| Ngôn ngữ nhận diện | `language_detected` | `varchar(16)` | Mã ISO 639-1: `en`, `vi`, `ko`, `ja`, `zh` |
| Độ tin cậy từng trường | `confidence` | `jsonb` | `{"full_name": 0.95, "email": 0.4, ...}` — UI tô vàng trường < 0.7 (task 5.1) |
| JSON gốc LLM trả về | `ocr_raw_json` | `jsonb` | Giữ nguyên văn để đo lại độ chính xác (task 7.8/10.8) mà không phải quét lại ảnh |
| Ghi chú | `notes` | `text` | Người dùng tự nhập ở màn hình review |

### 2.3 Quy tắc chung khi trích xuất *(chống rủi ro R3)*

1. **Không suy đoán.** Trường không đọc được → `null`, **không** đoán từ ngữ cảnh.
   Ví dụ: thấy `@fpt.com.vn` không được suy ra website `https://fpt.com.vn`.
2. **Không dịch.** Danh thiếp tiếng Nhật giữ nguyên tiếng Nhật; việc dịch (nếu cần) là của
   tầng hiển thị, không phải tầng dữ liệu.
3. **Mỗi trường kèm `confidence` 0..1.** Prompt bắt buộc trả về, dùng cho UI review.
4. **Người dùng phải xác nhận** trước khi danh thiếp được coi là dữ liệu thật
   (`status: needs_review → confirmed`).

### 2.4 Vòng đời một danh thiếp

```
pending ──(OCR xong)──> needs_review ──(người dùng bấm Xác nhận)──> confirmed
                                                                        │
                                                    gắn company_id + ingest vào KB
```

**Chốt quan trọng:** xác nhận danh thiếp **KHÔNG** sinh hồ sơ doanh nghiệp.
Lúc này `companies` mới chỉ có bản ghi tên công ty. Xem mục 3.

---

## 3. Danh sách trường hồ sơ doanh nghiệp (F2)

Hồ sơ **chỉ được sinh khi người dùng chủ động tích chọn công ty rồi bấm nút**
(tiêu chí A5b). Hệ thống không tự chạy.

| Trường | Cột DB | Bắt buộc có nguồn? | Ghi chú |
|--------|--------|--------------------|---------|
| Tên pháp lý | `legal_name` | ✅ | Tên đăng ký kinh doanh, khác tên thương hiệu |
| Mã số thuế | `tax_code` | ✅ | **Trường dễ bị LLM bịa nhất** (rủi ro R4) |
| Năm thành lập | `founded_year` | ✅ | |
| Quy mô | `size_label` | ✅ | Nhãn dạng chữ: "Doanh nghiệp lớn", "SME"… |
| Khoảng nhân sự | `employee_range` | ✅ | Ví dụ `1000-5000` |
| Ngành nghề | `industry` | ✅ | Mảng text, có index GIN để lọc |
| Sản phẩm/dịch vụ | `products` | ✅ | Mảng text |
| Địa chỉ | `address` | ✅ | Trụ sở chính |
| Website | `website` | ✅ | |
| Điện thoại | `phone` | ✅ | |
| Email | `email` | ✅ | |
| Mô tả | `description` | ✅ | 2–4 câu |
| Nguồn trích dẫn | `sources` | — | `{ten_truong: [{"url":…, "title":…}]}` |

**Quy tắc chống bịa (R4) — chốt cứng:**
> Trường nào **không có URL nguồn kiểm chứng được** thì để `null` và gắn nhãn
> `unverified` trên UI. Thà thiếu còn hơn sai. Tiêu chí A5 chỉ đòi ≥ 5 trường có nguồn,
> không đòi đủ 12 trường.

Mỗi công ty có **đúng một** hồ sơ (`company_profiles.company_id` UNIQUE) — nút "Tạo lại hồ sơ"
ghi đè bản cũ. Xem `docs/erd.md` mục 2, quyết định số 1.

---

## 4. Ranh giới hai luồng nghiệp vụ

| | Luồng 1 — Quét danh thiếp | Luồng 2 — Lập hồ sơ đối tác |
|---|---|---|
| **Kích hoạt** | Người dùng upload ảnh | Người dùng tích chọn công ty + bấm nút |
| **Tự động chạy?** | Có (OCR chạy ngay sau upload) | **Không** — bắt buộc thủ công |
| **Ghi vào** | `business_cards`, `companies` (chỉ tên) | `company_profiles` |
| **Gọi Internet?** | Không (chỉ LLM Vision đọc ảnh) | Có (LLM + tìm kiếm web) |
| **Chủ sở hữu** | Q | T |

Đây là điểm dễ hiểu sai nhất của đề bài, đã chốt tại buổi họp: **quét xong không tự tra cứu**.

---

## 5. Quyết định chốt tại buổi họp

| # | Quyết định | Lý do |
|---|-----------|-------|
| 1 | Ngày upload là **thời điểm hệ thống nhận file**, không phải ngày in trên thiếp | Ngày trên thiếp gần như không bao giờ có |
| 2 | `company_name_raw` giữ **nguyên văn**, chuẩn hoá tách sang bảng `companies` | Giữ được bằng chứng gốc để đối chiếu khi dedupe sai |
| 3 | Chỉ lấy tối đa **2 số điện thoại** (`phone`, `phone_alt`) | Đủ cho demo; danh thiếp >2 số rất hiếm |
| 4 | Ngưỡng `confidence` cảnh báo trên UI = **0.7** | Số chốt tạm, tinh chỉnh sau khi đo thực tế ở task 7.8 |
| 5 | Không lưu ảnh gốc vào DB, chỉ lưu `image_path` trỏ vào volume `uploads` | Giữ DB nhẹ |
| 6 | `status` dùng `varchar` + `StrEnum` phía Python, không dùng ENUM của Postgres | Thêm giá trị mới không cần `ALTER TYPE` |

---

## 6. Nhật ký thay đổi phạm vi *(điền khi phát sinh sau D1)*

| Ngày | Thay đổi | Ai đề xuất | Ai duyệt | Ảnh hưởng |
|------|----------|-----------|----------|-----------|
| 2026-09-10 | Chốt bản đầu tiên | — | Q + T | — |
| 2026-09-10 | Cắt task 8.10 (gộp công ty trùng thủ công, ưu tiên C) | T | Q + T | Nhường giờ cho màn hình lập hồ sơ đối tác (5.8 + 6.5, ưu tiên M) |

---

## 7. Việc còn treo sau buổi họp

| # | Vấn đề | Người gỡ | Hạn |
|---|--------|----------|-----|
| 1 | Cấu trúc bảng theo dõi **job lập hồ sơ hàng loạt** (task 5.8) chưa có trong ERD | T chốt cấu trúc → báo Q sinh revision | Đầu D5 |
| 2 | Chốt model embedding & số chiều vector | T (task 2.6) — khác 384 chiều phải báo Q **trong ngày** | D2 |
| 3 | Chốt chữ ký `upsert_company()` và `ingest_company_profile()` | Họp đầu D2 (30') | D2 |
| 4 | `LLM_MODEL` mặc định đang là `gemini-flash-latest` nhưng provider `antigravity` không có model này | Q (task 2.3) — xem `docs/cliproxy-notes.md` mục 5 | D2 |
