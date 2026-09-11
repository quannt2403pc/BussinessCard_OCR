# Bảng sở hữu file & quy ước chống xung đột

> Chủ sở hữu: **T** · Task **1.10** · Ngày chốt: **2026-09-10**
> Bản rút gọn của quy ước nằm ở đầu [Task.md](../Task.md) — **khi hai bên lệch nhau, `Task.md` thắng**
> (đó là file hai người mở hằng ngày). File này giải thích *tại sao* và bổ sung quy trình xử lý
> khi cần chạm file của người kia.

Nhóm 2 người làm song song 11 ngày trên cùng một repo nhỏ. Rủi ro lớn nhất không phải là code
sai mà là **hai người sửa cùng một file rồi mất thời gian gỡ conflict**. Toàn bộ quy ước dưới
đây phục vụ đúng một mục tiêu: **mỗi file có đúng một người được ghi**.

---

## 1. Nguyên tắc gốc

| # | Nguyên tắc | Vì sao |
|---|-----------|--------|
| 1 | **Mỗi file đúng một chủ sở hữu.** Không có file nào "của cả hai" | Conflict chỉ xảy ra khi hai người ghi cùng file |
| 2 | **Mỗi task đúng một người chịu trách nhiệm.** Không có task ghi "Q + T" | Việc cần cả hai (họp, chốt thiết kế) không nằm trong bảng task |
| 3 | **Đọc/import file của người kia thoải mái. Ghi thì không.** | Ranh giới rõ ràng, dễ tự kiểm |
| 4 | **Không xếp hai người vào cùng một file trong cùng một ngày** | Bất khả kháng thì làm tuần tự, người sau `git pull --rebase` trước khi sửa |
| 5 | **Chỉ Q sinh Alembic revision** | Hai người cùng sinh → 2 head phải merge tay. CI có job bắt lỗi này |

---

## 2. Bảng sở hữu

### 2.1 Của Quân (Q)

| Vùng | File/thư mục |
|------|--------------|
| Hạ tầng & khởi động | `Dockerfile`, `docker-compose.yml`, `.env.example`, `app/main.py`, `app/core/`, `alembic/`, `scripts/seed.py`, `README.md` |
| Kết nối CLIProxy & LLM | `app/services/cliproxy_client.py`, `app/services/llm.py`, `app/routers/integration.py`, `templates/settings.html`, `docs/oauth-setup.md` |
| F1 — OCR danh thiếp | `app/services/ocr.py`, `app/services/image.py`, `app/services/normalize.py`, `app/prompts/ocr.py`, `app/routers/cards.py`, `app/repositories/card.py`, `app/models/card.py`, `app/schemas/card.py`, `templates/cards/`, `docs/accuracy.md` |
| F3 — RAG & trợ lý AI | `app/services/embeddings.py`, `app/services/kb.py`, `app/services/retriever.py`, `app/prompts/assistant.py`, `app/routers/chat.py`, `app/routers/kb.py`, `app/repositories/kb.py`, `app/models/kb.py`, `app/models/chat.py`, `app/models/integration.py`, `templates/assistant.html` |
| Khung giao diện dùng chung | `templates/base.html`, `static/` |
| Test hạ tầng & F1/F3 | `tests/conftest.py`, `tests/test_ocr*.py`, `tests/test_card*.py`, `tests/test_normalize.py`, `tests/test_rag*.py` |
| Nhật ký bug F1/F3 | `docs/bugs-f1-f3.md` |

### 2.2 Của Tùng (T)

| Vùng | File/thư mục |
|------|--------------|
| F2 — Hồ sơ doanh nghiệp | `app/services/enrichment.py`, `app/services/normalize_company.py`, `app/services/company_matching.py`, `app/prompts/enrichment.py`, `app/routers/companies.py`, `app/repositories/company.py`, `app/schemas/company.py`, `app/models/company.py`, `templates/companies/` |
| Service embedding (RAG) | `embedder/` (Dockerfile + FastAPI + model), `scripts/spike_embedding.py`, `docs/adr-embedding.md` |
| Dashboard & export | `app/routers/stats.py`, `app/routers/export.py`, `templates/dashboard.html` |
| Tài liệu & dữ liệu mẫu | `docs/` *(trừ `oauth-setup.md`, `bugs-f1-f3.md`, `accuracy.md`)*, `samples/`, `scripts/spike_*.py` |
| Test F2 | `tests/test_normalize_company.py`, `tests/test_company*.py`, `tests/test_enrichment.py`, `tests/test_export.py` |
| Nhật ký bug F2 | `docs/bugs-f2.md` |

### 2.3 Ba ngoại lệ dễ nhầm

| File | Chủ | Vì sao dễ nhầm |
|------|-----|----------------|
| `app/services/normalize.py` | **Q** | Chuẩn hoá **SĐT/email** — hậu xử lý của F1, gọi trong `ocr.py` |
| `app/services/normalize_company.py` | **T** | Chuẩn hoá **tên công ty** — phục vụ dedupe F2. Tách file riêng chỉ để giữ quy ước 1 chủ/file |
| `docs/accuracy.md` | **Q** | Nằm trong `docs/` (vùng của T) nhưng là báo cáo độ chính xác OCR — thuộc F1 |

---

## 3. Ba file dùng chung — đã "đóng băng" từ D1

Ba file dưới đây về lý thuyết ai cũng cần đụng khi thêm tính năng. Q đã khai sẵn stub đầy đủ
từ D1 để **về sau không ai phải sửa**:

| File | Đã khai sẵn gì | Hệ quả |
|------|----------------|--------|
| `app/main.py` | Vòng lặp `for _name, _router in iter_routers(): app.include_router(_router)` | Thêm router mới không phải sửa file này |
| `app/routers/__init__.py` | `ROUTER_MODULES` liệt kê đủ 7 module, `iter_routers()` bỏ qua module chưa có biến `router` | Chủ router chỉ cần khai `router = APIRouter(...)` trong file của mình |
| `docker-compose.yml` | Khối service `embedder` build từ `./embedder`, port 8001, biến `EMBEDDING_MODEL`, healthcheck | T chỉ việc tạo thư mục `embedder/`, không đụng compose của Q |

> Ví dụ: T làm task 5.4 chỉ cần viết trong `app/routers/companies.py`:
> ```python
> from fastapi import APIRouter
> router = APIRouter(prefix="/api/companies", tags=["companies"])
> ```
> là router tự được gắn vào app.

---

## 4. Ba chỗ hai người gọi code của nhau

Gọi, **không** sửa. Chữ ký hàm phải chốt trong buổi họp đầu D2 rồi mới code:

| Bên gọi | Bên cung cấp | Chốt ở |
|---------|--------------|--------|
| `cards.confirm` (Q) | `company_matching.upsert_company()` (T) | Họp D2 · [docs/api.md](./api.md) mục 8 |
| `enrichment` (T) | `kb.ingest_company_profile()` (Q) | Họp D2 · [docs/api.md](./api.md) mục 8 |
| `services/embeddings.py` (Q) | service `embedder` qua HTTP (T) | Đã chốt sẵn: Plan.md mục 2.6 |

---

## 5. Quy trình khi cần thay đổi file của người kia

**Không tự sửa.** Kể cả khi chỉ là một dòng, kể cả khi người kia đang bận.

1. Nhắn cho chủ file (hoặc nêu ở daily sync sáng hôm sau nếu không gấp).
2. Nói rõ **cần gì**, không phải **sửa thế nào**: *"cần `upsert_company()` trả thêm
   `display_name`"*, chứ không phải *"thêm dòng 42 vào file X"*.
3. Chủ file tự sửa, tự commit, báo lại.
4. Nếu chủ file đang nghỉ/bận cả ngày và việc bị chặn → ghi vào ô trạng thái task là `⏸️`
   kèm **lý do và tên người gỡ vướng**, rồi chuyển sang task khác.

**Ngoại lệ duy nhất được tự sửa file người khác:** không có.

---

## 6. Git

### Nhánh

`main` luôn ở trạng thái chạy được, chỉ nhận PR. Nhánh làm việc: `<loại>/<module>-<việc>`

```
feat/cards-upload        fix/ocr-json-hong        docs/erd
feat/companies-enrich    chore/ci-gitleaks        refactor/kb-chunking
```

PR nhỏ, **merge trong ngày**, rebase lên `main` trước khi merge, **không để nhánh sống qua đêm**.

### Commit

Conventional Commits: `<loại>(<phạm vi>): <mô tả ngắn, chữ thường, không dấu chấm cuối>`

Loại: `feat` · `fix` · `docs` · `refactor` · `test` · `chore`
Phạm vi: `cards` · `companies` · `kb` · `chat` · `integration` · `embedder` · `db` · `ci`

### PR phải xanh CI mới được merge

`main` bật branch protection, required check = **`CI success`**. Chạy trước ở máy cho đỡ mất lượt:

```bash
ruff check . && ruff format --check . && mypy app embedder scripts && pytest
```

Chi tiết: [.github/workflows/README.md](../.github/workflows/README.md).

---

## 7. Alembic — vì sao chỉ Q được sinh revision

Alembic quản lý migration bằng chuỗi liên kết `down_revision`. Hai người cùng chạy
`alembic revision` trên hai nhánh sẽ tạo **2 head** cùng trỏ về một cha → merge xong phải chạy
`alembic merge` bằng tay, và rất dễ để lọt một head lên `main` khiến `alembic upgrade head` lỗi
trên máy người khác.

Vì việc này khó phát hiện lúc review, CI có job **`Chỉ một head Alembic`** bắt lỗi ngay khi mở PR.

**T cần đổi schema → báo Q.** Migration khởi tạo ở D1 đã tạo đủ 6 nhóm bảng nên về sau rất ít
revision mới. Hai trường hợp đã biết sẽ cần Q sinh revision:

1. Task 2.6 chốt model embedding **khác 384 chiều** → đổi kiểu cột `kb_chunks.embedding`,
   phải xong **trước D6**.
2. Task 5.8 cần bảng theo dõi **job lập hồ sơ hàng loạt** → T chốt cấu trúc, báo Q **chậm nhất đầu D5**.

---

## 8. Hai chỗ đang tạm thời "vay nợ" nhau

Hai điểm dưới đây là hệ quả của việc `app/models/company.py` (của T) còn là stub trong khi
migration `0001` (của Q) đã tạo bảng. **Gỡ ngay khi T khai xong model `Company`:**

| Chỗ | Hiện trạng | Ai gỡ |
|-----|-----------|-------|
| `app/models/card.py` | `company_id` cố ý **chưa khai** `ForeignKey("companies.id")` — khai lúc này sẽ làm `Base.metadata` ném `NoReferencedTableError`, hỏng cả autogenerate lẫn fixture test | **Q** |
| `alembic/env.py` | `TABLES_WITHOUT_MODEL = {"companies", "company_profiles"}` bỏ qua 2 bảng khi so sánh autogenerate, nếu không sẽ sinh nhầm `drop_table` | **Q** |

→ **T làm xong `app/models/company.py` phải báo Q ngay** để Q gỡ hai chỗ này. Quên thì
autogenerate về sau sẽ sinh migration sai.

---

## 9. Nhật ký bug tách hai file

`docs/bugs-f1-f3.md` (Q ghi) và `docs/bugs-f2.md` (T ghi) — vì D10 là ngày cả hai cùng ghi bug
liên tục, dùng chung một file gần như chắc chắn conflict.

Phát hiện bug thuộc module của người kia → **báo chủ module, chủ module tự ghi vào file của mình**.
Phân loại bug về đúng chủ ở buổi họp đầu D10 (30').
