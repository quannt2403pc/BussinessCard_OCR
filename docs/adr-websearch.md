# ADR — Tìm kiếm Internet qua CLIProxy cho enrichment (F2)

> Chủ sở hữu: **T** · Task **2.7** · Mở: 2026-09-10 · **Chốt: 2026-09-21**
> Trạng thái: **✅ ĐÃ CHỐT** — đã chạy thật trên 3 mốc (2026-09-11, 09-14, 09-21), đang dùng trong
> `services/enrichment.py` (4.7, 4.8) và đạt tiêu chí A5 (`docs/profile-quality.md`, 10.9)
> Script: [`scripts/spike_websearch.py`](../scripts/spike_websearch.py) · [`scripts/spike_profile_quality.py`](../scripts/spike_profile_quality.py)

---

## 1. Quyết định

| # | Quyết định | Lý do (số đo ở mục 2) |
|---|------------|------------------------|
| **Q1** | Bật tìm kiếm bằng tool **`{"googleSearch": {}}`** trên endpoint Gemini gốc `POST /v1beta/models/<LLM_MODEL>:generateContent`, gọi qua `llm.generate_content()` để nhận JSON thô | Cần đọc `candidates[0].groundingMetadata.groundingChunks[].web` — `generate_text()` chỉ trả chữ |
| **Q2** | **Gọi hai lượt.** Lượt 1: bật `googleSearch`, prompt tra cứu **văn xuôi**, **không** `systemInstruction`, **không** đặc tả JSON. Lượt 2: **không** bật tool, chỉ xếp kết quả lượt 1 vào JSON và chỉ được trích URL có trong danh sách grounding | Có `systemInstruction` hoặc đặc tả JSON dài thì response **mất hẳn `groundingMetadata`** (5/5 lần, 2026-09-14) — model vẫn tra cứu nhưng không trả nguồn nào |
| **Q3** | `web.uri` là link redirect của Vertex → **theo redirect đúng 1 lần** (timeout 5 s), lưu URL gốc vào `company_profiles.sources`, giữ `web.title` làm nhãn tên miền. Không theo được thì **bỏ nguồn đó**, không hỏng cả hồ sơ | Link redirect trả `302` ra trang gốc thật nhưng có thể hết hạn; nguồn lưu phải bấm được lâu dài |
| **Q4** | Một trường chỉ được giữ khi có nguồn **thuộc tên miền trong danh sách grounding**; không có thì để `null` (luật 4.8) | Chống bịa (R4): URL model tự gõ ra ngoài grounding không được tin |
| **Q5** | **Không chọn model theo cờ `supports_web_search`.** Chọn bằng một lời gọi thật (`spike_websearch.py --model …` hoặc `spike_profile_quality.py`) | Cờ cho kết quả **khác nhau theo tài khoản và thời điểm** — âm tính giả 11/11 ngày 09-11, khớp thực tế ngày 09-21 |
| **Q6** | Model: **`gemini-3-flash`** là mặc định của dự án; **`gemini-3.6-flash-high`** cũng đã kiểm, dùng được | Cả hai đạt A5, 10/10 MST khớp trang nguồn (10.9) |

---

## 2. Số đo

### 2026-09-11 — Q chạy `spike_websearch.py` ngay sau khi OAuth xong (ghi ở **I-12**)

| Câu hỏi của ADR | Kết quả |
|-----------------|---------|
| Model nào có `supports_web_search`? | Cờ báo **`không` cho cả 11/11 model** — kể cả model thực tế tra cứu được → cờ cho **âm tính giả** |
| Gọi `tools: [{"googleSearch": {}}]` có chạy? | **Có** trên `gemini-3-flash`: `HTTP 200`, 3 `groundingChunks` (`masothue.com`, `vnr500.vn`, `vsd.vn`) cho câu hỏi MST của FPT |
| `web.uri` có phải URL thật? | **Không** — là link redirect Vertex, **nhưng sống**: gọi thẳng trả `302` + `Location` là trang gốc (`fptsoftware.com/en`…), theo tiếp ra `200`; `web.title` chứa đúng tên miền |

### 2026-09-14 — T chạy enrichment thật lần đầu (4.7)

- Gọi **một lượt** (có `systemInstruction` / đặc tả JSON) → `groundingMetadata` **biến mất 5/5 lần** → 4.8 loại sạch
  → hồ sơ trắng. Bỏ đặc tả ra thì 3/3 lần còn 2–5 nguồn. → Quyết định **Q2**.
- Sau khi tách hai lượt, `gemini-3-flash`: FPT Software **10**, Vinamilk **11**, Hòa Phát **11** trường có nguồn,
  15–22 s/công ty; **21/22 URL nguồn mở ra `200`** (còn lại `topcv.vn` trả `403` vì chặn bot).
- Đã thử và bỏ hướng "tin link redirect do model tự chèn vào văn bản": link thật trả `302`, link sửa/bịa trả `404`
  nên chống giả được, nhưng model không phải lần nào cũng chèn (2/3 công ty ra URL thường).

### 2026-09-21 — T đo 10 công ty thật + 1 công ty bịa, 2 model (10.9)

| | `gemini-3-flash` | `gemini-3.6-flash-high` |
|---|---|---|
| ≥ 5 trường có nguồn | 10/10 | 10/10 |
| Đủ 5 trường A5 | 9/10 | 8/10 |
| **MST tìm thấy đúng trong trang nguồn đã trích** | **10/10** | **10/10** |
| Công ty bịa tên | 0 trường | 0 trường |
| Trung vị | 43 s | 32 s |

Cùng ngày, `spike_websearch.py --list-models` với tài khoản đang dùng: cờ `supports_web_search` báo **CÓ cho mọi model
Gemini** (9 model) và **không** cho `claude-*`, `gpt-oss-*` — lần này **khớp** hành vi thật, **ngược** với số đo
09-11. Cờ phụ thuộc tài khoản / thời điểm CLIProxy nạp `fetchAvailableModels`, nên không dùng làm căn cứ (**Q5**).

---

## 3. Hệ quả và rủi ro còn lại

| Rủi ro | Mức | Ai cần biết |
|--------|-----|-------------|
| **Grounding rất nhạy với prompt.** Sửa prompt lượt 1 (ví dụ thêm hướng dẫn "ưu tiên cổng đăng ký kinh doanh" ở 9.7) có thể làm mất nguồn mà không có lỗi nào báo | Cao | T (9.7): mọi thay đổi prompt phải chạy lại `spike_profile_quality.py` trước khi merge |
| `generate_text(..., system=…, tools=[TOOL_GOOGLE_SEARCH])` sẽ **không có nguồn** | Trung bình | Q: nếu F3 muốn tra cứu Internet thì phải theo **Q2** |
| Kết quả tìm kiếm thay đổi theo ngày — một công ty 5/5 hôm nay có thể 4/5 lần sau | Thấp | Bình thường; A5 đo trên 10 công ty, không trên một công ty |
| Một số trang chặn bot (`topcv.vn` → `403`) | Thấp | Nguồn vẫn hợp lệ với người dùng bấm từ trình duyệt; chỉ ảnh hưởng việc kiểm tự động |
| Link redirect Vertex hết hạn | Đã xử lý | **Q3** lưu URL gốc ngay lúc tạo hồ sơ |
| Tài khoản Google Workspace không lấy được `project_id` → mọi lời gọi `400` dù badge *Đã kết nối* | Trung bình | Người dùng: đăng nhập Gmail cá nhân (`docs/user-guide.md` mục 8) |

---

## 4. Ảnh hưởng tới các task khác

| Task | Người | Theo quyết định nào | Trạng thái |
|------|-------|---------------------|------------|
| 2.3 `services/llm.py` | Q | `TOOL_GOOGLE_SEARCH`, `generate_content()` trả JSON thô | ✅ |
| 4.7 `services/enrichment.py` | T | Q1, Q2, Q3 | ✅ |
| 4.8 Lọc nguồn | T | Q4 | ✅ |
| 6.6 Khối *Nguồn tham khảo* | T | Q3 — hiện URL gốc, nhãn là tên miền | ✅ |
| 9.7 Nâng chất lượng hồ sơ | T | Rủi ro mục 3 dòng đầu | ⬜ |
| 10.9 Đo chất lượng hồ sơ | T | Q5, Q6 | ✅ |

---

## Phụ lục — đã kiểm trong mã nguồn CLIProxyAPI (commit `7fac6b15`, 2026-09-10)

Phần này viết **trước** khi gọi được thật; cả ba điểm đều đã được số đo ở mục 2 xác nhận.

| Điều | Bằng chứng |
|------|-----------|
| Tên tool là **`googleSearch`** (camelCase) | `internal/runtime/executor/antigravity_executor.go:829` — `tool.Get("googleSearch").Exists()` |
| Kết quả ở `groundingMetadata.groundingChunks`, có thể nằm dưới `response.candidates.0` hoặc `candidates.0` | `internal/runtime/executor/helps/antigravity_grounding_urls.go:71-74` |
| Mỗi chunk có `web.uri` + `web.title` | `internal/translator/antigravity/claude/web_search.go:267+` |
| `supports_web_search` không có trong `models.json` tĩnh, được nạp lúc chạy từ `fetchAvailableModels.webSearchModelIds` | `internal/registry/model_registry.go:65-67` |

Cách gọi đã dùng:

```jsonc
POST /v1beta/models/<LLM_MODEL>:generateContent
{
  "contents": [{"role": "user", "parts": [{"text": "<prompt tra cứu dạng văn xuôi>"}]}],
  "tools": [{"googleSearch": {}}]
}
```
