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
| 11.2 | Q | Nạp dữ liệu demo sạch — thực tế `scripts/seed.py` (9.2) nạp bộ hư cấu của `scripts/eval_retrieval.py` cho `docs/qa-testset.md`, **không** đọc `samples/demo/` |
| 11.7 | T | Chọn bộ ảnh đẹp cho kịch bản demo |

## 7. Bộ thẻ demo `samples/demo/` (task 11.7)

7 ảnh danh thiếp **tự tạo** bằng `samples/demo/make_cards.py` — loại 1 trong quy tắc ẩn danh ở mục 2:

| Ảnh | Công ty (có thật) | Người (bịa) | Dùng để minh hoạ |
|-----|-------------------|-------------|------------------|
| `vi-01-clear.png` | Công ty CP Sữa Việt Nam | Trần Minh Khoa | Quét thẻ tiếng Việt, tạo hồ sơ có nguồn (A3, A5) |
| `vi-02-clear.png` | Công ty CP Tập đoàn Hòa Phát | Lê Thu Hà | Chống trùng: gộp với `en-03` |
| `en-01-clear.png` | Coteccons Construction JSC | Nguyen Duc Anh | Thẻ tiếng Anh của công ty Việt Nam |
| `en-02-clear.png` | Samsung Electronics Vietnam | Park Ji-hoon | Cùng tên miền với `ko-01`: gợi ý cùng tập đoàn, **không** gộp |
| `en-03-clear.png` | Hoa Phat Group JSC | Pham Quoc Bao | Chống trùng: *"Group"* + tên tiếng Anh về cùng khoá `hoa phat` |
| `ja-01-clear.png` | 株式会社日立製作所 | 山田 花子 | Thẻ tiếng Nhật (A4) |
| `ko-01-clear.png` | 삼성전자 주식회사 | 김민수 | Thẻ tiếng Hàn (A4) |

- **Công ty có thật** để phần tạo hồ sơ tra cứu ra nguồn; **người, số điện thoại bịa** (`0900 000 00x`,
  `+81 3-0000-0005`, `+82 2-0000-0006`), **email là tên miền con của `example.com`**. Tên miền con, không phải
  `example.com` trần: nếu mọi thẻ chung một tên miền thì khối *"Cùng tên miền"* trên trang công ty sẽ nối tất cả
  công ty demo với nhau.
- Dữ liệu kỳ vọng từng thẻ: `samples/demo/cards.json`. Tạo lại ảnh: `python samples/demo/make_cards.py` (cần font
  Arial / Yu Gothic / Malgun Gothic của Windows).
- Chạy lại demo từ đầu: `docker compose exec -T db psql -U bizcard -d bizcard < samples/demo/reset_demo.sql` — xoá
  đúng thẻ và công ty demo, không đụng dữ liệu khác. Thiếu bước này thì lượt demo thứ hai **không quét lại được**,
  vì upload trùng ảnh bị chặn theo `image_hash`.
- Ảnh demo **không thay được bộ 30 ảnh của 3.9**: toàn ảnh rõ nét, một bố cục, nên đo trên đó chỉ cho con số đẹp
  hơn thực tế.
