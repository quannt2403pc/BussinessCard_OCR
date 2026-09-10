# ADR — Tìm kiếm Internet qua CLIProxy cho enrichment (F2)

> Chủ sở hữu: **T** · Task **2.7** · Ngày: **2026-09-10**
> Trạng thái: **⏸️ CHƯA CHỐT — bị chặn bởi task 2.1 của Q**
> Script: [`scripts/spike_websearch.py`](../scripts/spike_websearch.py) *(đã viết, chưa chạy lần nào)*

---

## 1. Vì sao chưa chốt được

Task 2.7 đòi "chốt cách gọi" tìm kiếm Internet. Muốn chốt thì phải **gọi thật** một lần và
nhìn kết quả trả về. Hiện chưa gọi được:

| Điều kiện | Trạng thái |
|---|---|
| Service `cliproxy` trong `docker-compose.yml` | ❌ Chưa có — task **2.1** của **Q** |
| OAuth đã kết nối | ❌ Chưa — task **2.5** của **Q** |
| Danh sách model hỗ trợ web search | ❌ Chỉ đọc được **sau khi** OAuth xong (mục 3) |

→ Phần đo đạc dời sang thời điểm Q xong 2.1 + 2.5. ADR này ghi lại **những gì đã xác định
chắc chắn từ mã nguồn** để lúc đó chỉ việc chạy script, không phải khảo sát lại từ đầu.

---

## 2. Đã chắc chắn (đọc mã nguồn CLIProxyAPI, commit `7fac6b15`)

### 2.1 CLIProxy CÓ hỗ trợ Google Search grounding

Không phải suy đoán — executor của antigravity nhận diện tool này:

| Điều | Bằng chứng trong mã nguồn |
|---|---|
| Tên tool là **`googleSearch`** (camelCase) | `internal/runtime/executor/antigravity_executor.go:829` — `tool.Get("googleSearch").Exists()` |
| Kết quả nằm ở `groundingMetadata.groundingChunks` | `internal/runtime/executor/helps/antigravity_grounding_urls.go:71-74` |
| Có **hai** đường dẫn khả dĩ | Cùng file: `response.candidates.0.groundingMetadata.groundingChunks` **hoặc** `candidates.0.groundingMetadata.groundingChunks` — script thử cả hai |
| Mỗi chunk có `web.uri` + `web.title` | `internal/translator/antigravity/claude/web_search.go:267+` |

### 2.2 Cách gọi dự kiến

```jsonc
POST /v1beta/models/<model>:generateContent
{
  "contents": [{"role": "user", "parts": [{"text": "..."}]}],
  "tools": [{"googleSearch": {}}]
}
```

---

## 3. 🚨 Rủi ro lớn nhất: không biết model nào tra cứu được Internet

`ModelInfo.SupportsWebSearch` được khai ở `internal/registry/model_registry.go:65-67`:

```go
// SupportsWebSearch indicates this Antigravity model is listed by
// fetchAvailableModels.webSearchModelIds and can execute native googleSearch.
SupportsWebSearch bool `json:"supports_web_search,omitempty"`
```

Điểm mấu chốt: **`supports_web_search` KHÔNG có trong `models.json` tĩnh.** Đã kiểm — khoá của
mỗi model antigravity chỉ gồm `id, object, owned_by, type, display_name, name, description,
context_length, max_completion_tokens, thinking, supportedInputModalities,
supportedOutputModalities`. Trường này được CLIProxy nạp **lúc chạy** từ
`fetchAvailableModels.webSearchModelIds` của Antigravity.

→ Danh sách model tra cứu được Internet **chỉ biết sau khi OAuth thành công**.

### Vì sao điều này đáng lo cho F2

Toàn bộ thiết kế chống bịa R4 (`prompts/enrichment.py`, task 2.9) đứng trên giả định model
**tra cứu được Internet và trả về URL nguồn thật**. Nếu `gemini-3-flash` (model đề xuất ở
I-03) hoá ra không nằm trong `webSearchModelIds`, thì:

- Model sẽ trả lời bằng **kiến thức nội tại**, không có nguồn → đúng kịch bản bịa thông tin.
- `sources` rỗng → task 4.8 loại sạch mọi trường → hồ sơ trắng → **trượt tiêu chí A5**
  (≥ 5 trường có nguồn).

**Phương án nếu xảy ra:** đổi sang model khác trong `antigravity` có `supports_web_search: true`
(ưu tiên `gemini-3.1-pro-low` hoặc `gemini-pro-agent` vì bản "pro"/"agent" thường có tool).
Nếu **không model nào** hỗ trợ → phải dựng bước tìm kiếm riêng ngoài LLM, việc này lớn, phải
báo Q và cân nhắc cắt phạm vi ngay trong D2.

---

## 4. Còn phải đo — làm ngay khi Q xong 2.1 + 2.5

Chạy theo thứ tự:

```bash
python scripts/spike_websearch.py --list-models         # (1)
python scripts/spike_websearch.py --model <model đã chọn>  # (2)(3)
```

| # | Câu hỏi | Vì sao quan trọng |
|---|---|---|
| 1 | Model nào có `supports_web_search: true`? | Quyết định `LLM_MODEL` cho F2 — xem mục 3 |
| 2 | Gọi có `tools: [{"googleSearch": {}}]` thì trả 200 hay lỗi? | Xác nhận cách gọi ở mục 2.2 |
| 3 | **`groundingChunks[].web.uri` có phải URL thật không?** | Câu quan trọng nhất — xem dưới |

### Vì sao câu 3 là câu quan trọng nhất

Gemini grounding thường trả URI dạng **redirect của Vertex** (`vertexaisearch.cloud.google.com/
grounding-api-redirect/...`) chứ không phải link gốc. Nếu đúng vậy:

- URL lưu vào `company_profiles.sources` sẽ là link trung gian, **có thể hết hạn**.
- Người dùng bấm vào ô "Nguồn tham khảo" (task 6.6) có thể ra trang lỗi → hỏng đúng thứ
  thuyết phục nhất của F2: chứng minh thông tin có thật.
- Tiêu chí A5 đòi nguồn **"kiểm chứng được"** — link chết thì không kiểm chứng được.

**Phải mở thử vài URL bằng tay**, không chỉ nhìn có URL là xong. Nếu là link redirect thì cân
nhắc: theo redirect một lần rồi lưu URL cuối, hoặc yêu cầu model trích thêm tên miền gốc trong
phần text.

---

## 5. Ảnh hưởng tới các task khác

| Task | Người | Phụ thuộc gì vào ADR này |
|---|---|---|
| 4.7 `services/enrichment.py` | T | Cách bật tool + đường dẫn đọc grounding |
| 4.8 Validate nguồn | T | URL có mở được không quyết định luật lọc |
| 2.3 `services/llm.py` | Q | `generate_text()` phải cho truyền `tools` xuống |
| 9.7 Nâng chất lượng hồ sơ | T | Có chặn được domain / ưu tiên cổng đăng ký DN không |

> **Lưu ý cho Q (task 2.3):** `services/llm.py` cần cho phép truyền tham số `tools` xuống
> `generateContent`. Nếu hàm chỉ nhận mỗi prompt thì F2 không bật được tìm kiếm.
> Đây là chữ ký hàm nên chốt sớm — báo ở daily sync.
