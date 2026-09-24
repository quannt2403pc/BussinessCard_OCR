# ADR — Chọn model riêng cho từng chức năng

> Chủ sở hữu: **Q** · Task **EX-12** (đo) → **EX-13/14/15** (làm) · Vấn đề **I-33**, **I-12** · Tiêu chí liên quan **A3**, **A5**, **A6**
> Mở: 2026-09-24 · **Chốt: 2026-09-24** · Trạng thái: **✅ ĐÃ CHỐT**
> Đo trên CLIProxyAPI thật trong container `api`, channel `antigravity`, **12 model × 3 phép đo = 36 lượt gọi thật**
> Script: [`scripts/spike_model_matrix.py`](../scripts/spike_model_matrix.py)

---

## 1. Vấn đề

Chủ dự án yêu cầu (2026-09-24): **người dùng tự chọn model cho từng chức năng** — quét danh thiếp,
lập hồ sơ, trợ lý AI; mỗi chức năng có thể dùng một model khác nhau.

Câu hỏi chặn `EX-15` (giao diện chọn model):

> Ô chọn hiện những model nào?

Thả cả danh mục vào ô chọn là mời người dùng tự làm hỏng luồng của mình. Hai đường hỏng, **cả hai
đều im lặng**:

1. **Model không nhận ảnh** → mọi lượt quét danh thiếp hỏng, và lỗi chỉ lộ ra lúc bấm upload chứ
   không phải lúc bấm lưu. Trước ADR này **chưa ai đo** — task 2.3 chỉ kiểm chứng vision cho đúng
   **một** model (`gemini-3-flash`), 11 model còn lại là vùng trắng (**I-33**).
2. **Model không tra cứu được Internet** → hồ sơ doanh nghiệp sinh ra **không có nguồn nào**, tức
   trượt thẳng tiêu chí **A5**. Và nó không báo lỗi: model vẫn trả lời trơn tru, chỉ thiếu
   `groundingMetadata`.

## 2. Vì sao phải đo bằng lời gọi thật

Cờ `supports_web_search` của CLIProxy **không dùng được để chọn model**:

| Ngày | Cờ báo gì | Thực tế |
|------|-----------|---------|
| 2026-09-11 (I-12) | **không** cho 11/11 model | `gemini-3-flash` tra cứu được, trả 3 `groundingChunks` |
| 2026-09-21 (2.7) | **có** cho mọi model Gemini | khớp thực tế lượt đó |

Cùng một cờ, hai câu trả lời trái ngược, phụ thuộc tài khoản và thời điểm.
`docs/adr-websearch.md` mục **Q5** đã chốt: *chọn bằng lời gọi thật*. ADR này làm đúng thế cho
**cả ba** năng lực, không chỉ web search.

**Phép đo `ảnh` cố ý hỏi một chuỗi có thật trên tấm thẻ mẫu** (`coteccons`, từ
`samples/demo/en-01-clear.png`) chứ không hỏi *"bạn có thấy ảnh không"*. Model không nhận ảnh vẫn
trả lời câu hỏi thứ hai một cách trơn tru — đó là *âm tính giả* tự mình tạo ra.

## 3. Bảng năng lực đo được (2026-09-24)

| Model | JSON | Ảnh | Web | **Quét thẻ** | **Lập hồ sơ** | **Trợ lý** | Ghi chú |
|-------|------|-----|-----|----------|-----------|--------|---------|
| `claude-opus-4-6-thinking` | ✅ 4.4s | ✅ 4.3s | ❌ 123.5s | ✅ | ❌ | ✅ | không có `groundingChunks`; **chạm trần timeout 120s** |
| `claude-sonnet-4-6` | ✅ 5.6s | ✅ 3.0s | ❌ 2.4s | ✅ | ❌ | ✅ | không có `groundingChunks` |
| `gemini-3.6-flash-high` | ✅ 3.2s | ✅ 4.8s | ✅ 5.9s | ✅ | ✅ | ✅ | 1 nguồn |
| `gemini-3.7-flash-high` | ✅ 4.7s | ✅ 2.8s | ✅ 4.4s | ✅ | ✅ | ✅ | 2 nguồn |
| `gemini-3.8-flash-high` | ✅ 2.5s | ✅ 2.8s | ✅ 5.6s | ✅ | ✅ | ✅ | 2 nguồn |
| `gemini-3-flash` *(mặc định)* | ✅ 2.8s | ✅ 3.4s | ✅ 8.4s | ✅ | ✅ | ✅ | 1 nguồn |
| `gemini-3.1-flash-image` | ✅ 2.3s | ✅ 3.0s | ✅ 4.7s | ✅ | ✅ | ✅ | 5 nguồn |
| `gemini-pro-agent` | ✅ 5.9s | ✅ 3.9s | ❌ 8.7s | ✅ | ❌ | ✅ | không có `groundingChunks` |
| `gemini-3.1-pro-low` | ✅ 4.9s | ✅ 9.1s | ❌ 7.4s | ✅ | ❌ | ✅ | không có `groundingChunks` |
| `gpt-oss-120b-medium` | ✅ 1.2s | ❌ 2.7s | ❌ 1.5s | ❌ | ❌ | ✅ | **model duy nhất không đọc được ảnh** — trả `{"full_name": "none", "company": "none"}` |
| `gemini-3.1-flash-lite` | ✅ 1.3s | ✅ 1.3s | ✅ 1.9s | ✅ | ✅ | ✅ | 2 nguồn — **nhanh nhất** |
| `gemini-3.5-flash-lite` | ✅ 1.1s | ✅ 2.0s | ✅ 2.5s | ✅ | ✅ | ✅ | 1 nguồn |

> ⚠️ Danh mục channel `antigravity` nay có **12 model**, không phải 11 như I-03 ghi ngày 2026-09-10:
> `gemini-3.5-flash-lite` là model mới, `gemini-3.7-flash-high` / `gemini-3.8-flash-high` đã có sẵn.
> Đây đúng là lý do `Plan.md` mục 2.4 cấm hardcode danh mục theo tài liệu — **danh mục tự đổi**.

### Ba kết luận đáng nhớ

1. **Nhận ảnh gần như là mặc định: 11/12 model đọc được danh thiếp.** Nỗi lo lớn nhất của I-33 hoá
   ra nhỏ — nhưng nó **không rỗng**: `gpt-oss-120b-medium` trượt, và nó trượt theo kiểu tệ nhất là
   **trả lời bình thường với nội dung bịa** (`"none"`) thay vì báo lỗi. Nếu ai đó chọn nó cho quét
   danh thiếp thì mọi tấm thẻ vào hệ thống đều rỗng, không một dòng log đỏ nào.
2. **Tra cứu Internet mới là thứ hiếm: chỉ 7/12.** Và **không suy ra được từ tên model** — cả hai
   model `claude-*` trượt (đoán được), nhưng `gemini-pro-agent` và `gemini-3.1-pro-low` cũng trượt
   dù là Gemini. Luật *"Gemini thì tra được"* là **sai**.
3. **Trượt web search không ném lỗi.** Cả 5 model trượt đều trả `HTTP 200` kèm câu trả lời đọc được,
   chỉ thiếu `groundingMetadata`. Đúng lối hỏng mà **R4** (LLM bịa thông tin doanh nghiệp) mô tả:
   `enrichment.py` bỏ mọi trường không có nguồn, nên hồ sơ sinh ra sẽ **trống trơn** mà người dùng
   không hiểu vì sao — họ chỉ thấy "hệ thống dở", không thấy "tôi chọn nhầm model".

## 4. Quyết định

| # | Quyết định | Lý do |
|---|-----------|-------|
| **M1** | **Ô chọn của mỗi chức năng chỉ hiện model đủ năng lực cho chức năng đó**, không hiện rồi để hỏng sau | Cả hai đường hỏng ở mục 1 đều **im lặng**. Chặn lúc chọn là chỗ duy nhất báo được cho người dùng bằng thứ họ hiểu |
| **M2** | Ba khoá chức năng: **`ocr`** · **`enrich`** · **`chat`** | Đúng ba chức năng chủ dự án nêu |
| **M3** | **Việt hoá sau khi quét (`EX-02`) dùng chung khoá `ocr`** | **QĐ-5**. Nó là lượt gọi model *thứ hai bên trong* luồng quét, người dùng không nhìn thấy như một chức năng riêng — thêm ô thứ tư là thêm một ô để chọn sai. Năng lực cần cũng trùng khít: đều là JSON, và bước dịch **không** cần ảnh |
| **M4** | Danh sách cho phép **lấy từ danh mục thật lúc chạy**, lọc bằng bảng ở mục 3, **không hardcode 12 tên model trong mã** | Danh mục đã đổi 11 → 12 trong 14 ngày. Hardcode là hẹn trước một lần lệch nữa |
| **M5** | Model đã lưu mà **biến mất khỏi danh mục** → rơi về `LLM_MODEL` + cảnh báo, **không** làm hỏng lượt quét | Cùng lối đã chốt ở `EX-02`: mất bản dịch còn hơn mất tấm thẻ vừa quét |
| **M6** | **Không** lọc theo độ trễ | Chênh 1,1s ↔ 5,9s là thật, nhưng mỗi model mới đo **một lượt** — nằm trong sai số. Số liệu để tham khảo, không để chặn |

### Danh sách cho phép chọn (dẫn xuất từ mục 3)

- **`ocr` — Quét danh thiếp** *(11 model)*: mọi model **trừ** `gpt-oss-120b-medium`.
- **`enrich` — Lập hồ sơ** *(7 model)*: `gemini-3-flash`, `gemini-3.1-flash-lite`,
  `gemini-3.1-flash-image`, `gemini-3.5-flash-lite`, `gemini-3.6-flash-high`,
  `gemini-3.7-flash-high`, `gemini-3.8-flash-high`.
- **`chat` — Trợ lý AI** *(cả 12)*: chat không cần ảnh, cũng không cần tra cứu Internet —
  `prompts/assistant.py` **cấm** model dùng kiến thức ngoài Knowledge Base (quy tắc 1), nên
  model tra được Internet cũng không được phép dùng.

Model mặc định `gemini-3-flash` **đủ năng lực cho cả ba** → `NULL = dùng mặc định` của `EX-13` an toàn.

## 5. Hệ quả cho các task sau

- **`EX-13`**: bảng `user_model_prefs` có đúng ba cột `ocr_model` / `enrich_model` / `chat_model`
  (M2), nullable (M5).
- **`EX-14`**: `model_for(..., feature)` nhận đúng ba khoá của M2; bước Việt hoá đi theo `ocr` (M3);
  model lạ/biến mất → rơi về mặc định kèm log cảnh báo (M5).
- **`EX-15`**: `GET /api/integration/models` trả **ba** danh sách đã lọc, dựng lúc chạy từ
  `model_ids()` giao với bảng năng lực (M4). `PUT` từ chối model ngoài danh sách của chức năng đó.
- **`EX-16`**: phải có ca *model đã chọn biến mất khỏi danh mục* — đó là hành vi M5, và nó sẽ xảy ra
  thật vì danh mục tự đổi.

## 6. Hạn chế đã biết của phép đo

- **Mỗi model một lượt.** Web search phụ thuộc kết quả Google thay đổi theo lần gọi; một model trả
  "0 nguồn" ở lượt đo **có thể** trả nguồn ở lượt khác. Ba model Claude/pro-agent trượt với khoảng
  cách rõ (0 nguồn so với 1–5 nguồn) nên kết luận vững, nhưng **không nên** dùng bảng này để xếp
  hạng chất lượng giữa các model cùng ✅.
- **Chỉ đo một tấm thẻ** (`en-01-clear.png`, tiếng Anh, sắc nét). Nó trả lời được câu hỏi *"model
  có nhận `inline_data` không"* — **không** trả lời được *"model đọc thẻ tiếng Nhật tốt đến đâu"*.
  Việc đó là `docs/accuracy.md`, đo bằng `scripts/check_multilang_ocr.py`.
- **`claude-opus-4-6-thinking` mất 123,5s** cho lượt web — vượt `LLM_TIMEOUT = 120s`. Nó bị xếp ❌
  cho `enrich` vì không có nguồn, nhưng kể cả có thì độ trễ đó cũng không dùng được trong luồng
  enrich vốn đã chạy nền nhiều công ty.
- **Danh mục đổi theo thời gian.** Chạy lại spike khi thấy tên model lạ trong `/settings`; bảng ở
  mục 3 là ảnh chụp ngày 2026-09-24, không phải hằng số.
