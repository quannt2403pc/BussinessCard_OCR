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

---

# Phần C — Việt hoá sau khi quét (task EX-07)

> Đo ngày **2026-09-22** · `scripts/check_translation.py` · `LLM_MODEL = gemini-3-flash` qua
> CLIProxy (OAuth `quanpyke@gmail.com`) · `alembic current = 0006 (head)` · `pytest` 556 passed

## 10. Đo cái gì, và không đo cái gì

Phần A đo **model đọc đúng chữ trên ảnh chưa**. Phần C đo bước ngay sau đó: **chữ đọc được có
thành thứ người Việt dùng được không** — dịch chức vụ và loại hình pháp nhân, phiên âm tên riêng
(`services/translate.py`, EX-02).

**Không** đo lại phần A: prompt OCR chỉ đổi đúng một chỗ ở EX-05 (mã ngôn ngữ hết bị ép vào 5
giá trị), còn quy tắc 1, 2, 4, 5, 6 không đổi một chữ. Cột `language_detected` ở 6/6 ca dưới đây
trả đúng mã, gồm cả ba mã mà bản cũ không thể trả.

**Không** chấm bằng máy chuyện *cách phiên âm nào đúng*: `李伟` nên là *Lý Vĩ* (Hán Việt, quy ước
tiếng Việt) hay *Li Wei* (bính âm) là việc phải đọc bằng mắt. Script in cả bảng ra để đọc; phần
chấm tự động chỉ nhận những thứ có đáp án: mã ngôn ngữ, chữ gốc còn nguyên, bản dịch hết chữ bản
địa, chức vụ khớp bảng, loại hình pháp nhân khớp tiền tố, nguồn bản dịch là `llm`.

## 11. Kết quả — **36/36 phép kiểm đạt**

| Thẻ | Gốc | Việt hoá | Cách phiên âm |
|-----|-----|----------|---------------|
| `ja` | 田中 太郎 · 営業部長 · 東京テック株式会社 | **Tanaka Taro** · Trưởng phòng Kinh doanh · Công ty Cổ phần Tokyo Tech | Romaji |
| `zh` | 李伟 · 销售经理 · 深圳市远景电子有限公司 | **Lý Vĩ** · Trưởng phòng Kinh doanh · Công ty TNHH Điện tử Viễn Cảnh Thâm Quyến | Hán Việt |
| `ko` | 김민준 · 부장 · 한화정밀기계 주식회사 | **Kim Min-jun** · Trưởng phòng · Công ty Cổ phần Máy móc Chính xác Hanwha | chuyển tự Latin |
| `th` | สมชาย ใจดี · ผู้จัดการฝ่ายขาย · บริษัท สยามเทค จำกัด | **Somchai Jaidee** · Trưởng phòng Kinh doanh · Công ty TNHH Siam Tech | chuyển tự Latin |
| `ru` | Иван Петров · Генеральный директор · ООО Яндекс Технологии | **Ivan Petrov** · Tổng giám đốc · Công ty TNHH Công nghệ Yandex | chuyển tự Latin |
| `de` | Hans Müller · Vertriebsleiter · Müller Maschinenbau GmbH | **(không dịch)** · Trưởng phòng Kinh doanh · Công ty TNHH Chế tạo Máy Müller | — |

Địa chỉ cũng ra đúng lối đã định — từ chỉ loại dịch, tên riêng phiên âm, thứ tự giữ nguyên:
`東京都千代田区丸の内1-2-3` → *Thành phố Tokyo, Quận Chiyoda, Marunouchi 1-2-3*;
`广东省深圳市南山区科技园路 18 号` → *Tỉnh Quảng Đông, Thành phố Thâm Quyến, Quận Nam Sơn, Đường
Khoa Kỹ Viên số 18*.

Ba điều đáng ghi lại:

1. **Ba ngôn ngữ ngoài phạm vi cũ (Thái, Nga, Đức) chạy y như ba ngôn ngữ trong phạm vi.** Không
   có nhánh mã nào riêng cho chúng — đó là điểm của EX-05: bỏ tập đóng 5 mã đi thì phần còn lại
   của hệ thống vốn đã không quan tâm ngôn ngữ nào.
2. **Thẻ Đức không sinh bản dịch tên người, và đó là kết quả ĐÚNG.** `_finalize()` bỏ bản dịch
   trùng y hệt bản gốc, nên `full_name_vi = NULL` nghĩa là *bản gốc dùng được luôn*. Không có
   quy tắc này thì giao diện in "Hans Müller" hai lần chồng lên nhau trên mọi thẻ Latin.
3. **Chức vụ tiếng Thái (`ผู้จัดการฝ่ายขาย`) không có trong `JOB_TITLES`** mà vẫn ra *Trưởng phòng
   Kinh doanh*. Đây là ranh giới giữa hai tầng: bảng tra cứu giữ tính nhất quán cho phần hay gặp,
   model phủ phần đuôi dài — và phần đuôi dài là toàn bộ lý do không thể làm bằng bảng tra cứu.

## 12. Hạn chế đã biết của phần C

1. **Ảnh dựng bằng phông chữ, không phải ảnh chụp** — cùng hạn chế với phần A, và cùng lý do:
   tiêu chí A3 vẫn là task **7.8**, vẫn ⏸️.
2. **Sáu thẻ, mỗi ngôn ngữ một thẻ.** Đủ để nói *cơ chế chạy đúng*, không đủ để nói tỉ lệ phiên
   âm đúng trên tên người thật (tên hiếm, tên có nhiều cách đọc).
3. **Phiên âm không tất định theo kiểu bảng tra.** `temperature = 0.0` giữ cho cùng một thẻ ra
   cùng một kết quả, nhưng **đổi model là phải đo lại cả bảng trên** — khác hẳn phần loại hình
   pháp nhân, vốn do `LEGAL_FORMS` quyết định và có test riêng trong `tests/test_translate.py`.
4. **Địa chỉ dài chưa đo ở ca xấu**: thẻ có địa chỉ hai dòng kèm toà nhà/tầng chưa nằm trong bộ
   này. `address_vi` là cột `TEXT` nên không có rủi ro tràn, nhưng cách sắp xếp lại thì chưa đo.

---

# Phần D — Model theo từng chức năng (task `EX-16`)

> Đo ngày **2026-09-24** · trên hệ thật trong Docker, qua đúng các API mà giao diện gọi ·
> CLIProxy + OAuth thật · bảng năng lực từng model ở [`adr-model-per-feature.md`](./adr-model-per-feature.md)

## 13. Đo cái gì

Từ `EX-14`, tên model không còn là hằng số mà là kết quả của **lựa chọn của người dùng × bảng
năng lực × danh mục thật lúc chạy**. Phần này trả lời đúng ba câu, tất cả bằng lời gọi thật:

1. Ba chức năng có đi bằng **ba model khác nhau cùng lúc** không?
2. Mỗi chỗ có **khai đúng model vừa gọi** không — hay vẫn khai model mặc định như trước (**I-34**)?
3. Model không đủ năng lực có **bị chặn ngay lúc chọn** không?

**Không** đo ở đây: chất lượng tương đối giữa các model. Mỗi phép dưới đây chạy **một lượt**, và
một lượt thì không xếp hạng được cái gì.

## 14. Kết quả — ba chức năng, ba model, cùng một thời điểm

| Chức năng | Model đã chọn | Chỗ ghi lại | Khai ra | Thời gian |
|-----------|---------------|-------------|---------|-----------|
| Quét danh thiếp *(nút **Dịch lại**)* | `gemini-3.1-flash-lite` | `translation_meta.model` | ✅ `gemini-3.1-flash-lite` | 23,8s |
| Quét danh thiếp *(cùng thẻ, lượt 2)* | `gemini-3.6-flash-high` | `translation_meta.model` | ✅ `gemini-3.6-flash-high` | 9,4s |
| Lập hồ sơ doanh nghiệp | `gemini-3.7-flash-high` | `company_profiles.llm_model` | ✅ `gemini-3.7-flash-high` | ~3 phút (chạy nền) |
| Trợ lý AI | `gemini-3.8-flash-high` | `ChatOut.model` | ✅ `gemini-3.8-flash-high` | 4,1s · 2 trích dẫn |

Ba lựa chọn trên **sống cùng lúc trên một tài khoản** — đây chính là yêu cầu của chủ dự án, và là
thứ không kiểm được bằng một biến `LLM_MODEL` duy nhất.

### Đổi model ra kết quả khác thật, không chỉ khác tên

Cùng một tấm thẻ tiếng Ả Rập (`محمد عبدالله العتيبي` · `أرامكو السعودية`), bấm *Dịch lại* hai lần
với hai model:

| Model | `company_name_vi` |
|-------|-------------------|
| `gemini-3.1-flash-lite` | Công ty Aramco Saudi |
| `gemini-3.6-flash-high` | Công ty Saudi Aramco ← đúng |

Model nhanh hơn 2,5 lần **đảo thứ tự tên riêng**. Một mẫu không kết luận được gì về chất lượng
trung bình, nhưng nó cho thấy quyền chọn model là quyền thật, không phải nút trang trí.

## 15. Chặn ở đúng chỗ chặn được

| Thử gì | Kết quả |
|--------|---------|
| Ô *Lập hồ sơ* có hiện `claude-*` không | **Không** — 7/12 model, đúng danh sách đo được |
| Ô *Quét danh thiếp* có hiện `gpt-oss-120b-medium` không | **Không** — 11/12 model |
| Ô *Trợ lý AI* | **12/12** — chat không cần ảnh cũng không cần tra cứu |
| `PUT {"enrich": "claude-sonnet-4-6"}` gọi thẳng API | **422** — *“Model `claude-sonnet-4-6` không dùng được cho Lập hồ sơ doanh nghiệp. Chọn trong danh sách gợi ý.”* |
| Sau lần 422 đó, DB có lưu gì không | **Không** — từ chối là không lưu, không phải lưu rồi báo |

Ba con số 11 / 7 / 12 đọc thẳng từ ô chọn trên `/settings` của trình duyệt thật, tức chúng đi trọn
đường **danh mục CLIProxy → bảng năng lực → API → giao diện**, không phải đọc từ hằng số trong mã.

## 16. Hạn chế đã biết của phần D

1. **Mỗi phép một lượt.** Bảng ở mục 14 chứng minh *đường dây đúng*, không chứng minh model nào tốt
   hơn. Muốn xếp hạng thì dùng `scripts/spike_profile_quality.py` và `scripts/eval_assistant.py`.
2. **Chỉ một tấm thẻ cho phần Việt hoá**, và là thẻ tiếng Ả Rập — chọn nó vì nó nằm sẵn trong dữ
   liệu thật, không phải vì nó đại diện cho gì.
3. **Không đo lại A3/A5/A6 cho từng model.** Người dùng nay chọn được 11 model cho quét thẻ, mà độ
   chính xác OCR ở phần A chỉ đo trên `gemini-3-flash`. Ai đổi model là bước ra ngoài phạm vi số
   đo đó — mục 16 này là chỗ ghi lại điều ấy, không phải chỗ hứa sẽ đo hết.
4. **Bảng năng lực là ảnh chụp ngày 2026-09-24.** Danh mục channel tự đổi (11 → 12 model trong 14
   ngày); thấy tên model lạ trong ô chọn thì chạy lại `scripts/spike_model_matrix.py`.
