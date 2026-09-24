# API spec — bản chốt ban đầu

> Chủ sở hữu: **T** · Task **1.8** · Ngày: **2026-09-10**
> ⚠️ **Sau D1, nguồn tài liệu API chính là Swagger tự sinh tại `/docs`** (quy ước số 9, Task.md).
> File này là bản chốt ban đầu để hai người thống nhất trước khi code — **không cập nhật tay**
> theo từng thay đổi, tránh hai người sửa cùng một file.

Base URL: `http://localhost:8000` · Tất cả body là JSON UTF-8 (trừ upload dùng `multipart/form-data`).

---

## 0. Quy ước chung

### Mã lỗi

| HTTP | Khi nào | Body |
|------|---------|------|
| 400 | Tham số sai, file sai định dạng | `{"detail": "<mô tả>"}` |
| 404 | Không tìm thấy bản ghi | `{"detail": "not found"}` |
| 409 | Xung đột — upload trùng ảnh, enrich đang chạy dở | `{"detail": "<mô tả>", "existing_id": "<uuid>"}` |
| 413 | File vượt `MAX_UPLOAD_MB` (mặc định 10MB) | `{"detail": "file too large"}` |
| 422 | Pydantic validation lỗi | Chuẩn FastAPI |
| 502 | LLM/CLIProxy lỗi hoặc trả JSON hỏng | `{"detail": "<mô tả>"}` |
| 503 | Chưa kết nối OAuth CLIProxy | `{"detail": "chưa kết nối CLIProxy"}` |

### Phân trang

Endpoint danh sách nhận `?page=1&size=20` (size tối đa 100), trả:

```json
{ "items": [...], "total": 137, "page": 1, "size": 20 }
```

### Kiểu dùng lại

```jsonc
// SourceRef — nguồn trích dẫn cho một trường hồ sơ DN (chống bịa, rủi ro R4)
{ "url": "https://...", "title": "Tên trang", "retrieved_at": "2026-09-15T10:00:00Z" }

// Citation — trích dẫn trong câu trả lời của trợ lý AI
{ "source_type": "card" | "company_profile", "source_id": "<uuid>",
  "snippet": "đoạn văn bản gốc", "score": 0.83 }
```

---

## 1. Ops

### `GET /health`
Healthcheck của container. → `200 {"status":"ok"}`

### `GET /api/stats` *(T, task 7.7)*

```json
{
  "total_cards": 137, "confirmed_cards": 120, "needs_review_cards": 17,
  "total_companies": 42, "total_profiles": 18, "review_rate": 0.124
}
```

---

## 2. Integration — nút OAuth CLIProxy *(Q, task 2.4)*

> Hợp đồng dưới đây đã tính tới các phát hiện ở `docs/cliproxy-notes.md`:
> badge trạng thái đọc từ `auth-files`, **không** đọc từ `get-auth-status`.

### `GET /api/integration/status`

```json
{ "connected": true, "provider": "antigravity",
  "account_label": "user@gmail.com",
  "models": ["gemini-3-flash", "..."],
  "last_checked_at": "2026-09-11T09:00:00Z" }
```

### `POST /api/integration/connect`
Backend gọi `GET /v0/management/antigravity-auth-url` rồi trả URL cho UI mở tab mới.

```json
{ "auth_url": "https://accounts.google.com/o/oauth2/...", "state": "<state>" }
```

### `GET /api/integration/poll?state=<state>`
UI poll 2 giây/lần trong lúc chờ người dùng đồng ý.
→ `{"status": "wait" | "ok" | "error", "error": "<mô tả nếu có>"}`

### `POST /api/integration/test`
Gọi thử một prompt ngắn tới model. → `{ "ok": true, "model": "gemini-3-flash", "reply": "...", "latency_ms": 820 }`

### `POST /api/integration/disconnect`
→ `{ "connected": false }`

### `GET /api/integration/models` *(EX-15)*
Model của **chính người đang đăng nhập** cho từng chức năng, kèm danh sách chọn được.
```json
{ "default_model": "gemini-3-flash", "reachable": true,
  "features": [
    { "key": "ocr", "label": "Quét danh thiếp", "hint": "…",
      "selected": null, "selected_available": true,
      "available": ["gemini-3-flash", "…"] }
  ] }
```
`selected: null` = **dùng `LLM_MODEL`**, không phải chưa chọn. `available` là danh mục thật của
channel đã lọc theo năng lực **đo được** của chức năng đó (`docs/adr-model-per-feature.md`):
`ocr` 11 model · `enrich` 7 · `chat` 12.

### `PUT /api/integration/models` *(EX-15)*
Body `{"ocr": "gemini-3.6-flash-high"}` — **chức năng không nhắc tới thì giữ nguyên**, gửi `null`
là về mặc định. Trả về đúng hình dạng của `GET`.

| Mã | Khi nào |
|----|---------|
| `422` | Model không đủ năng lực cho chức năng đó (không đọc được ảnh / không tra cứu được Internet) — **không lưu gì cả** |
| `503` | Chưa lấy được danh mục từ CLIProxy → không có gì để đối chiếu, nên không lưu |

---

## 3. Cards — F1 *(Q)*

### `POST /api/cards/upload` *(task 3.1)*
`multipart/form-data`, field `file`. Chấp nhận `image/jpeg`, `image/png`, `image/webp`.

→ `201` trả `CardDetail` với `status: "needs_review"`.
→ `409` nếu `image_hash` đã tồn tại, kèm `existing_id`.

### `POST /api/cards/batch-upload` *(task 5.2, ưu tiên S)*
`multipart/form-data`, field `files[]`. Xử lý nền.
→ `202 { "job_id": "<uuid>", "total": 12 }`

### `GET /api/cards/batch-jobs/{job_id}`

```json
{ "job_id": "...", "total": 12, "done": 7, "failed": 1,
  "items": [{ "filename": "a.jpg", "status": "done", "card_id": "<uuid>" },
            { "filename": "b.jpg", "status": "error", "error": "ảnh mờ, LLM trả JSON hỏng" }] }
```

### `GET /api/cards` *(task 4.1)*
Query: `page`, `size`, `q` (tìm trong tên/công ty/email), `status`, `company_id`, `language`.
→ `200` danh sách `CardListItem` đã phân trang.

### `GET /api/cards/{id}` → `CardDetail`

### `PATCH /api/cards/{id}` *(task 4.2)*
Body: bất kỳ trường nào trong nhóm "Kết quả OCR" + `notes`. → `200 CardDetail`

### `POST /api/cards/{id}/confirm` *(task 4.3)*
Chuyển `confirmed`, gọi `company_matching.upsert_company()` gắn `company_id`, ingest vào KB.
**Không kích hoạt enrich** — xem `docs/scope.md` mục 4.
→ `200 { "id": "...", "status": "confirmed", "company_id": "<uuid>" }`

### `DELETE /api/cards/{id}` → `204`

### Schema

```jsonc
// CardListItem
{ "id": "<uuid>", "full_name": "Nguyễn Văn A", "job_title": "Giám đốc",
  "company_name_raw": "Công ty TNHH ABC", "company_id": "<uuid|null>",
  "email": "a@abc.vn", "phone": "+84901234567",
  "status": "needs_review", "language_detected": "vi",
  "uploaded_at": "2026-09-15T08:30:00Z" }

// CardDetail = CardListItem +:
{ "image_path": "/data/uploads/....jpg", "address": "...", "website": "https://abc.vn",
  "phone_alt": "+842838220000", "notes": null,
  "confidence": { "full_name": 0.97, "email": 0.62 },
  "ocr_raw_json": { }, "created_at": "...", "updated_at": "..." }
```

---

## 4. Companies — F2 *(T)*

### `GET /api/companies` *(task 5.5)*
Query: `page`, `size`, `q`, `has_profile` (bool — bộ lọc "chưa có hồ sơ" ở màn hình 6.5).

```jsonc
// CompanyListItem
{ "id": "<uuid>", "display_name": "Công ty TNHH ABC", "name_normalized": "abc",
  "contact_count": 3, "profile_status": null | "draft" | "generated" | "verified",
  "profile_generated_at": "2026-09-16T10:00:00Z" }
```

### `GET /api/companies/{id}`
→ `CompanyListItem` + `profile: CompanyProfile | null` + `contacts: CardListItem[]`

### `POST /api/companies/{id}/enrich` *(task 5.4)*
Enrich 1 công ty, chạy nền. → `202 { "job_id": "<uuid>" }`
→ `409` nếu công ty đó đang có job chạy dở.

### `POST /api/companies/enrich-batch` *(task 5.8)*
Body: `{ "company_ids": ["<uuid>", "..."] }` → `202 { "job_id": "<uuid>", "accepted": 3, "skipped": 1 }`
`skipped` = số công ty bị bỏ qua vì đang chạy dở.

### `GET /api/companies/enrich-jobs/{job_id}` *(task 5.8, UI poll ở 7.10)*

```json
{ "job_id": "...", "total": 3, "done": 2, "failed": 0,
  "items": [
    { "company_id": "<uuid>", "display_name": "ABC", "status": "done" },
    { "company_id": "<uuid>", "display_name": "XYZ", "status": "running" },
    { "company_id": "<uuid>", "display_name": "KFT", "status": "error",
      "error": "không tìm thấy thông tin công khai" }
  ] }
```

`status` mỗi dòng: `pending` | `running` | `done` | `error`.

### `PATCH /api/companies/{id}/profile` *(task 6.8)*
Sửa tay, đánh dấu `verified`. → `200 CompanyProfile`

### `GET /api/companies/{id}/contacts` → danh sách `CardListItem`

### Schema `CompanyProfile`

```jsonc
{
  "id": "<uuid>", "company_id": "<uuid>",
  "legal_name": "Công ty TNHH ABC Việt Nam", "tax_code": "0301234567",
  "founded_year": 2005, "size_label": "SME", "employee_range": "50-200",
  "industry": ["Logistics", "Kho vận"], "products": ["Vận tải đường bộ"],
  "address": "...", "website": "https://abc.vn", "phone": "+842838220000",
  "email": "info@abc.vn", "description": "...",
  // Trường KHÔNG có nguồn phải là null (rủi ro R4) — xem docs/scope.md mục 3
  "sources": { "tax_code": [ { "url": "...", "title": "...", "retrieved_at": "..." } ] },
  "unverified_fields": ["founded_year"],
  "llm_model": "gemini-3-flash", "generated_at": "...", "status": "generated"
}
```

---

## 5. Assistant — F3 *(Q)*

### `POST /api/chat` *(task 8.2 — hợp đồng chốt ở họp D2)*

Request: `{ "question": "Công ty nào làm logistics?", "session_id": "<uuid|null>",
"filters": { "source_type": "card" | "company_profile" | null, "company_id": null } }`

```json
{ "session_id": "<uuid>", "answer": "Có 2 công ty...",
  "citations": [ { "source_type": "company_profile", "source_id": "<uuid>",
                   "snippet": "...", "score": 0.83 } ] }
```

Không tìm được ngữ cảnh → `answer` nói rõ không biết, `citations` rỗng. **Không bịa.**

### `GET /api/chat/{session_id}`
→ `{ "session_id": "...", "title": "...", "messages": [ { "role": "user", "content": "...", "citations": null, "created_at": "..." } ] }`

### `POST /api/kb/reindex` *(task 6.4)*
→ `202 { "job_id": "<uuid>", "cards": 120, "profiles": 18 }`

---

## 6. Export *(T, task 7.6)*

| Endpoint | Trả về |
|----------|--------|
| `GET /api/export/cards.csv?status=confirmed` | `text/csv` |
| `GET /api/export/cards.json` | `application/json` |
| `GET /api/export/companies.csv` | `text/csv` — hồ sơ DN, cột `sources` là JSON string |
| `GET /api/export/companies.json` | `application/json` |

CSV encode UTF-8 **có BOM** để Excel tiếng Việt/CJK không vỡ chữ.

---

## 7. Trang giao diện (không phải API)

| Đường dẫn | Template | Chủ | Task |
|-----------|----------|-----|------|
| `/` | `home.html` | Q *(khung)* + T *(nội dung)* | 1.3, 14.6, 14.7 |
| `/cards`, `/cards/upload`, `/cards/{id}`, `/cards/batch` | `templates/cards/` | Q | 4.4, 4.5, 5.1, 5.3 |
| `/companies`, `/companies/{id}` | `templates/companies/` | T | 6.5, 6.6 |
| `/assistant` | *(đã gỡ ở EX-09)* — `301` về `/`; `?session=<uuid>` thành `/?chat=<uuid>` | Q | 8.4, EX-09 |
| `/dashboard` | *(đã gỡ ở 14.6 theo **QĐ-2**)* — `301` về `/`, `dashboard.html` đã xoá | T | 7.7, 14.6 |
| `/settings` | `settings.html` | Q | 2.5 |

---

## 8. Ba hợp đồng nội bộ giữa Q và T

Không phải HTTP API nhưng phải chốt cùng nhau (họp đầu D2):

| Bên gọi | Bên cung cấp | Chữ ký dự kiến |
|---------|--------------|----------------|
| `cards.confirm` (Q) | `company_matching.upsert_company()` (T) | `async def upsert_company(db, raw_name: str, *, email: str \| None = None, website: str \| None = None) -> uuid.UUID` — chỉ `flush`, người gọi commit (chốt ở 3.8) |
| `enrichment` (T) | `kb.ingest_company_profile()` (Q) | `async def ingest_company_profile(db, profile_id: uuid.UUID) -> int` |
| `services/embeddings.py` (Q) | service `embedder` (T) | `POST /embed {"texts": [...], "kind": "passage"\|"query"}` → `{"vectors": [[...]], "dim": 384, "model": "..."}` |
