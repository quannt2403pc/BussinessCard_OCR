# Plan.md — Kế hoạch dự án "Số hoá danh thiếp & Hồ sơ doanh nghiệp đối tác"

> Mã dự án: **BusinessCard_OCR** · Thời gian: **11 ngày làm việc + 4 ngày dự phòng** · Nhân sự: **2 (Quân, Tùng)**
> Sản phẩm: **demo chạy localhost (môi trường dev)**, quản lý toàn bộ bằng Docker.

---

## 1. Mục tiêu & phạm vi

### 1.1 Mục tiêu
Xây dựng hệ thống giúp doanh nghiệp chuyển hoá danh thiếp thu thập tại hội thảo/triển lãm thành **hồ sơ đối tác chuẩn hoá**, loại bỏ nút thắt nhập liệu thủ công và tra cứu phân tán.

### 1.2 Ba chức năng chính
| # | Chức năng | Mô tả |
|---|-----------|-------|
| F1 | **Số hoá danh thiếp qua ảnh** | Upload/scan ảnh danh thiếp → LLM Vision (Gemini Flash) trích xuất: công ty, họ tên người đưa danh thiếp, chức vụ, email, SĐT, địa chỉ, website, ngày upload… → lưu DB, cho phép người dùng review/sửa. |
| F2 | **Hồ sơ doanh nghiệp đối tác** | **Người dùng chủ động tích chọn 1 hoặc nhiều công ty** đã thu được từ danh thiếp rồi bấm “Tạo hồ sơ doanh nghiệp” — hệ thống **không** tự sinh. Với mỗi công ty được chọn, dùng LLM + tìm kiếm Internet tổng hợp: mã số thuế, tên pháp lý, quy mô, ngành nghề, sản phẩm/dịch vụ, địa chỉ, website, nguồn trích dẫn → sinh Hồ sơ doanh nghiệp. |
| F3 | **Trợ lý AI hỏi–đáp (RAG)** | Chat hỏi đáp trên Knowledge Base gồm **danh thiếp đã nhập liệu + hồ sơ doanh nghiệp đã tạo**, trả lời kèm trích dẫn nguồn. |

### 1.3 Trong phạm vi
- Web app (FastAPI + giao diện web đơn giản) chạy localhost qua Docker Compose.
- Upload 1 ảnh và upload hàng loạt (batch) danh thiếp; danh thiếp đã quét nằm ở một màn hình danh sách riêng.
- **Lập hồ sơ đối tác kích hoạt thủ công**: màn hình Doanh nghiệp cho tích chọn 1..n công ty rồi chạy bằng một nút, có theo dõi tiến trình.
- Danh thiếp **đa ngôn ngữ**: Anh, Việt, Hàn, Nhật, Trung.
- **Nút bấm kết nối OAuth với CLIProxy** trên giao diện + hiển thị trạng thái kết nối.
- Quản lý mã nguồn bằng Git, đóng gói toàn bộ bằng Docker.

### 1.4 Ngoài phạm vi
- Không triển khai production, **không CD** (không tự động deploy), không HTTPS/domain thật, không auto-scaling.
  *(Có **CI kiểm tra chất lượng** trên PR — lint, kiểu, test, quét secret, một head Alembic. Xem `.github/workflows/README.md`.)*
- Không làm app mobile native (dùng web + thuộc tính `capture` của trình duyệt để chụp ảnh).
- Không quản lý người dùng/phân quyền phức tạp (single-user demo).
- Không tích hợp CRM bên ngoài (chỉ export CSV/JSON).
- Chưa làm trong demo (ghi nhận cho giai đoạn sau): danh thiếp 2 mặt ghép 1 bản ghi, nhập KB từ file CSV có sẵn, dark mode / responsive mobile.

---

## 2. Kiến trúc hệ thống

### 2.1 Sơ đồ tổng thể

```mermaid
flowchart LR
    U["Người dùng<br/>(trình duyệt localhost)"] --> W["Web UI<br/>(Jinja2 + HTMX)"]
    W --> API["FastAPI Backend<br/>:8000"]

    subgraph APP["Docker Compose network"]
        API --> DB[("PostgreSQL 16<br/>+ pgvector<br/>:5432")]
        API --> ST["Volume lưu ảnh<br/>/data/uploads"]
        API --> PX["CLIProxyAPI<br/>:8317"]
        API --> EMB["embedder :8001<br/>sentence-transformers"]
    end

    PX -->|OAuth Gemini| G["Gemini Flash<br/>(vision / text / embedding)"]
    PX -->|Web Search| NET["Nguồn công khai<br/>trên Internet"]
```

### 2.2 Luồng nghiệp vụ

```mermaid
sequenceDiagram
    participant U as Người dùng
    participant API as FastAPI
    participant PX as CLIProxyAPI
    participant EMB as embedder
    participant DB as PostgreSQL

    rect rgb(238, 245, 255)
    Note over U,DB: LUỒNG 1 — Quét danh thiếp (chỉ lưu lại, KHÔNG tự sinh hồ sơ DN)
    U->>API: 1. Upload ảnh danh thiếp
    API->>PX: 2. Gemini Flash Vision + prompt trích xuất JSON
    PX-->>API: 3. JSON {name, title, company, email, phone, address...}
    API->>DB: 4. Lưu card (status = needs_review)
    U->>API: 5. Review & xác nhận
    API->>DB: 6. Lưu card (confirmed) + upsert company (mới chỉ là bản ghi tên, chưa có hồ sơ)
    API->>EMB: 7. Sinh embedding cho danh thiếp
    API->>DB: 8. Lưu vector danh thiếp vào Knowledge Base
    end

    rect rgb(240, 252, 240)
    Note over U,DB: LUỒNG 2 — Lập hồ sơ đối tác (người dùng chủ động kích hoạt)
    U->>API: 9. Màn hình Doanh nghiệp: tích chọn 1..n công ty, bấm "Tạo hồ sơ doanh nghiệp"
    API->>PX: 10. Enrich từng công ty đã chọn (LLM + tìm kiếm Google)
    PX-->>API: 11. Hồ sơ DN: MST, quy mô, ngành nghề, sản phẩm...
    API->>DB: 12. Lưu company_profile + nguồn trích dẫn
    API->>EMB: 13. Sinh embedding cho hồ sơ DN
    API->>DB: 14. Lưu vector vào Knowledge Base
    end

    U->>API: 15. Hỏi trợ lý AI
    API->>DB: 16. Vector search top-k (RAG)
    API->>PX: 17. Gemini Flash + context
    PX-->>API: 18. Câu trả lời + trích dẫn
    API-->>U: 19. Hiển thị trả lời
```

### 2.3 Thành phần Docker

| Service | Image / Build | Port | Vai trò |
|---------|---------------|------|---------|
| `api` | build từ `./backend` (python:3.12-slim) | 8000 | FastAPI: REST API + phục vụ Web UI |
| `db` | `pgvector/pgvector:pg16` | 5432 | PostgreSQL + extension `vector` |
| `cliproxy` | `eceasy/cli-proxy-api` (image công khai, **không build từ mã nguồn**) | 8317 + 51121 | Cổng OAuth + gateway tới Gemini. Cấu hình từ `cliproxy/config.example.yaml`; 51121 là callback OAuth của Antigravity |
| `embedder` | build từ `./embedder` (python:3.12-slim + sentence-transformers) | 8001 | Sinh vector embedding cho RAG — **model nhúng sẵn trong image lúc build**, chạy CPU, không cần mạng lúc chạy (xem mục 2.6) |
| `adminer` *(tuỳ chọn)* | `adminer` | 8080 | Xem DB khi debug |

Volumes: `pgdata` (DB), `uploads` (ảnh danh thiếp), `cliproxy_auths` (token OAuth — giữ lại giữa các lần restart).

### 2.4 Tích hợp CLIProxyAPI

> **Nguồn khảo sát:** `github.com/router-for-me/CLIProxyAPI` — commit `ecc9aa72` (bản đầu, Q) và
> `7fac6b15` (T kiểm lại ở task 1.9). **Không ghi đường dẫn tuyệt đối trên máy vào tài liệu**:
> mỗi người clone một chỗ, đường dẫn của người này luôn sai với người kia.
> Bản clone chỉ là tư liệu đọc mã nguồn — **không phải phụ thuộc lúc chạy**, service `cliproxy`
> dùng image công khai (mục 2.3).

| Việc | Endpoint | Ghi chú |
|------|----------|---------|
| Lấy URL đăng nhập OAuth | `GET http://cliproxy:8317/v0/management/{provider}-auth-url?is_webui=1` | Trả `{"status":"ok","url":…,"state":…}`. Route sinh động theo provider; built-in gồm `anthropic`, `codex`, `antigravity`, `kimi`, `xai`. **Luôn gửi `is_webui=1`** — nhánh dành cho luồng bấm nút trên UI (task 2.2) |
| Callback OAuth | `GET/POST /v0/management/oauth-callback` | CLIProxyAPI tự xử lý, lưu token vào thư mục `auths/` |
| **Badge "Đã kết nối / Chưa kết nối"** | `GET /v0/management/auth-files` | **Nguồn sự thật duy nhất** cho trạng thái. Mảng rỗng `{"files":[]}` = chưa kết nối |
| Poll trong lúc chờ người dùng đồng ý | `GET /v0/management/get-auth-status?state=…` | **CHỈ dùng cho việc này.** ⚠️ I-02: không truyền `state` thì trả `{"status":"ok"}` kể cả khi chưa đăng nhập bao giờ → dùng cho badge là lỗi âm thầm, badge sẽ luôn xanh. ⚠️ Báo lỗi bằng **HTTP 200** kèm `{"status":"error"}` — phải đọc body, không nhìn mã HTTP |
| Huỷ phiên OAuth | `DELETE /v0/management/oauth-session` | |
| Ngắt kết nối (xoá credential) | `DELETE /v0/management/auth-files?name=<tên file>` | **Bắt buộc `name`** (lấy từ `auth-files[].name`); thiếu là `400 invalid name`. Không có biến thể "xoá tất cả" → ngắt kết nối = liệt kê rồi xoá từng file (đo thật, task 2.2) |
| Danh mục model của channel | `GET /v0/management/model-definitions/antigravity` | Chốt tên model thật cho `LLM_MODEL`; danh mục tự cập nhật từ xa nên **không hardcode theo tài liệu** |
| Gọi model (Gemini native) | `POST /v1beta/models/<LLM_MODEL>:generateContent` | dùng cho vision + text. **Không cần management key** (`api-keys: []` = không kiểm tra client). Chưa kết nối OAuth thì trả `400 unknown provider for model …` — **trùng câu chữ với lỗi sai tên model**, phân biệt bằng cách tra danh mục channel (task 2.3) |
| Gọi model (OpenAI-compatible) | `POST /v1/chat/completions` | phương án thay thế |
| Embedding | **Không tồn tại** — đã kiểm chứng trong mã nguồn | Route `/v1beta/models/*action` chỉ nhận `generateContent`, `streamGenerateContent`, `countTokens`; cũng không có `/v1/embeddings`. Embedding do service `embedder` cục bộ đảm nhiệm — xem mục 2.6 |

> Các route `/v0/management/*` yêu cầu **management key** (khai ở `remote-management.secret-key`
> trong `config.yaml` của CLIProxyAPI, truyền vào backend qua biến `CLIPROXY_MGMT_KEY` — hai giá
> trị phải trùng nhau). Backend đóng vai trò proxy để Web UI chỉ cần bấm 1 nút.
>
> Ba ràng buộc bắt buộc, đã kiểm chứng bằng container thật (xem `cliproxy/config.example.yaml`):
> - **`allow-remote: true`** — CLIProxy hiểu "localhost" đúng nghĩa đen `127.0.0.1`/`::1`; container `api` gọi qua mạng bridge nên để `false` là nhận `403 remote management disabled` dù gửi đúng key (I-01).
> - **Publish cổng `51121`** — callback OAuth của Antigravity; thiếu thì token không bao giờ được lưu (I-04).
> - **Không retry khi 401/403** — sai key 5 lần là ban IP 30 phút, cả container `api` chung một IP (I-05).
>
> `secret-key` để rỗng = tắt hẳn Management API, mọi route trả **404** chứ không phải 401.
> *(Khoá `home` không tồn tại trong `config.example.yaml` của CLIProxy — không phải khai gì.)*

### 2.5 Nút bấm kết nối OAuth (yêu cầu bắt buộc)

Trang `/settings` gồm:
1. Badge trạng thái: **Chưa kết nối / Đã kết nối** (kèm tài khoản, danh sách model khả dụng) — đọc từ **`auth-files`**.
2. Nút **"Kết nối CLIProxy (OAuth)"** → backend gọi `…-auth-url`, nhận về `url` + `state` → mở tab mới tới URL OAuth của Google.
3. Người dùng đồng ý → CLIProxy nhận callback ở cổng `51121` → UI **poll `get-auth-status?state=<state vừa nhận>`** mỗi 2 giây cho tới khi `{"status":"ok"}` → **rồi mới gọi lại `auth-files`** để vẽ badge. ⚠️ Poll mà quên `state` thì lần nào cũng "ok" ngay lập tức (I-02).
4. Nút **"Kiểm tra kết nối"** (gọi thử 1 prompt ngắn tới Gemini Flash) và nút **"Ngắt kết nối"**.

### 2.6 Phương án embedding cho RAG

**Kết luận khảo sát mã nguồn CLIProxyAPI (commit `ecc9aa72`, T kiểm lại ở `7fac6b15`): CLIProxy KHÔNG có endpoint embedding.**
- Không tồn tại route `/v1/embeddings` (kiểu OpenAI).
- Route Gemini native `/v1beta/models/*action` chỉ nhận `generateContent`, `streamGenerateContent`, `countTokens` (`sdk/api/handlers/gemini/gemini_handlers.go:155`). Không có `embedContent`.
- Cảnh báo: `switch` này **không có nhánh `default`** → gọi `:embedContent` trả HTTP 200 rỗng chứ không phải 404, rất dễ hiểu nhầm là "gọi được nhưng parse lỗi".

Dùng Gemini embedding bằng API key trực tiếp cũng bị loại vì trái nguyên tắc "không nhúng API key trong ứng dụng" (mục 9).

→ **Embedding do một service cục bộ trong Docker Compose đảm nhiệm, tách khỏi CLIProxy.**

#### Kiến trúc

```mermaid
flowchart LR
    API["FastAPI :8000<br/>services/embeddings.py"] -->|"POST /embed"| EMB["embedder :8001<br/>FastAPI + sentence-transformers"]
    EMB --> M["Model nhúng sẵn trong image<br/>(build-time, không tải lúc chạy)"]
    API --> DB[("pgvector<br/>kb_chunks.embedding")]
```

Tách thành service riêng thay vì nạp model thẳng trong `api` vì 4 lý do:
1. `api` được rebuild liên tục suốt 11 ngày — không nên kéo theo ~2.5GB `torch` mỗi lần build.
2. Test của Q mock bằng `respx` (đã có trong stack) — mock 1 lời gọi HTTP đơn giản hơn nhiều so với monkeypatch model trong tiến trình.
3. Đổi model chỉ cần đổi 1 biến môi trường + rebuild 1 service, không đụng `api`.
4. Ranh giới sở hữu sạch: Tùng dựng `embedder/` (thư mục mới), Quân giữ `app/` và `docker-compose.yml` — không ai phải sửa file của người kia.

#### Ứng viên đánh giá (task 2.6 trong Task.md)

| Ứng viên | Chiều | Dung lượng | Ghi chú |
|----------|-------|-----------|---------|
| **`intfloat/multilingual-e5-small`** *(mặc định đề xuất)* | 384 | ~470MB | 118M tham số, chạy CPU tốt; phủ đủ Anh/Việt/Hàn/Nhật/Trung; vector 384 chiều giữ index `ivfflat` nhẹ |
| `BAAI/bge-m3` | 1024 | ~2.2GB | Chất lượng truy hồi đa ngôn ngữ cao hơn; nặng, build & load chậm — chỉ chọn nếu e5-small trượt tiêu chí |
| *(dự phòng cuối)* full-text `tsvector` | — | — | Bỏ vector search, RAG thuần từ khoá. Chỉ dùng nếu cả hai ứng viên trên đều không chạy nổi |

#### Lưu ý bắt buộc khi triển khai

- **Tiền tố của họ e5**: khi index phải thêm `passage: `, khi truy vấn phải thêm `query: `. Quên bước này thì chất lượng truy hồi tụt rõ rệt — đây là lỗi âm thầm, không báo lỗi.
- **Nhúng model vào image lúc build**, không tải từ HuggingFace lúc chạy — nếu không, máy sạch không có mạng sẽ hỏng demo (tiêu chí A1).
- Chuẩn hoá vector (L2) và dùng khoảng cách cosine, khớp với index `ivfflat` đã thiết kế.
- Chiều vector phải chốt **trước D6**; đổi model khác chiều thì cần Q sinh thêm một Alembic revision đổi kiểu cột.

#### Hợp đồng API của `embedder`

```
POST /embed   { "texts": ["..."], "kind": "passage" | "query" }
           →  { "vectors": [[...]], "dim": 384, "model": "..." }
GET  /health  → { "status": "ok", "model": "...", "dim": 384 }
```

---

## 3. Thiết kế dữ liệu (PostgreSQL)

```
business_cards
  id (uuid, pk), image_path, image_hash, uploaded_at, ocr_raw_json (jsonb),
  full_name, job_title, company_name_raw, email, phone, phone_alt, address,
  website, language_detected, confidence (jsonb),
  status (pending | needs_review | confirmed),
  company_id (fk -> companies.id, nullable), notes, created_at, updated_at

companies
  id (uuid, pk), name_normalized (unique), display_name, aliases (text[]),
  created_at, updated_at

company_profiles
  id (uuid, pk), company_id (fk), legal_name, tax_code, founded_year,
  size_label, employee_range, industry (text[]), products (text[]),
  address, website, phone, email, description, sources (jsonb),
  llm_model, generated_at, status (draft | generated | verified),
  created_at, updated_at

kb_chunks                      -- Knowledge Base cho RAG
  id (uuid, pk), source_type (card | company_profile), source_id (uuid),
  content (text), metadata (jsonb), embedding (vector(384)), created_at   -- 384 = multilingual-e5-small, chốt ở ADR (mục 2.6)

chat_sessions / chat_messages
  session_id, role (user | assistant), content, citations (jsonb), created_at

integration_status             -- cache trạng thái OAuth CLIProxy
  provider, connected (bool), account_label, last_checked_at
```

Index: `ivfflat` trên `kb_chunks.embedding` (cosine); unique trên `business_cards.image_hash` để chống upload trùng; GIN trên `company_profiles.industry`.

---

## 4. API dự kiến

| Nhóm | Endpoint | Mô tả |
|------|----------|-------|
| Integration | `GET /api/integration/status` · `POST /api/integration/connect` · `POST /api/integration/disconnect` · `POST /api/integration/test` | Nút OAuth CLIProxy |
| Cards | `POST /api/cards/upload` · `POST /api/cards/batch-upload` · `GET /api/cards` (filter/search/paging) · `GET /api/cards/{id}` · `PATCH /api/cards/{id}` · `POST /api/cards/{id}/confirm` · `DELETE /api/cards/{id}` · `GET /api/cards/export.csv` | F1 |
| Companies | `GET /api/companies` · `GET /api/companies/{id}` · `POST /api/companies/{id}/enrich` (1 công ty) · **`POST /api/companies/enrich-batch`** (nhận `company_ids[]`, trả `job_id`) · **`GET /api/companies/enrich-jobs/{job_id}`** (tiến trình từng công ty) · `PATCH /api/companies/{id}/profile` · `GET /api/companies/{id}/contacts` | F2 |
| Assistant | `POST /api/chat` (câu hỏi → trả lời + citations) · `GET /api/chat/{session_id}` · `POST /api/kb/reindex` | F3 |
| Ops | `GET /health` · `GET /api/stats` (số danh thiếp, số hồ sơ, tỉ lệ cần review) | Dashboard |

---

## 5. Phân công

### 5.1 Theo yêu cầu
| Thành viên | Mảng phụ trách |
|-----------|----------------|
| **Quân** | OCR danh thiếp (F1) — gồm cả hậu xử lý & tối ưu độ chính xác sau khi quét, Trợ lý AI/RAG (F3), Git repo, Docker & docker-compose, tích hợp OAuth CLIProxy, LLM client dùng chung |
| **Tùng** | Hồ sơ doanh nghiệp đối tác (F2): enrichment bằng LLM + tìm kiếm Internet, chuẩn hoá/dedupe công ty, UI hồ sơ DN |

### 5.2 Các việc phát sinh — tự phân công

Nguyên tắc: **mỗi việc chỉ có đúng 1 người chịu trách nhiệm**, và việc nào chạm cùng một file thì
giao cho cùng một người để tránh xung đột Git (bảng sở hữu file chi tiết ở đầu `Task.md`).

| Việc phát sinh | Giao cho | Lý do |
|----------------|----------|-------|
| Schema DB & toàn bộ Alembic migration | Quân | Chỉ 1 người sinh revision, tránh 2 head phải merge tay; Tùng review qua PR |
| Web UI khung (`base.html`, nav, static) | Quân | Gắn liền với FastAPI skeleton, là file dùng chung |
| Web UI danh sách/chi tiết/review danh thiếp | Quân | Thuộc F1, cùng thư mục `templates/cards/` |
| Web UI hồ sơ doanh nghiệp + dashboard | Tùng | Thuộc F2, cùng thư mục `templates/companies/` |
| `services/llm.py` (client Gemini dùng chung) | Quân | File dùng chung; Quân cần vision + streaming, Tùng chỉ import |
| `services/normalize.py` (chuẩn hoá SĐT/email — hậu xử lý sau khi quét) | Quân | Thuộc F1: tối ưu dữ liệu ngay sau bước OCR, gọi trong `ocr.py` |
| `services/normalize_company.py` (chuẩn hoá tên công ty) | Tùng | Tách thành file riêng để giữ quy ước 1 chủ sở hữu/file; chỉ phục vụ dedupe F2 |
| Bộ dữ liệu test (≥30 danh thiếp đa ngôn ngữ) | Tùng | Làm song song khi Quân dựng pipeline |
| Prompt OCR, đo & tinh chỉnh độ chính xác (`docs/accuracy.md`) | Quân | Thuộc F1: trọn vòng lặp tối ưu sau khi quét; bộ ảnh mẫu + `expected.json` do Tùng chuẩn bị |
| Prompt enrichment + chống bịa thông tin | Tùng | Thuộc F2 |
| Đánh giá & chốt model embedding, dựng service `embedder` | Tùng | Cùng một người từ chọn model đến dựng service — tránh bàn giao nửa vời; `embedder/` là thư mục mới, không đụng file nào của Quân |
| `services/embeddings.py` (client gọi `embedder`) | Quân | Thuộc F3, nằm trong `app/`; gọi qua hợp đồng HTTP đã chốt ở mục 2.6 |
| Khối service `embedder` trong `docker-compose.yml` | Quân | Quân sở hữu compose, khai báo sẵn từ D1 để Tùng không phải sửa file chung |
| Ingest KB (chunk + embedding) | Quân | Thuộc F3; Tùng chỉ gọi hàm `kb.ingest_company_profile()` |
| Export CSV/JSON | Tùng | Đặt ở `routers/export.py` riêng để không đụng `cards.py` của Quân |
| Hạ tầng test (`conftest.py`) + test F1/F3 | Quân | |
| Test F2 (chuẩn hoá tên công ty, matching, enrichment, API companies) | Tùng | Mỗi người test phần mình, file test tách riêng |
| Nhật ký bug | Tách 2 file: Quân giữ `bugs-f1-f3.md`, Tùng giữ `bugs-f2.md` | Tránh 2 người sửa cùng 1 file trong ngày kiểm thử |
| README + hướng dẫn OAuth | Quân | |
| Tài liệu HDSD, kịch bản demo, slide | Tùng | |
| Video demo dự phòng | Quân | |

### 5.3 Quy ước làm việc
- Git flow đơn giản: `main` (ổn định) ← PR từ `feat/<module>-<việc>`; commit theo Conventional Commits.
- **PR phải xanh CI mới được merge** (`main` bật branch protection, required check = `CI xanh`). Chạy trước ở máy cho đỡ mất lượt: `ruff check . && ruff format --check . && mypy app embedder scripts && pytest`.
- **Mỗi task 1 owner duy nhất; không sửa file thuộc quyền sở hữu của người kia** — cần đổi thì báo chủ file. Bảng sở hữu file/module nằm ở đầu `Task.md`.
- **Không xếp hai người vào cùng một file trong cùng một ngày**; bất khả kháng thì làm tuần tự, người sau rebase trước khi sửa.
- `app/main.py` và `routers/__init__.py` khai báo sẵn stub toàn bộ router từ D1 → về sau không ai phải sửa file chung khi thêm tính năng.
- Daily sync 15 phút đầu ngày: hôm qua / hôm nay / vướng gì; ai cần chạm file của ai thì báo tại đây.
- Mọi thay đổi schema đều qua Alembic migration do Quân tạo, không sửa tay DB.
- Định nghĩa "Xong" (DoD): code chạy được trong Docker + có test hoặc kịch bản thử tay + đã merge vào `main`.

---

## 6. Rủi ro & phương án dự phòng

| # | Rủi ro | Ảnh hưởng | Phương án xử lý |
|---|--------|-----------|-----------------|
| R1 | OAuth CLIProxy lỗi / token hết hạn giữa buổi demo | Cao | Nút "Kiểm tra kết nối"; giữ token trong volume `cliproxy_auths`; dự phòng cấu hình Gemini API key trực tiếp trong CLIProxy |
| R2 | ~~CLIProxy không hỗ trợ endpoint embedding~~ — **đã xác nhận là KHÔNG hỗ trợ** (kiểm chứng mã nguồn, mục 2.6) | Đã xử lý | Không còn là rủi ro mà là quyết định thiết kế: dùng service `embedder` cục bộ (`multilingual-e5-small`). Rủi ro còn lại: model chọn sai chất lượng → task 2.6 có tiêu chí đạt/trượt và ứng viên thay thế `bge-m3`; dự phòng cuối là RAG bằng `tsvector` |
| R3 | OCR sai với danh thiếp Hàn/Nhật/Trung hoặc ảnh mờ | Cao | Bắt buộc bước người dùng review trước khi confirm; tiền xử lý ảnh (resize, tăng tương phản); prompt yêu cầu trả `confidence` từng trường |
| R4 | LLM bịa thông tin doanh nghiệp (mã số thuế sai) | Cao | Bắt buộc trả `sources` (URL) cho mỗi trường; trường không có nguồn → `null` + nhãn "chưa xác minh"; UI hiển thị rõ nguồn |
| R5 | Rate limit / chậm khi upload hàng loạt | Trung bình | Xử lý nền bằng `BackgroundTasks` + hàng đợi đơn giản trong DB, giới hạn đồng thời, retry có backoff |
| R6 | Trùng công ty do viết khác nhau ("FPT Software" vs "Cty FPT Software") | Trung bình | Chuẩn hoá tên (bỏ hậu tố pháp lý, lowercase, bỏ dấu) + so khớp mờ; cho phép gộp thủ công |
| R7 | Chậm tiến độ do tích hợp | Trung bình | Ưu tiên MUST trước, cắt SHOULD/COULD nếu cần. 4 ngày dự phòng (D12–D15) **không được lên lịch trước công việc nào** — chỉ dùng khi thực tế phát sinh (xem Task.md, mục D12–D15) |

**Ưu tiên khi phải cắt phạm vi:**
- **MUST:** F1 + F2 + F3 luồng cơ bản, nút OAuth, Docker Compose.
- **SHOULD:** batch upload, export CSV, dashboard thống kê.
- **COULD:** gộp công ty thủ công, chat streaming, gợi ý câu hỏi mẫu.

---

## 7. Lộ trình tổng thể (11 ngày + 4 dự phòng)

| Giai đoạn | Ngày | Nội dung | Kết quả bàn giao |
|-----------|------|----------|------------------|
| **P0 — Khởi động** | D1 | Chốt yêu cầu, thiết kế DB & API, dựng repo + Docker skeleton | `docker compose up` ra `/health`, ERD, API spec |
| **P1 — Nền tảng AI** | D2 | Tích hợp CLIProxy: OAuth + LLM client + nút kết nối trên UI | Bấm nút → OAuth thành công → gọi thử Gemini Flash OK |
| **P2 — F1 OCR** | D3–D4 | Upload ảnh → trích xuất → lưu DB → UI review/sửa/danh sách | Số hoá được danh thiếp đầu–cuối |
| **P3 — F2 Hồ sơ DN** | D5–D6 | Enrichment công ty bằng LLM + web search, dedupe, UI hồ sơ | Từ 1 danh thiếp sinh hồ sơ DN có trích dẫn nguồn |
| **P4 — F3 RAG** | D7–D8 | Ingest KB, vector search, API + UI chat có trích dẫn | Hỏi "Công ty X làm gì?" → trả lời đúng kèm nguồn |
| **P5 — Hoàn thiện** | D9 | Batch upload, đa ngôn ngữ, export, dashboard, gom Docker | Compose 1 lệnh chạy toàn hệ thống |
| **P6 — Kiểm thử** | D10 | Test đầu–cuối, sửa lỗi, dữ liệu mẫu, đo độ chính xác | Báo cáo test, danh sách bug đã đóng |
| **P7 — Bàn giao** | D11 | README, tài liệu, kịch bản + tổng duyệt demo | Bản demo hoàn chỉnh + tài liệu |
| **Dự phòng** | D12–D15 | **Không đặt trước công việc nào.** Chỉ dùng cho: kiểm thử hồi quy, sửa bug phát sinh, hoặc chức năng mới phát sinh sau khi kế hoạch đã chốt | Bản ổn định cuối cùng |

Chi tiết công việc từng ngày: xem **[Task.md](./Task.md)**.

---

## 8. Tiêu chí nghiệm thu

| # | Tiêu chí | Cách đo |
|---|----------|---------|
| A1 | `docker compose up -d` khởi động toàn bộ hệ thống, truy cập `http://localhost:8000` | Máy sạch, chạy 1 lệnh |
| A2 | Có nút kết nối OAuth CLIProxy, bấm là kết nối được và hiển thị đúng trạng thái | Thao tác tay |
| A3 | Upload ảnh danh thiếp → trích xuất đủ 7 trường bắt buộc (tên, chức vụ, công ty, email, SĐT, địa chỉ, ngày upload) | 30 ảnh mẫu, độ chính xác trường ≥ 85% với ảnh rõ nét |
| A4 | Hỗ trợ danh thiếp Anh/Việt + ít nhất 1 trong Hàn/Nhật/Trung | Bộ ảnh test đa ngôn ngữ |
| A5 | Sinh hồ sơ doanh nghiệp với ≥ 5 trường (MST, quy mô, ngành nghề, sản phẩm, địa chỉ) kèm nguồn | 10 công ty mẫu |
| A5b | Xác nhận danh thiếp xong mà chưa bấm nút thì **không** hồ sơ nào được sinh; vào màn hình Doanh nghiệp tích chọn 3 công ty, bấm 1 nút “Tạo hồ sơ doanh nghiệp” thì cả 3 chạy nền, tiến trình hiện đúng, xong xem được cả 3 hồ sơ | Thao tác tay |
| A6 | Trợ lý AI trả lời đúng ≥ 8/10 câu hỏi mẫu về danh thiếp & doanh nghiệp trong KB, có trích dẫn | Bộ câu hỏi kiểm thử |
| A7 | Toàn bộ mã nguồn trên Git, có README hướng dẫn cài đặt & chạy | Review repo |

---

## 9. Công nghệ

- **Backend:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0, Alembic, httpx, Pillow.
- **Database:** PostgreSQL 16 + pgvector.
- **AI (sinh nội dung):** Gemini Flash (vision + text) **qua CLIProxyAPI bằng OAuth** — không nhúng API key trong ứng dụng.
- **AI (embedding):** `sentence-transformers` chạy cục bộ trong service `embedder` — CLIProxy không có endpoint embedding (mục 2.6).
- **Frontend:** Jinja2 + HTMX + TailwindCSS (CDN) — đủ cho demo, không cần build step.
- **Hạ tầng:** Docker, Docker Compose.
- **CI:** GitHub Actions — ruff (lint + format), mypy, pytest trên pgvector thật, gitleaks, kiểm tra một head Alembic.
- **Test:** pytest, pytest-asyncio, respx (mock HTTP).
