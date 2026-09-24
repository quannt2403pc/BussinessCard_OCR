# Kịch bản demo 10 phút

> Chủ sở hữu: **T** · Task: **11.7** (kịch bản + `samples/demo/`) · **11.8** (tổng duyệt, mục 6) · Cập nhật: 2026-09-21
> Người trình bày: 1 người thao tác, 1 người nói. Video dự phòng: task 11.4 (Q).

## 1. Thông điệp cần người xem nhớ

1. **Danh thiếp → dữ liệu sạch trong vài giây**, kể cả thẻ tiếng Nhật, Hàn.
2. **Hồ sơ đối tác chỉ chứa thông tin có nguồn.** Không có nguồn thì để trống, không bịa.
3. **Hỏi bằng tiếng Việt, trả lời kèm nguồn bấm được**; không có thông tin thì nói không có.

## 2. Chuẩn bị (xong trước giờ trình bày 30 phút)

| # | Việc | Lệnh / thao tác | Kiểm |
|---|------|-----------------|------|
| 1 | Khởi động | `docker compose up -d` trong thư mục dự án | `http://localhost:8000/health` → `ok` |
| 2 | Model | `/settings` → khối **Model cho từng chức năng** → chọn `gemini-3.6-flash-high` cho *Quét danh thiếp* (model đã tổng duyệt, xem mục 5). **Không còn phải sửa `.env` hay khởi động lại `api`** (EX-15) | Ba ô hiện đúng model vừa chọn |
| 3 | OAuth | `/settings` → **Kiểm tra kết nối** bằng **Gmail cá nhân** | Có câu trả lời của model |
| 4 | Dữ liệu cho trợ lý | `docker compose exec api python -m scripts.seed` (bỏ qua nếu báo đã có) | `/companies` có *Logistics Đại Việt* |
| 5 | Dọn lượt demo trước | `docker compose exec -T db psql -U bizcard -d bizcard < samples/demo/reset_demo.sql` | Không còn *Sữa Việt Nam*, *Hòa Phát*… trong `/companies` |
| 6 | Mở sẵn tab | `/`, `/cards/upload`, `/companies` — trợ lý là **bong bóng góc phải dưới**, không còn tab riêng (EX-09) | |
| 7 | Mở sẵn thư mục ảnh | `samples/demo/` trong File Explorer, cạnh trình duyệt | |
| 8 | Chạy thử một câu | Bấm **bong bóng trợ lý** góc phải dưới, hỏi *"Công ty nào làm về logistics?"* | Có câu trả lời + nguồn |

⚠️ Bước 5 bắt buộc từ lượt thứ hai trở đi: hệ thống chặn upload trùng ảnh, nên không dọn thì bước quét danh thiếp
sẽ **không chạy lại**.

## 3. Kịch bản

Thời gian dự kiến ghi trước; số đo thật của từng bước ở mục 6. Phần máy chạy (quét, tạo hồ sơ, trợ lý) chiếm dưới 2,5 phút trong 10 phút; phần còn lại là thao tác và lời nói.

| Phút | Màn hình | Thao tác | Lời nói (ý chính) | Dự phòng nếu lỗi |
|------|----------|----------|-------------------|------------------|
| 0:00–0:40 | `/` | Mở trang chủ | *"Sau mỗi hội thảo có cả xấp danh thiếp; nhập tay mất cả buổi, tra cứu từng công ty mất cả ngày."* | — |
| 0:40–2:10 | `/cards/upload` → review | Kéo `vi-01-clear.png` vào → mở review → chỉ SĐT đã chuẩn hoá `+84…`, website thêm `https://` → **Xác nhận** | *"Mô hình đọc ảnh, rồi hệ thống chuẩn hoá. Người dùng luôn kiểm trước khi xác nhận."* | OCR chậm > 20 s: nói tiếp phần kiến trúc trong lúc chờ |
| 2:10–3:10 | `/cards/batch` | Kéo 6 ảnh còn lại → chờ xong → mở nhanh thẻ `ja-01`, `ko-01` | *"Tiếng Nhật, tiếng Hàn đọc được như tiếng Việt."* | Chỉ xác nhận `ja-01`, `ko-01`, `vi-02`, `en-03`, `en-02`; bỏ qua thẻ còn lại |
| 3:10–3:40 | `/cards` | Xác nhận các thẻ vừa quét (nút *Xác nhận* trong từng review) | — | — |
| 3:40–4:20 | `/companies` | Tìm *hoa phat* → **một** công ty, 2 danh thiếp (tên hiển thị là tên in trên thẻ **xác nhận trước**, ví dụ *HOA PHAT GROUP JSC*; tên kia nằm trong *tên khác*). Chỉ cột *Hồ sơ*: tất cả *Chưa có hồ sơ* | *"Hai thẻ in tên khác nhau vẫn về một công ty. Và xác nhận thẻ không tự sinh hồ sơ: người dùng chủ động chọn."* | — |
| 4:20–6:50 | `/companies` | Tích *Sữa Việt Nam*, *Hòa Phát*, *Coteccons* → **Lập hồ sơ**. Trong lúc chờ: tích *日立製作所* → tạo → bấm **Huỷ** ngay | *"Mỗi công ty: tra cứu Internet, rồi chỉ giữ trường có trang nguồn chứng minh. Bấm nhầm thì huỷ được."* | Quá 3 phút: mở hồ sơ đã *✅ Xong* đầu tiên, để các công ty kia chạy tiếp |
| 6:50–7:50 | Hồ sơ *Sữa Việt Nam* | Cuộn tới **Nguồn tham khảo** → bấm một nguồn MST → trang gốc mở ra, có đúng mã số thuế | *"Mỗi con số đều bấm ra được trang gốc. Trường không có nguồn thì để trống."* | — |
| 7:50–8:10 | Hồ sơ *Samsung Electronics Vietnam* | Chỉ khối *"Cùng tên miền với: 삼성전자 (samsung.com)"* | *"Hai pháp nhân khác nhau cùng tập đoàn: hệ thống gợi ý, không tự gộp."* | Bỏ qua nếu thiếu giờ |
| 8:10–9:30 | Bong bóng trợ lý (bấm góc phải dưới, nút **phóng to** nếu câu trả lời dài) | Hỏi 3 câu: (1) *"Mã số thuế của Vinamilk là gì?"* → bấm thẻ nguồn; (2) *"Ai là giám đốc mua hàng ở Hòa Phát?"*; (3) *"Giá vàng hôm nay bao nhiêu?"* | *"Trả lời từ đúng hồ sơ vừa tạo, có nguồn. Câu ngoài dữ liệu thì nói không có."* | Câu (1) trượt: hỏi *"Công ty nào làm về logistics?"* (dữ liệu seed) |
| 9:30–10:00 | `/api/export/companies.csv` | Tải file, mở Excel | *"Xuất CSV/JSON để đưa sang CRM."* | — |

## 4. Câu hỏi cho trợ lý AI

| Câu | Kỳ vọng | Dựa trên |
|-----|---------|----------|
| Mã số thuế của Vinamilk là gì? | MST của CTCP Sữa Việt Nam, thẻ nguồn trỏ về hồ sơ | Hồ sơ tạo trong demo |
| Ai là giám đốc mua hàng ở Hòa Phát? | Lê Thu Hà | Thẻ `vi-02` |
| Có ai làm ở công ty Nhật không? | 山田 花子, 株式会社日立製作所 | Thẻ `ja-01` |
| Công ty nào làm về logistics? | Logistics Đại Việt | Dữ liệu seed (`scripts.seed`) |
| Giá vàng hôm nay bao nhiêu? | Không có thông tin, không có nguồn | Câu ngoài phạm vi |

> Bộ chấm điểm chính thức cho A6 là `docs/qa-testset.md`, chấm **trước** phần tạo hồ sơ demo (câu chặn X3 hỏi đúng
> MST Vinamilk và chỉ hợp lệ khi KB chưa có hồ sơ Vinamilk).

## 5. Chọn model cho buổi demo

Số đo ở `docs/profile-quality.md` (10.9): **cả `gemini-3-flash` lẫn `gemini-3.6-flash-high` đều đạt A5** (10/10 MST khớp
trang nguồn, công ty bịa 0 trường). `gemini-3.6-flash-high` nhanh hơn (32 s so với 43 s mỗi công ty) và là model đã
dùng trong lượt tổng duyệt 1 (A6 đạt 10/10). **Chốt: giữ `gemini-3.6-flash-high`** — đổi model sát giờ demo là thêm một
biến chưa tổng duyệt. Nếu tài khoản bị `429 cooldown` với model này thì đổi sang `gemini-3-flash` (mục 2 bước 2).

## 6. Nhật ký tổng duyệt (11.8)

Mỗi lượt: chạy mục 2 bước 5 rồi làm lại từ đầu mục 3, bấm giờ từng đoạn. Điểm vấp chia về đúng chủ module.

### Lượt 1 — tổng duyệt kỹ thuật, 2026-09-21

Máy dev của T, `gemini-3.6-flash-high`, commit `61e1385`. Chạy **qua đúng các API mà giao diện gọi** (script bấm giờ
từng bước) để đo phần máy chạy; thao tác bấm trên giao diện đã kiểm riêng ở 8.8 / 10.7. Chuẩn bị theo mục 2 (bước 4, 5).

| Bước | Số đo | Kết quả |
|------|------:|---------|
| Chấm `docs/qa-testset.md` (15 câu, trước khi tạo hồ sơ) | 110 s | **10/10**, ngoài phạm vi **3/3**, nhiều lượt đạt |
| Quét `vi-01` | 10 s | Đủ trường, SĐT `+84900000001` |
| Xác nhận | < 1 s | Gắn công ty + index KB |
| Quét lô 6 thẻ | 33 s | 6/6 xong (Anh, Nhật, Hàn) |
| Xác nhận 6 thẻ | < 1 s | *Hòa Phát* gộp thành 1 công ty 2 thẻ; Samsung VN ↔ 삼성전자 gợi ý *cùng tên miền* |
| Tạo hồ sơ 3 công ty | 68 s | Vinamilk 9, Coteccons 11, Hòa Phát 10 trường có nguồn |
| Huỷ Hitachi sau 4 s | tức thì | `cancelled`, không để lại hồ sơ |
| 3 câu hỏi demo | 27 s | MST Vinamilk đúng + trích hồ sơ; *Lê Thu Hà* + trích thẻ; câu giá vàng từ chối |
| Export `companies.csv` | < 1 s | Có BOM |
| Ẩn hồ sơ Vinamilk → hỏi → hiện lại → hỏi (TS-09) | — | Ẩn: trợ lý *"không có thông tin"*, 0 trích dẫn; hiện lại: trả lời lại đúng |
| Tắt CLIProxy → tạo hồ sơ → bật lại → chạy lại (TS-08) | 60 s / 21 s | Lỗi đọc được sau 60 s; chạy lại xong, 10 trường |

**Điểm vấp:**

| Điểm vấp | Mức | Chủ | Xử lý |
|----------|-----|-----|-------|
| CLIProxy không chạy → **60 s** mới báo lỗi, vì thử lại lồng nhau (client LLM 3 lần × job 3 lượt) cho một lỗi không tạm thời | Minor | **T** | **B2-05** trong `docs/bugs-f2.md`; khi demo lỗi mạng thì chuyển sang video dự phòng, đừng chờ |
| Ô *Điện thoại phụ* **trống vì thẻ không có số phụ** vẫn bị chấm độ tin cậy 0% và tô vàng (*"Cần kiểm: 1 trường"*) — báo động giả trên mọi thẻ demo | Minor | **Q** | Bổ sung vào **I-28** |
| Tên hiển thị của công ty gộp phụ thuộc thẻ nào xác nhận trước | — | — | Không phải lỗi; đã ghi vào mục 3 để người nói không bất ngờ |

### Còn lại cho 11.8

| Lượt | Ngày | Máy | Tổng thời gian | Điểm vấp | Chủ | Trạng thái |
|------|------|-----|----------------|----------|-----|------------|
| 2 | | Máy sạch (`docker compose down -v` → build → up) | | | | ⬜ Cả nhóm, bấm trên giao diện, người nói thật |
| 3 | | Máy trình bày | | | | ⬜ Cả nhóm |
