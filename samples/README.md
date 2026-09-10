# Bộ dữ liệu mẫu

> Chủ sở hữu: **T** · Task **1.11** (tạo cấu trúc + bắt đầu thu thập) → **3.9** (hoàn tất 30 ảnh + `expected.json`)
> Dùng cho: đo độ chính xác OCR (task 7.8 / 10.8, Q làm) và dữ liệu demo (task 11.2 / 11.7)

---

## 1. Cấu trúc thư mục

| Thư mục | Commit vào Git? | Nội dung |
|---------|-----------------|----------|
| `samples/cards/` | ✅ Có | Ảnh danh thiếp **đã ẩn danh**, dùng để đo độ chính xác |
| `samples/demo/` | ✅ Có | Bộ ảnh đẹp dùng khi trình diễn (task 11.7) |
| `samples/private/` | ❌ **Không** — `.gitignore` đã chặn | Ảnh danh thiếp thật chưa ẩn danh |
| `samples/expected.json` | ✅ Có | Kết quả kỳ vọng của từng ảnh trong `cards/` |

## 2. 🔒 Quy tắc ẩn danh — bắt buộc

Danh thiếp chứa **thông tin cá nhân thật** (tên, số điện thoại, email của người khác).
Repo này sẽ được nộp/chia sẻ, nên:

1. **Không commit ảnh danh thiếp thật.** Ảnh thật để trong `samples/private/` (đã bị gitignore).
2. Ảnh trong `samples/cards/` phải là một trong ba loại:
   - Danh thiếp **tự tạo** bằng công cụ thiết kế với thông tin bịa;
   - Danh thiếp của **chính mình / người đã đồng ý**;
   - Danh thiếp thật đã **thay tên, SĐT, email** bằng dữ liệu giả (giữ nguyên layout, phông chữ,
     ngôn ngữ — đó mới là thứ ảnh hưởng tới OCR).
3. Số điện thoại giả nên dùng dải không có thật; email giả dùng tên miền `example.com`.

> Ẩn danh **không** làm hỏng mục đích đo lường: cái ta đo là khả năng đọc layout đa ngôn ngữ,
> không phải khả năng đoán đúng tên một người cụ thể.

## 3. Quy ước đặt tên

```
<mã ngôn ngữ>-<số thứ tự 2 chữ số>-<độ khó>.<ext>

vi-01-clear.jpg      en-04-blur.png       ja-02-clear.jpg
ko-03-angle.jpg      zh-01-lowlight.jpg
```

| Thành phần | Giá trị |
|------------|---------|
| Mã ngôn ngữ | `en`, `vi`, `ko`, `ja`, `zh` (ISO 639-1, khớp `language_detected`) |
| Độ khó | `clear` (rõ nét) · `blur` (mờ) · `angle` (chụp nghiêng) · `lowlight` (thiếu sáng) · `dense` (nhiều chữ/2 cột) |

Tiêu chí A3 chỉ yêu cầu **≥ 85% độ chính xác trường với ảnh rõ nét** — ảnh khó dùng để biết
giới hạn của hệ thống, không tính vào con số nghiệm thu.

## 4. Mục tiêu thu thập (30 ảnh)

| Ngôn ngữ | Mục tiêu | Đã có | Ghi chú |
|----------|----------|-------|---------|
| Tiếng Việt (`vi`) | 8 | 0 | Bắt buộc — tiêu chí A4 |
| Tiếng Anh (`en`) | 8 | 0 | Bắt buộc — tiêu chí A4 |
| Tiếng Nhật (`ja`) | 5 | 0 | Cần ≥1 trong 3 ngôn ngữ CJK |
| Tiếng Hàn (`ko`) | 5 | 0 | |
| Tiếng Trung (`zh`) | 4 | 0 | |
| **Tổng** | **30** | **0** | |

Trong 30 ảnh nên có khoảng **20 `clear`** và **10 ảnh khó** (blur/angle/lowlight/dense).

> **Trạng thái hiện tại: chưa có ảnh nào.** Việc thu thập cần người thật đi lấy danh thiếp
> hoặc tự thiết kế — không tự động hoá được. Hoàn tất ở task 3.9 (D3).

## 5. `expected.json`

Mỗi ảnh trong `samples/cards/` phải có đúng một mục trong `expected.json`, ghi **giá trị đúng
do người đọc bằng mắt**, dùng làm mốc so sánh với kết quả OCR.

Xem cấu trúc và ví dụ trong chính file [`expected.json`](./expected.json).

Quy tắc điền:

- Trường **không có trên danh thiếp** → `null`. Không bịa, không suy luận.
- Ghi **nguyên văn như in**, kể cả sai chính tả trên thiếp.
- `phone` / `email` ghi **dạng thô như in trên thiếp**; việc chuẩn hoá E.164 là của
  `services/normalize.py`, được đo riêng bằng `tests/test_normalize.py`.
- `address` gộp nhiều dòng thành một chuỗi, ngăn bằng `, `.

## 6. Cách bộ dữ liệu này được dùng

| Task | Ai | Dùng làm gì |
|------|-----|-------------|
| 3.9 | T | Hoàn tất 30 ảnh + điền đủ `expected.json` |
| 7.8 | Q | Chạy OCR toàn bộ `cards/`, so với `expected.json` → `docs/accuracy.md` |
| 9.3 | Q | Tinh chỉnh `prompts/ocr.py` dựa trên lỗi thực tế của ảnh CJK |
| 10.8 | Q | Đo lại sau khi tinh chỉnh prompt |
| 11.2 | Q | Nạp `samples/demo/` vào DB qua `scripts/seed.py` |
| 11.7 | T | Chọn bộ ảnh đẹp cho kịch bản demo |
