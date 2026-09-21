# Độ chính xác — OCR danh thiếp (F1) & Trợ lý AI (F3)

> Chủ sở hữu: **Q** · Task: **10.8** (phần A) / **10.1** (phần B) · Đo ngày **2026-09-21**
> Môi trường: Docker Compose trên máy dev · `LLM_MODEL = gemini-3-flash` qua CLIProxy (OAuth
> `quanpyke@gmail.com`) · embedding `intfloat/multilingual-e5-small` (384 chiều) ·
> `alembic current = 0004 (head)` · `pytest` 416 passed

Hai phần đo hai thứ khác nhau và **không** thay thế cho nhau: phần A đo model đọc chữ trên ảnh,
phần B đo trợ lý trả lời đúng từ Knowledge Base. Trượt ở A thì B cũng sai theo, nhưng A đạt
không nói gì về B.

---

# Phần A — Độ chính xác OCR (task 10.8)

## 1. ⚠️ Đây KHÔNG phải phép đo tiêu chí A3

Tiêu chí **A3** (`Plan.md` mục 8) đòi: *độ chính xác trường ≥ 85% trên **30 ảnh mẫu** với ảnh rõ
nét*. Phép đo đó là task **7.8**, và nó vẫn **⏸️ bị chặn** đúng lý do đã ghi từ D7:

> `samples/cards/` **có 0 ảnh**. Task 1.11 → 3.9 (chủ sở hữu **T**) mới dựng khung thư mục và
> `samples/expected.json` với một mục ví dụ; ảnh thật chưa thu thập. Kiểm lại 2026-09-21: vẫn
> chỉ có `.gitkeep`.

Không có 30 ảnh chụp thật thì **không đo được A3**, và không con số nào ở dưới được phép đem ra
thay thế. Ảnh dùng ở đây **dựng bằng phông chữ**: chữ sắc nét tuyệt đối, không nghiêng, không
loá, không bóng, nền trắng tinh — tức là dễ hơn hẳn mọi tấm ảnh người dùng chụp bằng điện thoại
tại hội thảo. Một con số 100% trên bộ này chỉ nói *prompt không tự phá chính nó*, không nói
*model đọc được danh thiếp thật*.

Cái phần A **thật sự** trả lời: sau khi 9.3 sửa quy tắc 4 của `prompts/ocr.py`, prompt còn đúng
trên cả 5 ngôn ngữ trong phạm vi không, và nó bắt đầu hỏng ở đâu khi chữ mất nét.

## 2. Cách đo

```bash
python -m scripts.check_multilang_ocr --blur 4.0     # chạy ở MÁY, stack Docker đang chạy
```

Script dựng 5 danh thiếp bằng phông của Windows (`malgun`/`msgothic`/`msyh`/`arial`), upload qua
đúng endpoint người dùng dùng (`POST /api/cards/upload`), rồi chấm từng trường so với đáp án.

- **Đơn vị chấm là một trường**, giống cách `samples/expected.json` sẽ chấm ở 7.8 — để hai số
  đem so với nhau được khi T giao ảnh.
- Trường `full_name` và `company_name_raw` của ba thẻ CJK bị chấm **hai điều kiện**: khớp chữ
  **và** còn là chữ bản địa. Model trả `Kim Min-jun` thay cho `김민준` là sai đúng thứ quy tắc 3
  của prompt cấm, nhưng nhìn qua vẫn "có vẻ đúng".
- `website` có đáp án kèm `https://` dù thẻ không in scheme: API trả bản **đã chuẩn hoá** bởi
  `normalize_website()` (task 3.6). Đây là kỳ vọng của bài test, không phải model bịa thêm.
- Thẻ quét hỏng vẫn tính vào **mẫu số**. Bỏ ra thì càng nhiều ảnh trượt tỉ lệ càng đẹp.

## 3. Kết quả

| Lượt | Trường đúng / tổng | Tỉ lệ | Thời gian OCR |
|------|--------------------|-------|----------------|
| **Sắc nét** (ảnh dựng nguyên bản) | **28 / 28** | **100.0%** | 3.0 – 5.7 s/ảnh |
| Nhoè nhẹ (Gaussian r = 2.5) | 27 / 28 | 96.4% | 4.2 – 4.8 s/ảnh |
| Nhoè rõ (Gaussian r = 4.0) | 25 / 28 | 89.3% | 4.0 – 5.7 s/ảnh |

### Chi tiết lượt sắc nét — 0 điểm trượt

| Thẻ | `language_detected` | Trường chấm | Ghi chú |
|-----|---------------------|-------------|---------|
| KO — song ngữ Hàn–Anh | ✅ `ko` | 4/4 | Lấy đúng mặt chữ Hàn (`김민준`, `한화정밀기계`), không phiên âm sang Latin — quy tắc 3 đứng vững |
| JA — thuần Nhật, 2 số | ✅ `ja` | 5/5 | `携帯: 090-…` vào `phone`, `TEL: 03-…` vào `phone_alt` — **đúng quy tắc 4a** (ưu tiên số di động), không phải theo thứ tự in |
| ZH — thuần Trung, nhãn chữ Hán | ✅ `zh` | 4/4 | Bóc đúng nhãn `电话`/`邮箱`/`地址` khỏi giá trị |
| VI — có dấu, nhãn viết tắt | ✅ `vi` | 5/5 | Giữ nguyên dấu, bóc đúng nhãn `ĐT:` / `Email:` / `Địa chỉ:` |
| EN | ✅ `en` | 5/5 | Nhãn `Mobile:` → quy tắc 4a |

### Prompt hỏng ở đâu khi chữ mất nét

Ba điểm trượt ở r = 4.0, **tất cả đều là sai một ký tự**, không phải bịa nguyên trường:

| Thẻ | Trường | Đáp án | Model đọc | Kiểu sai |
|-----|--------|--------|-----------|----------|
| ZH | `email` | `li.wei@yuanjing-elec.cn` | `l.wei@yuanjing-elec.cn` | mất chữ `i` |
| VI | `email` | `maianh.nguyen@haidang-logistics.vn` | `maianh.nguyen@haidanglogistics.vn` | mất dấu `-` |
| VI | `website` | `https://www.haidang-logistics.vn` | `https://www.haidanglogistics.vn` | mất dấu `-` |

Đáng chú ý là kiểu sai: model **không bịa** một email khác trông hợp lý, nó chép thiếu đúng chỗ
chữ nhoè. Đó là hành vi quy tắc 1 của prompt nhắm tới, và là kiểu sai mà bước review của người
dùng sửa được trong 3 giây — khác hẳn kiểu sai "suy ra email từ tên miền" mà không ai phát hiện.

### Điểm `confidence` có phản ánh đúng ảnh mờ không

Có. Ca **E-01** của `scripts/e2e_f1_f3.py --suite edge` upload một thẻ tiếng Anh đã làm nhoè
(r = 3.2): **5 trên 7 trường** có điểm dưới ngưỡng `LOW_CONFIDENCE = 0.75`, tức
`templates/cards/detail.html` tô vàng đúng những chỗ đáng kiểm, và thẻ về `needs_review` chứ
không tự xác nhận. Đây là lớp phòng thủ số 2 của `prompts/ocr.py` và nó đang chạy đúng.

### Ảnh không phải danh thiếp

Ca **E-02**: ảnh phong cảnh (dải màu trời–cỏ, không một chữ nào) → `is_business_card: false`,
**toàn bộ 8 trường nội dung `null`**, `notes` ghi *"Model cho rằng ảnh này không phải danh thiếp
— kiểm lại trước khi xác nhận."* Không trường nào bị vét chữ ra để điền. Quy tắc 6 chạy đúng.

## 4. Kết luận phần A

- Prompt sau khi 9.3 chỉnh quy tắc 4 **không hồi quy** ở bất kỳ ngôn ngữ nào trong phạm vi
  (Plan.md mục 1.3: Anh / Việt / Hàn / Nhật / Trung). Không sửa thêm gì ở `prompts/ocr.py`
  trong 10.8 — sửa một prompt đang đúng chỉ để "có sửa" là cách nhanh nhất làm nó hỏng.
- Khi chữ bắt đầu mất nét, hỏng theo kiểu **sai ký tự trong trường định danh** (email, website),
  không theo kiểu bịa cả trường. Bước review + `confidence` là đúng lớp phòng thủ cho kiểu hỏng
  này.
- **Tiêu chí A3 vẫn chưa đo được.** Đây là đường găng còn lại của F1 và nó nằm ở phía **T**
  (task 3.9). Nêu ở daily D11.

---

# Phần B — Độ chính xác trợ lý AI (tiêu chí A6, task 10.1)

## 5. Vì sao mãi tới D10 mới đo được

Tiêu chí hoàn thành của **D8** (chat đúng ≥ 7/10) và tiêu chí **A6** (≥ 8/10) chấm trên 10 câu ở
`docs/qa-testset.md`. Bộ câu hỏi đó bám vào bộ dữ liệu cố định trong
`scripts/eval_retrieval.py::seed()` — mà hàm ấy **rollback** ở cuối nên chat thật không thấy dữ
liệu. D8 chỉ đo được 3 câu chặn và cặp câu nhiều lượt; ô "Tiêu chí hoàn thành" của D8 treo từ đó.

Mảnh còn thiếu là `scripts/seed.py` (task **9.2**, xong 2026-09-18): cùng bộ dữ liệu ấy nhưng
**commit**. Vì vậy đây là **lần đầu tiên 10 câu tính điểm được chấm**.

## 6. Cách đo

```bash
python -m scripts.eval_assistant
```

Máy chấm được **hai trong ba** luật ở `qa-testset.md` mục 3, và nói rõ nó không chấm được luật
còn lại thay vì giả vờ chấm được:

| Luật | Máy chấm? | Cách |
|------|-----------|------|
| 1 — đủ dữ kiện bắt buộc | ✅ | So khớp bỏ dấu phụ; SĐT so theo chữ số nên `0987 654 321` và `+84987654321` là một |
| 2 — **không bịa thêm** | ❌ | Máy không biết "thành lập năm 2005" là đúng hay bịa. Script **in nguyên văn mọi câu trả lời**, người đọc lại và kết luận. Kết quả dưới đây đã đọc tay từng câu |
| 3 — có trích dẫn trỏ đúng nguồn | ✅ | `citations[].source_id` so với id thật trong DB, tra qua chính `GET /api/cards` và `GET /api/companies` |

KB lúc đo: **6 danh thiếp + 3 hồ sơ doanh nghiệp → 9 chunk**. Gồm bộ cố định 4 thẻ + 3 hồ sơ của
`qa-testset.md`, cộng 2 thẻ dev còn lại (`山田 太郎`, `Trần Thị Bích Ngọc`) — bản ghi trùng đã
dọn trước khi đo (xem B-09 ở `bugs-f1-f3.md`).

## 7. Kết quả — **10/10**

| # | Câu hỏi | Đạt? | Ghi chú |
|---|---------|------|---------|
| 1 | Công ty nào làm về logistics? | ✅ | Trả đúng P1, 1 trích dẫn |
| 2 | Mã số thuế của Công ty CP Sữa Mộc Châu? | ✅ | `0100233468` |
| 3 | Hanwha Precision Vietnam cung cấp sản phẩm gì? | ✅ | Nêu **đủ** 2 mục, không dừng ở mục đầu (quy tắc 5) |
| 4 | Email của Trần Thị Bình? | ✅ | |
| 5 | Số 0912 345 678 là của ai? | ✅ | Trả **hai** người — đúng sự thật trong KB: thẻ dev `Trần Thị Bích Ngọc` trùng số với C1. Không phải bịa |
| 6 | Ai là giám đốc kinh doanh của Logistics Đại Việt? | ✅ | |
| 7 | Kim Min-jun làm chức vụ gì, ở công ty nào? | ✅ | Giữ nguyên `영업 과장`, không phiên âm (quy tắc 6) |
| 8 | Có ai làm ở công ty Nhật không? | ✅ | **Trượt ở lượt đo đầu** — xem B-03. Sau khi chunk ghi `Ngôn ngữ: Tiếng Nhật (ja)` thì trả đủ cả 2 người |
| 9 | Liên hệ công ty sản xuất sữa thì gọi cho ai? | ✅ | Nối hồ sơ DN → danh thiếp, trích dẫn cả hai nguồn |
| 10 | Mã số thuế 0301234567 là của công ty nào? | ✅ | Tra ngược từ định danh |

**Ba câu chặn (điều kiện chặn — trượt một câu là cả lượt đo bỏ đi): 3/3 ĐẠT.**

| # | Câu hỏi | Kết quả |
|---|---------|---------|
| X1 | Giá vàng hôm nay bao nhiêu? | "Không có thông tin này trong dữ liệu đã nhập." — `citations` rỗng |
| X2 | Hướng dẫn nấu phở bò | như trên |
| X3 | **Mã số thuế của Vinamilk là gì?** | như trên — **không** nêu con số nào, dù KB có một công ty sữa khác và model biết sẵn đáp án |

X3 là câu đáng kể nhất: tầng truy hồi vẫn trả về 5 chunk cho cả ba câu này (7.4 đã đo được là
không tồn tại ngưỡng điểm nào tách được câu lạc đề — hai phân bố chồng lên nhau). Việc từ chối
hoàn toàn do `prompts/assistant.py` quyết định, và nó đứng vững.

**Hội thoại nhiều lượt: đạt.** Lượt 2 *"Số điện thoại của chị ấy là gì?"* không nhắc tên ai mà
vẫn trả đúng số của Trần Thị Bình → lịch sử đã vào prompt (task 8.3).

## 8. Kết luận phần B

| Tiêu chí | Ngưỡng | Đo được | Kết luận |
|----------|--------|---------|----------|
| Tiêu chí hoàn thành **D8** | ≥ 7/10 | **10/10** | ✅ Đạt — ô treo từ D8 nay đóng được |
| **A6** (`Plan.md` mục 8) | ≥ 8/10 | **10/10** | ✅ Đạt |
| Câu ngoài phạm vi | 3/3 | **3/3** | ✅ Đạt, số đo trên dùng nghiệm thu được |

> ⚠️ Hai ngưỡng khác nhau (D8 ghi ≥7, A6 ghi ≥8) vẫn **chưa được chốt về một con số** — ghi chú
> này có từ D8 và vẫn đúng. Lần này không thành vấn đề vì 10/10 vượt cả hai, nhưng phải chốt
> trước khi có một lượt đo rơi vào khoảng 7–8.
>
> 📋 Bảng kết quả theo mẫu ở `qa-testset.md` mục 8 cần **T** chép vào file đó — `docs/qa-testset.md`
> thuộc quyền sở hữu của T (quy ước số 2).

## 9. Hạn chế đã biết của số đo này

1. **Bộ 10 câu và bộ dữ liệu là cố định và nhỏ** (9 chunk). Nó kiểm *hệ thống có chạy đúng
   không*, không kiểm *nó chịu được bao nhiêu dữ liệu*. Không suy ra được gì về KB vài nghìn
   chunk.
2. **Câu hỏi tiếng Việt gõ không dấu kiểu mô tả vẫn trượt** (B-10 / I-23) — cố ý nằm ngoài 10 câu
   tính điểm. Đưa vào thì một điểm luôn mất vì một lý do đã biết, che mất lỗi mới.
3. **Luật 2 chấm bằng mắt người.** Mười câu trên đã đọc tay từng câu ngày 2026-09-21, nhưng đây
   là bước không tự động hoá được và sẽ phải làm lại nếu đổi model.
