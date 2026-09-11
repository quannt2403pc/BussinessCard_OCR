# Task.md — Nhiệm vụ theo từng ngày

Dự án: **BusinessCard_OCR** · 11 ngày + 4 ngày dự phòng · Thành viên: **Quân (Q)**, **Tùng (T)**
Kế hoạch tổng thể: xem **[Plan.md](./Plan.md)**

**Ưu tiên:** M = Must · S = Should · C = Could
Mỗi ngày làm việc ~8h. Ngày kết thúc khi các "Tiêu chí hoàn thành" đều đạt và code đã merge vào `main`.
Ước tính giờ chỉ mang tính tương đối — ngày nào tràn thì **cắt task ưu tiên S/C của chính ngày đó**, không dồn sang ngày dự phòng.

---

## Quy ước chống xung đột Git

1. **Mỗi task có đúng 1 người chịu trách nhiệm.** Không có task nào ghi "Q + T". Việc cần cả hai (họp, chốt thiết kế) không nằm trong bảng task — xem mục *Họp đồng bộ* bên dưới.
2. **Không sửa file thuộc quyền sở hữu của người kia.** Cần thay đổi → nhắn cho chủ file, chủ file tự sửa và commit. Ngoại lệ duy nhất: đọc/import để dùng.
3. **Không xếp hai người vào cùng một file trong cùng một ngày.** Nếu bất khả kháng thì làm tuần tự, người sau `git pull --rebase` trước khi sửa.
4. **`app/main.py` và `app/routers/__init__.py` được khai báo sẵn stub cho toàn bộ router ngay từ D1** (cards, companies, integration, chat, kb, stats, export) → về sau không ai phải sửa hai file này khi thêm tính năng.
5. **Chỉ Q tạo Alembic revision.** Hai người cùng sinh revision sẽ tạo 2 head phải merge thủ công — **CI có job `Chỉ một head Alembic` bắt lỗi này ngay khi mở PR**. T cần đổi schema → báo Q. Migration khởi tạo ở D1 đã tạo đủ bảng nên về sau rất ít revision mới.
6. **Model tách theo file**: `models/card.py`, `models/company.py`, `models/kb.py`, `models/chat.py`, `models/integration.py` — không dùng một file `models.py` chung.
7. **Nhật ký bug tách 2 file**: `docs/bugs-f1-f3.md` (Q ghi) và `docs/bugs-f2.md` (T ghi). Ai phát hiện bug thuộc module của người kia thì báo cho chủ module, chủ module tự ghi vào file của mình.
8. **PR nhỏ, merge trong ngày.** Branch `feat/<module>-<việc>`; rebase lên `main` trước khi merge; không để branch sống qua đêm. **PR phải xanh CI** — `main` bật branch protection, required check là `CI success` (xem `.github/workflows/README.md`).
9. **Tài liệu API sau D1 dùng Swagger tự sinh** (`/docs`) làm nguồn chính — `docs/api.md` chỉ là bản chốt ban đầu, không cập nhật tay để tránh sửa file chung.

### Bảng sở hữu file/module

| Vùng | Chủ sở hữu | File/thư mục |
|------|-----------|--------------|
| Hạ tầng & khởi động | **Q** | `Dockerfile`, `docker-compose.yml`, `.env.example`, `app/main.py`, `app/core/`, `alembic/`, `scripts/seed.py`, `README.md` |
| Kết nối CLIProxy & LLM | **Q** | `app/services/cliproxy_client.py`, `app/services/llm.py`, `app/routers/integration.py`, `templates/settings.html`, `docs/oauth-setup.md` |
| F1 — OCR danh thiếp (gồm hậu xử lý & tối ưu sau khi quét) | **Q** | `app/services/ocr.py`, `app/services/image.py`, `app/services/normalize.py`, `app/prompts/ocr.py`, `app/routers/cards.py`, `app/models/card.py`, `templates/cards/`, `docs/accuracy.md` |
| F3 — RAG & trợ lý AI | **Q** | `app/services/embeddings.py` (client gọi `embedder`), `app/services/kb.py`, `app/services/retriever.py`, `app/prompts/assistant.py`, `app/routers/chat.py`, `app/routers/kb.py`, `app/models/kb.py`, `app/models/chat.py`, `templates/assistant.html` |
| Khung giao diện dùng chung | **Q** | `templates/base.html`, `static/` |
| Test hạ tầng & F1/F3 | **Q** | `tests/conftest.py`, `tests/test_ocr*.py`, `tests/test_card*.py`, `tests/test_normalize.py`, `tests/test_rag*.py` |
| F2 — Hồ sơ doanh nghiệp | **T** | `app/services/enrichment.py`, `app/services/normalize_company.py`, `app/services/company_matching.py`, `app/prompts/enrichment.py`, `app/routers/companies.py`, `app/repositories/company.py`, `app/schemas/company.py`, `app/models/company.py`, `templates/companies/` |
| Service embedding (RAG) | **T** | `embedder/` (Dockerfile + FastAPI + model), `scripts/spike_embedding.py`, `docs/adr-embedding.md` |
| Dashboard & export | **T** | `app/routers/stats.py`, `app/routers/export.py`, `templates/dashboard.html` |
| Tài liệu & dữ liệu mẫu | **T** | `docs/` (trừ `oauth-setup.md`, `bugs-f1-f3.md`, `accuracy.md`), `samples/`, `scripts/spike_*.py` |
| Test F2 | **T** | `tests/test_normalize_company.py`, `tests/test_company*.py`, `tests/test_enrichment.py`, `tests/test_export.py` |

> Ba chỗ hai người **gọi** code của nhau (không sửa file của nhau) — chữ ký hàm/hợp đồng phải chốt trong buổi họp D2:
> `cards.confirm` (Q) → gọi `company_matching.upsert_company()` (T) · `enrichment` (T) → gọi `kb.ingest_company_profile()` (Q) · `services/embeddings.py` (Q) → gọi HTTP tới service `embedder` (T), hợp đồng `POST /embed` đã chốt sẵn ở Plan.md mục 2.6.
> Riêng service `embedder`: **T dựng thư mục `embedder/`, Q khai báo sẵn khối service trong `docker-compose.yml` ngay từ D1** (task 1.4) → về sau không ai phải sửa file của người kia.

### Họp đồng bộ *(không phải task, không có owner)*

| Khi nào | Thời lượng | Nội dung |
|---------|-----------|----------|
| Đầu D1 | 60' | Chốt phạm vi, danh sách trường dữ liệu, duyệt ERD; T ghi biên bản |
| Đầu D2 | 30' | Chốt chữ ký 2 hàm dùng chung (`upsert_company`, `ingest_company_profile`) + hợp đồng JSON của `POST /api/chat` |
| Mỗi sáng | 15' | Daily: hôm qua / hôm nay / vướng gì; ai cần chạm file của ai thì báo tại đây |
| Đầu D10 | 30' | Phân loại bug về đúng chủ module |

---

## Quy ước trạng thái

| Ký hiệu | Trạng thái | Ý nghĩa |
|---------|-----------|---------|
| ⬜ | **Chưa làm** | Chưa bắt đầu |
| 🔄 | **Đang làm** | Đang thực hiện (mỗi người tối đa 2 task cùng lúc) |
| ✅ | **Xong** | Đã đạt DoD: chạy được trong Docker + đã test + đã merge vào `main` |
| ⏸️ | **Tạm dừng** | Bị chặn — ghi rõ lý do & người gỡ vướng ngay trong ô trạng thái |
| ❌ | **Cắt khỏi phạm vi** | Thống nhất bỏ task — ghi rõ lý do (không mặc định đẩy sang ngày dự phòng) |

Cách ghi chú thêm khi cần: `🔄 Đang làm — 60%`, `⏸️ Chờ xác nhận provider OAuth`, `❌ Cắt — không kịp, ưu tiên C`.

> **Lưu ý:** toàn bộ phạm vi đã biết nằm trong **D1–D11**. Không lên lịch task nào vào D12–D15 (xem mục D12–D15).
Cập nhật trạng thái vào cuối mỗi ngày, trước buổi daily sync hôm sau.

### Theo dõi tiến độ tổng

| Ngày | Giai đoạn | Trạng thái ngày | Ghi chú |
|------|-----------|-----------------|---------|
| D1 | P0 — Khởi động | ✅ Xong | 1.1–1.10 đã merge vào `main`. Kiểm chứng lại 2026-09-10: `up -d` xanh, `/health` → `{"status":"ok"}`, `alembic current` = `0001 (head)` đủ 8 bảng, ruff/format/mypy xanh. Riêng **1.11 còn 🔄 40%** (chưa có ảnh nào) — ưu tiên S, theo kế hoạch hoàn tất ở **3.9** |
| D2 | P1 — Nền tảng AI | 🔄 Đang làm | Phần của **Q (2.1–2.5) đã viết xong và chạy trong Docker**, còn đúng một việc phải làm tay: bấm OAuth đầu–cuối bằng tài khoản Google thật (2.5) rồi thử vision (2.3). Phần của **T (2.6–2.9) chưa bắt đầu**. I-01/I-04/I-06 đã gỡ; I-02/I-03 đã xử lý trong mã; phát sinh I-09, I-10. **I-11: phát hiện 2026-09-11 là workflow CI chưa bao giờ chạy được** — đã sửa, chờ một lượt chạy thật trên `main` để xác nhận |
| D3 | P2 — F1 OCR | ⬜ Chưa làm | |
| D4 | P2 — F1 OCR | ⬜ Chưa làm | |
| D5 | P3 — F2 Hồ sơ DN | ⬜ Chưa làm | |
| D6 | P3 — F2 Hồ sơ DN | ⬜ Chưa làm | |
| D7 | P4 — F3 RAG | ⬜ Chưa làm | |
| D8 | P4 — F3 RAG | ⬜ Chưa làm | |
| D9 | P5 — Hoàn thiện | ⬜ Chưa làm | |
| D10 | P6 — Kiểm thử | ⬜ Chưa làm | |
| D11 | P7 — Bàn giao | ⬜ Chưa làm | |
| D12–D15 | Dự phòng | ⬜ Chưa dùng | Không có task đặt trước |

**Tổng quan:** 10 / 103 task (D1–D11) hoàn thành (10%) · 6 task đang dở (1.11, 2.1, 2.2, 2.3, 2.4, 2.5) · Cập nhật lần cuối: 2026-09-11

> Năm task D2 của Q đều để 🔄 chứ không ✅: DoD đòi **đã merge vào `main`**, mà nhánh `feature-day1-issues` chưa merge. Riêng 2.3 và 2.5 còn thiếu bước bấm OAuth thật bằng tài khoản Google — không ai thay được.

### Nhật ký vấn đề đang mở

Vấn đề phát hiện trong lúc làm, **chưa xử lý**, có thể làm hỏng task của người khác.
Ai phát hiện thì ghi vào đây; **người gỡ là chủ sở hữu file liên quan**, không phải người phát hiện.
Xử lý xong thì đổi trạng thái sang ✅ kèm ngày, không xoá dòng.

| Mã | Vấn đề | Phát hiện | Người gỡ | Chặn task | Trạng thái |
|----|--------|-----------|----------|-----------|------------|
| **I-01** | `remote-management.allow-remote: false` của CLIProxy **chặn container `api`**. Mã nguồn coi "localhost" đúng nghĩa đen `127.0.0.1`/`::1` (`handler.go:273,337`); `api` gọi qua mạng Docker nhận `403 remote management disabled` dù gửi đúng key → `config.yaml` bắt buộc `allow-remote: true` | T (1.9) | **Q** | 2.1 | ⬜ Chưa xử lý |
| **I-02** | `get-auth-status` **không dùng được cho badge trạng thái**. Không truyền `state` thì trả `{"status":"ok"}` kể cả khi chưa đăng nhập bao giờ → badge sẽ luôn xanh (lỗi âm thầm). Badge phải đọc `GET /auth-files`; `get-auth-status?state=…` chỉ để poll trong lúc chờ đồng ý. **Plan.md mục 2.4 đang ghi sai** | T (1.9) | **Q** | 2.4, 2.5 | ⬜ Chưa xử lý |
| **I-03** | `LLM_MODEL=gemini-flash-latest` **không tồn tại** trong provider `antigravity` (model đó thuộc channel `aistudio`/`gemini`, cần API key). Antigravity chỉ có `gemini-3-flash`, `gemini-3.6-flash-high`, `gemini-3.1-flash-lite`… Chốt tên model thật bằng `GET /v0/management/model-definitions/antigravity` sau khi container chạy | T (1.9) | **Q** | 2.3 | ⬜ Chưa xử lý |
| **I-04** | Compose phải publish thêm **cổng 51121** (callback OAuth của Antigravity, `internal/auth/antigravity/constants.go:8`). Thiếu thì bấm nút OAuth đi tới Google xong, trình duyệt quay về `localhost:51121` và chết — token không bao giờ được lưu | T (1.9) | **Q** | 2.1 | ⬜ Chưa xử lý |
| **I-05** | Sai management key **5 lần → ban IP 30 phút** (`handler.go:301-302`, đếm theo IP, cả container `api` chung một IP). Client CLIProxy **không được retry khi gặp 401/403**, chỉ retry lỗi mạng và 5xx. Gỡ ban sớm: `docker compose restart cliproxy` | T (1.9) | **Q** | 2.2 | ⬜ Chưa xử lý |
| **I-06** | Đường dẫn mã nguồn CLIProxy trong Plan.md mục 2.4 (`C:\FSoft\ojt\CliProxy`) **không tồn tại**; thực tế ở `C:\Users\pc\source\repos\CLIProxyAPI`, commit đã chuyển từ `ecc9aa72` sang `7fac6b15` (kết luận cũ vẫn đúng, đã kiểm lại) | T (1.9) | **Q** | — | ⬜ Chưa xử lý |
| **I-07** | ⚠️ **Chưa biết model nào tra cứu được Internet.** `supports_web_search` KHÔNG nằm trong `models.json` tĩnh — CLIProxy nạp lúc chạy từ `fetchAvailableModels.webSearchModelIds` (`model_registry.go:65-67`), tức **chỉ đọc được sau khi OAuth xong**. Nếu `gemini-3-flash` không hỗ trợ thì **F2 không có nguồn URL → trượt tiêu chí A5**. Kiểm ngay khi 2.1+2.5 xong: `python scripts/spike_websearch.py --list-models` | T (2.7) | **T** (sau khi Q mở đường) | 2.7, 4.7 | ⏸️ Chờ 2.1 + 2.5 |
| **I-08** | `services/llm.py` (task 2.3) phải cho **truyền `tools` xuống `generateContent`**. Nếu hàm chỉ nhận mỗi prompt thì F2 không bật được `googleSearch` → enrichment mất khả năng tra cứu Internet. Chốt chữ ký hàm ở daily sync | T (2.7) | **Q** | 2.3, 4.7 | ⬜ Chưa xử lý |
| **I-09** | 🔴 **CI CHƯA TỪNG XANH — `ci.yml` không hợp lệ, 15/15 run failed ở `0s`.** GitHub từ chối file trước khi tạo job, nên **không đọc cả bộ lọc `on:`** → chạy cả trên nhánh không phải `main` rồi gửi mail báo lỗi. Nguyên nhân: **dòng 160 là comment shell nhưng chứa `${{ }}` rỗng**; GitHub quét cả comment và diễn giải mọi `${{ }}` → biểu thức rỗng làm hỏng workflow. Trớ trêu: chính dòng comment cảnh báo về "Invalid workflow file" đang gây ra lỗi đó. Lịch sử: `d3ca520` hỏng vì nháy kép trong `join(...)`; `e1eabfb` sửa đúng chỗ đó nhưng comment giải thích lại thêm `${{ }}`. **Cách sửa: bỏ `$` trong comment dòng 160** (viết `biểu thức {{ }}`). Hệ quả hiện tại: luật "PR phải xanh CI" **không thể thực thi** — required check `CI xanh` không bao giờ tồn tại | T (2.6) | **Q** | Mọi PR | ⬜ Chưa xử lý |
| **I-10** | 🔴 **Không bật được branch protection — repo `private` trên gói GitHub Free.** API trả `403 "Upgrade to GitHub Pro or make this repository public"` cho `rulesets`, và `404` cho `branches/main/protection`. Nghĩa là **không phải Q quên bật, mà là không bật được**. Hệ quả: PR merge thẳng vào `main` không qua kiểm tra nào (PR #1–#5 đều vậy). **4 file đang khẳng định sai**: Plan.md mục 5.3, Task.md quy ước số 8, `README.md`, `.github/workflows/README.md`. Ba lựa chọn: (a) chuyển repo sang **public** → protection miễn phí; (b) nâng **GitHub Pro** → tốn tiền; (c) chấp nhận là **kỷ luật thủ công**, sửa lại tài liệu cho đúng sự thật. Với demo 11 ngày/2 người thì (c) hợp lý nhất. Ghép với I-09: kể cả bật được cũng vô dụng vì check `CI xanh` chưa bao giờ tồn tại → PR sẽ kẹt vĩnh viễn ở trạng thái chờ | T (2.6) | **Q** | Mọi PR | ⬜ Chưa xử lý |

| **I-09** | **Container `cliproxy` thoát (exit 0) ngay sau `Initializing Antigravity authentication...`** khi gọi `antigravity-auth-url` **không kèm `is_webui=1`**. Xảy ra 1 lần, ngay sau khi `cliproxy/config.yaml` bị ghi đè bằng key mới; thử lại nhiều lần sau đó **không tái hiện** nên chưa chốt được nguyên nhân (nghi file watcher nạp lại config). `restart: unless-stopped` lúc đó cũng không dựng container dậy | Q (2.2) | **Q** | 2.5 | ⬜ Chưa xử lý — đã né bằng cách **luôn gửi `is_webui=1`** (nhánh dành cho luồng UI). Nếu tái hiện lúc bấm OAuth thật ở 2.5 thì ghim lại log và mở issue với upstream. Ảnh hưởng hiện tại: không có, mọi lời gọi trong mã đều có `is_webui=1` |
| **I-10** | **CLIProxy trả y hệt `400 unknown provider for model <tên>` cho hai sự cố trái ngược nhau**: (a) `LLM_MODEL` sai tên, (b) tên model đúng nhưng **chưa có credential nào**. Đọc nguyên văn mà đoán thì sẽ đẩy người dùng đi sửa `.env` trong khi thật ra chỉ cần bấm nút OAuth. Biến thể thứ ba: credential vừa bị xoá → `503 auth_unavailable` (mã retry-được, dễ bị retry 3 lần vô ích) | Q (2.3) | **Q** | — | ✅ Xong 2026-09-11 — `services/llm.py` phân biệt bằng cách tra `model-definitions/<channel>`: model **có** trong danh mục ⇒ `LLMNotConnectedError`, **không có** ⇒ `LLMInvalidModelError`, không tra được ⇒ báo cả hai khả năng. `503 auth_unavailable` được tách thành `CliProxyNoCredentialError` và **không retry**. Đo lại: thông báo đúng, 36ms thay vì ~1.5s |
| **I-11** | **Workflow CI chưa bao giờ chạy — 19/19 lượt đều đỏ với 0 job, 0 phút runner.** GitHub báo *Invalid workflow file* và huỷ `ci.yml` trước khi tạo job nào, nên không có log để đọc. Hai nguyên nhân nối tiếp nhau, cùng ở bước gộp kết quả của job `CI xanh`: (a) `d3ca520` viết `join(needs.*.result, " ")` — biểu thức GitHub không chấp nhận nháy kép; (b) `e1eabfb` sửa đúng thành nháy đơn nhưng **thêm dòng comment chứa cặp ngoặc biểu thức rỗng** để giải thích cái bẫy đó — trong khối `run:` thì comment shell vẫn bị GitHub đánh giá như biểu thức, nên bản vá tái tạo chính lỗi nó mô tả. Hệ quả: mọi tuyên bố "CI xanh" từ D1 đến nay đều chưa từng được GitHub kiểm chứng; branch protection lấy `CI xanh` làm required check cũng không bảo vệ được gì | Q | **Q** | — | ✅ Xong 2026-09-11 — bỏ cặp ngoặc biểu thức rỗng khỏi comment, giữ nguyên lời cảnh báo dưới dạng chữ. Đã rà toàn bộ 7 biểu thức còn lại trong `ci.yml` (không cái nào rỗng hoặc dùng nháy kép) và chạy trước tất cả kiểm tra của CI ở máy: `ruff check` + `ruff format --check` xanh (61 file), `mypy` xanh (47 file), `pytest` trả 5 (chưa có test — `ci.yml` đã dung thứ), `alembic heads` = 1 head, gitleaks `no leaks found`. **Đã xác nhận bằng lượt chạy thật**: run `34554835829` trên `main` → `success`, tên workflow GitHub đọc được là `CI` (trước đó hiển thị nguyên đường dẫn file — dấu hiệu file không parse được). Nhân tiện đổi toàn bộ tên job/step trong `ci.yml` sang tiếng Anh cho chuyên nghiệp; **required check đổi tên `CI xanh` → `CI success`**, đã cập nhật ở `.github/workflows/README.md`, `README.md`, `Plan.md` và quy ước số 8 phía trên. ⚠️ `docs/ownership.md` dòng 130 vẫn ghi tên cũ — **file của T**, nhờ T sửa |

Chi tiết đầy đủ kèm trích dẫn mã nguồn: [`docs/cliproxy-notes.md`](./docs/cliproxy-notes.md).

---

## D1 — Khởi động, thiết kế & dựng khung dự án

**Mục tiêu ngày:** `docker compose up` chạy được, DB kết nối OK, có ERD và API spec đã chốt.

| # | Task | Người | Ưu tiên | Ước tính | Trạng thái |
|---|------|-------|---------|----------|--------|
| 1.1 | Khởi tạo Git repo, `.gitignore`, `README.md` sơ bộ, quy ước branch/commit | Q | M | 0.5h | ✅ Xong 2026-09-10 — đã merge vào `main` (PR #2/#3/#4) |
| 1.2 | Skeleton FastAPI: `app/main.py`, `app/core/config.py`, `GET /health` + **khai báo sẵn stub toàn bộ router** (cards, companies, integration, chat, kb, stats, export) để về sau không ai phải sửa `main.py` | Q | M | 2h | ✅ Xong 2026-09-10 — đã merge vào `main` (PR #2/#3/#4) |
| 1.3 | `templates/base.html`: layout, nav (Danh thiếp / Doanh nghiệp / Trợ lý AI / Cài đặt), Tailwind CDN | Q | M | 1h | ✅ Xong 2026-09-10 — đã merge vào `main` (PR #2/#3/#4) |
| 1.4 | `Dockerfile` backend + `docker-compose.yml` (api + db pgvector + adminer + **khai báo sẵn khối service `embedder`** build từ `./embedder`, port 8001, biến `EMBEDDING_MODEL`, healthcheck) — khai trước để T chỉ việc thêm thư mục `embedder/`, không phải sửa compose của Q | Q | M | 2h | ✅ Xong 2026-09-10 — đã merge vào `main` (PR #2/#3/#4) |
| 1.5 | ERD chi tiết → `docs/erd.md` (T review qua PR, không sửa trực tiếp) | Q | M | 1h | ✅ Xong 2026-09-10 — đã merge vào `main` (PR #2/#3/#4) |
| 1.6 | Alembic + migration khởi tạo **đủ 6 nhóm bảng**, bật extension `vector`, `kb_chunks.embedding = vector(384)` (theo mặc định đề xuất ở Plan.md mục 2.6), tách model theo file (`card/company/kb/chat/integration`) | Q | M | 2h | ✅ Xong 2026-09-10 — đã merge vào `main` (PR #2/#3/#4) |
| 1.7 | Ghi biên bản chốt phạm vi + danh sách trường dữ liệu cần trích xuất vào `docs/scope.md` (sau họp đầu ngày) | T | M | 1h | ✅ Xong 2026-09-10 — đã merge vào `main` (PR #2/#3/#4) |
| 1.8 | `docs/api.md`: spec endpoint + schema request/response (bản chốt ban đầu, sau D1 dùng Swagger tự sinh) | T | M | 2h | ✅ Xong 2026-09-10 — đã merge vào `main` (PR #2/#3/#4) |
| 1.9 | Khảo sát CLIProxyAPI: chạy thử container, đọc `config.example.yaml`, xác định management key & provider OAuth cho Gemini → `docs/cliproxy-notes.md` | T | M | 2h | ✅ Xong 2026-09-10 — `docs/cliproxy-notes.md` đã merge; phần **chạy thử container** hoàn tất khi làm 2.1 và **xác nhận đúng cả 4 phát hiện** (I-01 → 403/200, I-02 → `{"status":"ok"}` khi chưa đăng nhập, I-03 → 11 model không có `gemini-flash-latest`, I-04 → cổng 51121) |
| 1.10 | `docs/ownership.md`: chốt bảng sở hữu file/module + quy ước chống xung đột | T | M | 1h | ✅ Xong 2026-09-10 — đã merge vào `main` (PR #2/#3/#4) |
| 1.11 | Tạo `samples/`, bắt đầu thu thập ảnh danh thiếp mẫu (mục tiêu 30 ảnh, ≥3 ngôn ngữ) | T | S | 1h | 🔄 Đang làm — 40%, đã có cấu trúc `samples/` + quy ước đặt tên/ẩn danh + khung `expected.json`; **chưa có ảnh nào** (cần người thu thập, hoàn tất ở 3.9) |

**Tiêu chí hoàn thành:** `docker compose up -d` → `curl localhost:8000/health` trả `{"status":"ok"}`; `alembic upgrade head` tạo đủ bảng; ERD + API spec + bảng sở hữu file đã commit.

---

## D2 — Tích hợp CLIProxy: OAuth + LLM client + nút kết nối

**Mục tiêu ngày:** Bấm nút trên UI → đăng nhập OAuth thành công → gọi thử Gemini Flash trả về kết quả.

| # | Task | Người | Ưu tiên | Ước tính | Trạng thái |
|---|------|-------|---------|----------|--------|
| 2.1 | Thêm service `cliproxy` vào docker-compose (port 8317, volume `cliproxy_auths`, `config.yaml` có management key, `home.enabled: false`) — ⚠️ **đọc I-01 + I-04 trước**: bắt buộc `allow-remote: true` và publish thêm cổng `51121`. (`home` không có trong `config.example.yaml`, mặc định `false` nên không phải khai) | Q | M | 1.5h | 🔄 Đang làm — 100%, chạy & kiểm chứng lại trong Docker 2026-09-11 (v7.2.156): `up -d` xanh, publish đủ `8317` + `51121`, `allow-remote: true` cho 200. **Chờ merge vào `main` mới được ✅** |
| 2.2 | `services/cliproxy_client.py`: httpx async client cho Management API (auth-url, get-auth-status, auth-files, oauth-session), xử lý lỗi & timeout — ⚠️ **I-05**: không retry khi 401/403 | Q | M | 1.5h | 🔄 Đang làm — 100%, đã viết & đo hợp đồng từng endpoint trên container thật (bảng trong docstring của file). Kiểm chứng I-05: sai key → đúng **1** request tới CLIProxy, đếm bằng log container. **Chờ merge vào `main`** |
| 2.3 | `services/llm.py`: `generate_text()`, `generate_vision()` gọi qua CLIProxy, model lấy từ `settings.llm_model` (mặc định nay là **`gemini-3-flash`**, I-03 đã gỡ), retry/backoff — **file dùng chung, chỉ Q sửa**. ⚠️ Việc còn lại của I-03: **thử `generate_vision()` với `inline_data` thật** để chắc channel `antigravity` chạy được vision *trước khi* viết 3.4. *(Không có `embed()` — embedding đi qua service `embedder`, xem Plan.md 2.6)* | Q | M | 2h | 🔄 Đang làm — 85%: `generate_text()`/`generate_vision()` xong, ánh xạ đủ 4 dạng lỗi thật (xem I-10). Gọi text đã chạy tới provider (trả 401 vì chưa có token — đúng như mong đợi). **Còn lại: thử `generate_vision()` với ảnh thật sau khi OAuth xong** — phần cuối của I-03, bắt buộc trước 3.4 |
| 2.4 | `routers/integration.py`: `connect` / `status` (cache vào `integration_status`) / `disconnect` / `test` — ⚠️ **I-02**: `status` phải đọc `GET /auth-files`, không phải `get-auth-status` | Q | M | 1.5h | 🔄 Đang làm — 100%, chạy trong Docker: `status` đọc `auth-files` (thử credential giả → badge xanh đúng email), `connect`/`oauth-status`/`oauth-session`/`disconnect`/`test` đều đã gọi thật. Tắt `cliproxy` thì `/settings` vẫn 200 và `status` trả `reachable:false` + cache. **Chờ merge vào `main`** |
| 2.5 | `templates/settings.html`: **nút "Kết nối CLIProxy (OAuth)"**, badge trạng thái, poll 2s, nút Kiểm tra kết nối & Ngắt kết nối; test tay đầu–cuối và ghi `docs/oauth-setup.md` — ⚠️ **I-02**: poll dùng `get-auth-status?state=…`, badge dùng `auth-files` | Q | M | 2h | 🔄 Đang làm — 80%: trang + badge + 4 nút + poll 2s + huỷ phiên đã xong, `docs/oauth-setup.md` đã viết. **Còn lại: bấm OAuth đầu–cuối với tài khoản Google thật** (máy không tự làm thay được) — xem mục 7 của `docs/oauth-setup.md` |
| 2.6 | **Chốt model embedding cho RAG** (CLIProxy không có endpoint embedding — đã kiểm chứng, xem Plan.md 2.6). Chạy `scripts/spike_embedding.py` so sánh `multilingual-e5-small` (384d) vs `bge-m3` (1024d) trên ~20 đoạn mô tả công ty + 10 truy vấn tự soạn, đủ 5 ngôn ngữ Anh/Việt/Hàn/Nhật/Trung. **Bắt buộc test cả có và không có tiền tố `query:`/`passage:`.** Ghi `docs/adr-embedding.md`: model chốt, số chiều, thời gian nhúng/đoạn, dung lượng image. **Đạt** = top-3 chứa đoạn đúng ở ≥ 8/10 truy vấn và < 200ms/đoạn trên CPU → chọn e5-small; trượt → `bge-m3`; cả hai trượt → báo Q chuyển RAG sang `tsvector`. **Nếu chốt model khác 384 chiều phải báo Q ngay trong ngày** để sinh revision đổi kiểu cột trước D6 | T | M | 2h | ⬜ Chưa làm |
| 2.7 | Spike khả năng tìm kiếm Internet của Gemini Flash qua CLIProxy (`scripts/spike_websearch.py`) → chốt cách gọi, ghi `docs/adr-websearch.md` | T | M | 2h | ⬜ Chưa làm |
| 2.8 | `schemas/company.py`: `CompanyProfileSchema` + `SourceRef` (mỗi trường kèm nguồn) | T | M | 1.5h | ⬜ Chưa làm |
| 2.9 | `prompts/enrichment.py`: prompt sinh hồ sơ DN — tra cứu Internet, trả JSON, **mọi trường phải kèm URL nguồn, không có nguồn thì để null** | T | M | 2.5h | ⬜ Chưa làm |

**Tiêu chí hoàn thành:** Từ UI bấm 1 nút hoàn tất OAuth; badge chuyển "Đã kết nối"; nút "Kiểm tra kết nối" trả về text do Gemini Flash sinh; phương án embedding & web search đã chốt bằng ADR (ADR embedding phải nêu rõ model, số chiều và kết quả đo).

---

## D3 — F1: Pipeline OCR danh thiếp

**Mục tiêu ngày:** Upload 1 ảnh → nhận về JSON có cấu trúc → lưu DB.

| # | Task | Người | Ưu tiên | Ước tính | Trạng thái |
|---|------|-------|---------|----------|--------|
| 3.1 | `routers/cards.py` — `POST /api/cards/upload`: nhận multipart, validate định dạng/kích thước, lưu file vào volume, tính `image_hash` chống trùng | Q | M | 2h | ⬜ Chưa làm |
| 3.2 | `services/image.py`: auto-orient, resize cạnh dài ≤ 1600px, nén JPEG | Q | M | 1h | ⬜ Chưa làm |
| 3.3 | `prompts/ocr.py`: prompt trích xuất danh thiếp (JSON schema cố định, `confidence` từng trường, `language_detected`, cấm suy đoán) | Q | M | 2h | ⬜ Chưa làm |
| 3.4 | `services/ocr.py`: gọi Gemini Flash Vision, parse & validate bằng Pydantic, xử lý khi LLM trả JSON hỏng | Q | M | 2h | ⬜ Chưa làm |
| 3.5 | Lưu `business_cards` (`ocr_raw_json`, `status = needs_review`) + log thời gian xử lý | Q | M | 1h | ⬜ Chưa làm |
| 3.6 | `services/normalize.py`: chuẩn hoá SĐT (E.164), email lowercase, bỏ khoảng trắng thừa, tách nhiều SĐT — hậu xử lý ngay sau khi quét, gọi trong `ocr.py` | Q | M | 1.5h | ⬜ Chưa làm |
| 3.7 | `services/normalize_company.py`: `normalize_company_name()` — bỏ hậu tố pháp lý đa ngôn ngữ (Co., Ltd, JSC, Cty, 株式会社, 주식회사…), lowercase, bỏ dấu (**file riêng** để giữ quy ước 1 chủ sở hữu/file) | T | M | 2h | ⬜ Chưa làm |
| 3.8 | `services/company_matching.py`: `upsert_company()` + so khớp mờ (rapidfuzz) chống trùng, dùng `normalize_company.normalize_company_name()` — chữ ký đã chốt ở họp D2 | T | M | 2.5h | ⬜ Chưa làm |
| 3.9 | Hoàn tất bộ 30 ảnh mẫu (Anh/Việt/Hàn/Nhật/Trung) + `samples/expected.json` để đo độ chính xác | T | S | 1.5h | ⬜ Chưa làm |
| 3.10 | Dựng `embedder/`: Dockerfile + FastAPI `POST /embed` & `GET /health` theo hợp đồng ở Plan.md 2.6, model từ `docs/adr-embedding.md` (task 2.6) **tải lúc build, không tải lúc chạy**. DoD: `docker build` xong, **rút mạng** vẫn `curl localhost:8001/health` ra đúng `model` + `dim` | T | M | 1.5h | ⬜ Chưa làm |

**Tiêu chí hoàn thành:** `curl` upload 1 ảnh → response trả về đủ 7 trường bắt buộc; bản ghi có trong DB; upload lại cùng ảnh không tạo bản ghi trùng; service `embedder` trả vector đúng số chiều đã chốt.

---

## D4 — F1: Giao diện danh thiếp & khung enrichment

**Mục tiêu ngày:** Xem được danh sách danh thiếp trên trình duyệt; khung sinh hồ sơ DN chạy được ở mức service.

| # | Task | Người | Ưu tiên | Ước tính | Trạng thái |
|---|------|-------|---------|----------|--------|
| 4.1 | `routers/cards.py` — `GET /api/cards`: phân trang, tìm kiếm theo tên/công ty/email, lọc theo status | Q | M | 1.5h | ⬜ Chưa làm |
| 4.2 | `routers/cards.py` — `GET /{id}`, `PATCH /{id}`, `DELETE /{id}` | Q | M | 1.5h | ⬜ Chưa làm |
| 4.3 | `routers/cards.py` — `POST /{id}/confirm`: chuyển `confirmed`, gắn `company_id` bằng `company_matching.upsert_company()` của T. **Không kích hoạt enrich** — hồ sơ DN chỉ sinh khi người dùng bấm nút ở màn hình Doanh nghiệp | Q | M | 1h | ⬜ Chưa làm |
| 4.4 | `templates/cards/list.html`: bảng danh sách + ô tìm kiếm + bộ lọc + badge trạng thái | Q | M | 2h | ⬜ Chưa làm |
| 4.5 | `templates/cards/upload.html`: kéo–thả ảnh, `<input capture>` để chụp từ điện thoại, hiển thị tiến trình | Q | M | 1.5h | ⬜ Chưa làm |
| 4.6 | `repositories/company.py`: repository cho `companies` + `company_profiles` | T | M | 2h | ⬜ Chưa làm |
| 4.7 | `services/enrichment.py`: `enrich_company(name, hints)` — hints lấy từ danh thiếp (website, địa chỉ, quốc gia); gọi LLM + web search theo ADR | T | M | 3h | ⬜ Chưa làm |
| 4.8 | Parse & validate kết quả LLM: loại bỏ trường không có nguồn, gắn nhãn `unverified` | T | M | 1.5h | ⬜ Chưa làm |
| 4.9 | `tests/test_normalize_company.py` + `tests/test_company_matching.py` | T | S | 1h | ⬜ Chưa làm |
| 4.10 | `tests/test_normalize.py`: chuẩn hoá SĐT/email đa định dạng & đa quốc gia (hậu xử lý F1) | Q | S | 0.5h | ⬜ Chưa làm |

**Tiêu chí hoàn thành:** Danh sách danh thiếp hiển thị & lọc được trên UI; gọi `enrich_company()` từ script trả về JSON hồ sơ có nguồn.

---

## D5 — F1 review UI + F2 API hồ sơ doanh nghiệp

**Mục tiêu ngày:** Số hoá danh thiếp đầu–cuối qua trình duyệt; gọi API sinh hồ sơ DN chạy nền được.

| # | Task | Người | Ưu tiên | Ước tính | Trạng thái |
|---|------|-------|---------|----------|--------|
| 5.1 | `templates/cards/detail.html`: ảnh gốc bên trái, form các trường bên phải, tô vàng trường `confidence` thấp, nút Lưu / Xác nhận | Q | M | 2.5h | ⬜ Chưa làm |
| 5.2 | `POST /api/cards/batch-upload` (nhiều file) + hàng đợi xử lý nền, giới hạn đồng thời, retry có backoff | Q | S | 3h | ⬜ Chưa làm |
| 5.3 | `templates/cards/batch.html`: theo dõi tiến trình batch (đã xử lý / đang xử lý / lỗi) | Q | S | 2h | ⬜ Chưa làm |
| 5.4 | `routers/companies.py` — `POST /{id}/enrich` enrich 1 công ty (primitive để 5.8 gọi lại): chạy nền, cập nhật `company_profiles.status` (draft → generated), chống chạy trùng | T | M | 2h | ⬜ Chưa làm |
| 5.5 | `routers/companies.py` — `GET /api/companies`, `GET /{id}` (kèm hồ sơ + danh sách liên hệ từ danh thiếp) | T | M | 1.5h | ⬜ Chưa làm |
| 5.6 | Xử lý lỗi enrich: không tìm thấy thông tin, LLM timeout, kết quả rỗng → trạng thái rõ ràng trả về cho UI | T | M | 1.5h | ⬜ Chưa làm |
| 5.8 | `routers/companies.py` — **`POST /api/companies/enrich-batch`**: nhận `company_ids[]`, tạo job trong DB, chạy nền qua hàng đợi, **giới hạn đồng thời + retry có backoff** (rủi ro R5), bỏ qua công ty đang chạy dở, trả `job_id`. Kèm `GET /api/companies/enrich-jobs/{job_id}` trả tiến trình từng công ty (chờ / đang chạy / xong / lỗi kèm thông báo) | T | M | 2h | ⬜ Chưa làm |

**Tiêu chí hoàn thành:** Trên trình duyệt: chọn ảnh → xem kết quả trích xuất → sửa → Xác nhận → hiện trong danh sách. Gọi enrich cho 3 công ty thật → mỗi hồ sơ có ≥ 5 trường kèm URL nguồn kiểm chứng được; `POST /api/companies/enrich-batch` với 3 id chạy được cả 3 và `enrich-jobs/{job_id}` phản ánh đúng tiến trình.

---

## D6 — F2 giao diện hồ sơ DN + F3 Knowledge Base

**Mục tiêu ngày:** Xem/sửa hồ sơ DN trên UI; index toàn bộ card + profile vào KB.

| # | Task | Người | Ưu tiên | Ước tính | Trạng thái |
|---|------|-------|---------|----------|--------|
| 6.1 | `tests/conftest.py` + fixtures (DB test, mock CLIProxy bằng respx) + `tests/test_ocr.py` | Q | M | 2h | ⬜ Chưa làm |
| 6.2 | `services/kb.py`: chunk + serialize danh thiếp và hồ sơ DN thành văn bản; export hàm `ingest_card()` / `ingest_company_profile()` cho T gọi | Q | M | 2h | ⬜ Chưa làm |
| 6.3 | Repository `kb_chunks` + migration tạo index `ivfflat` (cosine) | Q | M | 1.5h | ⬜ Chưa làm |
| 6.4 | `services/embeddings.py` gọi service `embedder` (batch, timeout, retry) + `POST /api/kb/reindex` index lại toàn bộ. **Thêm tiền tố `passage:` khi index và `query:` khi truy vấn** theo `docs/adr-embedding.md` | Q | M | 2.5h | ⬜ Chưa làm |
| 6.5 | `templates/companies/list.html` — **màn hình lập hồ sơ đối tác**: danh sách công ty + số liên hệ + trạng thái hồ sơ; **checkbox từng dòng + “chọn tất cả”**, bộ lọc “chưa có hồ sơ”, thanh hành động hiện số đã chọn kèm nút **“Tạo hồ sơ doanh nghiệp”** gọi `enrich-batch` (theo dõi tiến trình làm ở 7.10) | T | M | 2.5h | ⬜ Chưa làm |
| 6.6 | `templates/companies/detail.html`: hồ sơ đầy đủ (MST, quy mô, ngành nghề, sản phẩm, địa chỉ, mô tả) + khối "Nguồn tham khảo" có link | T | M | 3h | ⬜ Chưa làm |
| 6.7 | Nút “Tạo lại hồ sơ” trên trang chi tiết — gọi lại `enrich-batch` với đúng 1 id, không dựng luồng riêng | T | M | 0.5h | ⬜ Chưa làm |
| 6.8 | `PATCH /api/companies/{id}/profile` + form chỉnh sửa thủ công, đánh dấu `verified` | T | M | 2h | ⬜ Chưa làm |

**Tiêu chí hoàn thành:** Xác nhận danh thiếp xong mà **chưa** bấm nút thì không hồ sơ nào được sinh; vào màn hình Doanh nghiệp tích chọn 3 công ty → bấm “Tạo hồ sơ doanh nghiệp” → cả 3 chạy nền và xem được hồ sơ (badge tiến trình hoàn thiện ở 7.10); trên UI xem được hồ sơ DN đầy đủ có nguồn và sửa/lưu được; `POST /api/kb/reindex` index toàn bộ card + profile không lỗi; `docker compose up` từ máy **ngắt mạng** vẫn khởi động được `embedder` (chứng minh model đã nằm trong image).

---

## D7 — F3: RAG retrieval

**Mục tiêu ngày:** Truy vấn ngôn ngữ tự nhiên trả về đúng các chunk liên quan.

| # | Task | Người | Ưu tiên | Ước tính | Trạng thái |
|---|------|-------|---------|----------|--------|
| 7.1 | `services/retriever.py`: vector search top-k theo cosine + ngưỡng điểm | Q | M | 2h | ⬜ Chưa làm |
| 7.2 | Hybrid search: kết hợp vector + full-text (`tsvector`) để bắt tốt tên riêng, email, SĐT | Q | S | 2.5h | ⬜ Chưa làm |
| 7.3 | Auto-ingest KB khi confirm danh thiếp (hook trong `cards.py`) | Q | M | 1.5h | ⬜ Chưa làm |
| 7.4 | Test retrieval với 10 truy vấn mẫu, đo recall thủ công, tinh chỉnh chunk size & top-k | Q | M | 2h | ⬜ Chưa làm |
| 7.5 | Gọi `kb.ingest_company_profile()` ở cuối luồng enrich (trong `enrichment.py`) | T | M | 0.5h | ⬜ Chưa làm |
| 7.6 | `routers/export.py`: export CSV/JSON danh thiếp + hồ sơ DN (router riêng, không đụng `cards.py`) | T | S | 2h | ⬜ Chưa làm |
| 7.7 | `routers/stats.py` + `templates/dashboard.html`: tổng danh thiếp, đã xác nhận, số công ty, số hồ sơ, tỉ lệ cần review | T | S | 2.5h | ⬜ Chưa làm |
| 7.8 | Đo độ chính xác OCR trên 30 ảnh mẫu (do T chuẩn bị ở 3.9), so với `samples/expected.json` → `docs/accuracy.md` | Q | M | 2h | ⬜ Chưa làm |
| 7.9 | `templates/companies/detail.html`: danh sách người liên hệ từ danh thiếp + link ngược card ↔ company | T | M | 1.5h | ⬜ Chưa làm |
| 7.10 | Theo dõi tiến trình lập hồ sơ trên `templates/companies/list.html`: badge mỗi dòng (⏳ đang xử lý / ✅ xong / ❌ lỗi) poll `enrich-jobs/{job_id}` bằng HTMX, xong thì bấm vào xem hồ sơ; lỗi thì hiện lý do và cho chạy lại riêng dòng đó | T | M | 1.5h | ⬜ Chưa làm |

**Tiêu chí hoàn thành:** Truy vấn "công ty làm về logistics" trả về đúng chunk liên quan; báo cáo độ chính xác OCR đã có số liệu; tích chọn 3 công ty rồi bấm tạo hồ sơ thì badge từng dòng chạy đúng từ ⏳ sang ✅/❌ mà không cần tải lại trang.

---

## D8 — F3: Trợ lý AI hỏi–đáp

**Mục tiêu ngày:** Chat được với KB, trả lời kèm trích dẫn.

| # | Task | Người | Ưu tiên | Ước tính | Trạng thái |
|---|------|-------|---------|----------|--------|
| 8.1 | `prompts/assistant.py`: system prompt — chỉ trả lời dựa trên context, không biết thì nói không biết, luôn trích dẫn nguồn | Q | M | 1.5h | ⬜ Chưa làm |
| 8.2 | `routers/chat.py` — `POST /api/chat`: retrieve → build context → gọi Gemini Flash → trả `answer` + `citations` (hợp đồng JSON chốt ở họp D2) | Q | M | 3h | ⬜ Chưa làm |
| 8.3 | Lưu `chat_sessions` / `chat_messages`, hỗ trợ hội thoại nhiều lượt (đưa lịch sử vào prompt) | Q | M | 2h | ⬜ Chưa làm |
| 8.4 | `templates/assistant.html`: khung chat, câu trả lời + thẻ trích dẫn có link tới danh thiếp/hồ sơ DN, gợi ý câu hỏi mẫu | Q | M | 3h | ⬜ Chưa làm |
| 8.5 | Lọc theo metadata trong `retriever.py` (chỉ danh thiếp / chỉ hồ sơ DN / theo công ty cụ thể) | Q | S | 1.5h | ⬜ Chưa làm |
| 8.6 | Streaming câu trả lời (SSE) — *chỉ làm nếu D8 xong sớm; không kịp thì cắt (❌), không dời sang ngày dự phòng* | Q | C | 2h | ⬜ Chưa làm |
| 8.7 | `docs/qa-testset.md`: 10 câu hỏi kiểm thử + đáp án kỳ vọng | T | M | 1.5h | ⬜ Chưa làm |
| 8.8 | Rà soát UI/UX các trang F2 + dashboard, sửa lỗi hiển thị (chỉ file của T; cần đổi `base.html` → báo Q) | T | S | 2.5h | ⬜ Chưa làm |
| 8.9 | `tests/test_company_api.py`: test API companies (list, detail, patch profile) | T | S | 1.5h | ⬜ Chưa làm |
| 8.10 | ~~Gộp công ty trùng thủ công (chọn 2 công ty → gộp)~~ | T | C | 2h | ❌ Cắt — nhường giờ cho màn hình lập hồ sơ đối tác (5.8 + 6.5, ưu tiên M). `company_matching` ở 3.8 đã tự chống trùng nên đây chỉ là lưới an toàn thủ công |
| 8.11 | `tests/test_enrichment.py`: mock LLM, kiểm tra loại bỏ trường không nguồn & gắn `unverified` *(dời từ D5 để cân tải; enrichment vẫn được kiểm tay ở tiêu chí D5)* | T | M | 2h | ⬜ Chưa làm |

**Tiêu chí hoàn thành:** Chat trả lời đúng ≥ 7/10 câu trong `docs/qa-testset.md`, mỗi câu trả lời có trích dẫn bấm được.

---

## D9 — Hoàn thiện & gom Docker

**Mục tiêu ngày:** Một lệnh `docker compose up -d` chạy được toàn hệ thống trên máy sạch.

| # | Task | Người | Ưu tiên | Ước tính | Trạng thái |
|---|------|-------|---------|----------|--------|
| 9.1 | Hoàn thiện `docker-compose.yml`: healthcheck, `depends_on` có điều kiện, restart policy, gom biến môi trường vào `.env.example` | Q | M | 2h | ⬜ Chưa làm |
| 9.2 | Entrypoint tự chạy `alembic upgrade head` khi container api start; `scripts/seed.py` nạp dữ liệu mẫu; migration bổ sung index theo đề xuất của T (task 9.8) | Q | M | 2h | ⬜ Chưa làm |
| 9.3 | Kiểm tra toàn bộ luồng đa ngôn ngữ (Hàn/Nhật/Trung) và tinh chỉnh `prompts/ocr.py` theo lỗi thực tế | Q | M | 2.5h | ⬜ Chưa làm |
| 9.4 | Xử lý lỗi toàn cục: exception handler, thông báo lỗi thân thiện trên UI, màn hình khi chưa kết nối OAuth | Q | M | 2h | ⬜ Chưa làm |
| 9.5 | Logging middleware: request id, thời gian gọi LLM, token usage nếu có | Q | S | 1h | ⬜ Chưa làm |
| 9.6 | Mở rộng test enrichment (thêm case lỗi) + `tests/test_export.py` | T | M | 2.5h | ⬜ Chưa làm |
| 9.7 | Nâng chất lượng hồ sơ DN: bổ sung nguồn tra cứu chuyên biệt (cổng thông tin đăng ký DN), chấm điểm độ tin cậy từng trường | T | S | 2h | ⬜ Chưa làm |
| 9.8 | Rà soát truy vấn chậm phía F2 (N+1, thiếu index) → danh sách index đề xuất vào `docs/db-tuning.md` để Q tạo migration | T | S | 1h | ⬜ Chưa làm |
| 9.9 | Dọn code phần F2: bỏ code chết, thống nhất naming, thêm docstring cho service chính | T | S | 1h | ⬜ Chưa làm |
| 9.10 | Kiểm thử lại toàn bộ luồng F2 sau khi gom Docker (chạy từ volume rỗng) | T | M | 1.5h | ⬜ Chưa làm |

**Tiêu chí hoàn thành:** Xoá toàn bộ volume, chạy lại từ đầu bằng 1 lệnh → hệ thống hoạt động đầy đủ.
*Nếu tràn giờ:* cắt 9.5 và 9.7 (ưu tiên S) trước.

---

## D10 — Kiểm thử đầu–cuối & sửa lỗi

**Mục tiêu ngày:** Đóng hết bug chặn demo, có báo cáo kiểm thử.

| # | Task | Người | Ưu tiên | Ước tính | Trạng thái |
|---|------|-------|---------|----------|--------|
| 10.1 | Chạy kịch bản test nhóm F1 + F3, ghi bug vào `docs/bugs-f1-f3.md` (mức độ, nguyên nhân) | Q | M | 2h | ⬜ Chưa làm |
| 10.2 | Sửa bug nhóm F1 + F3 | Q | M | 3h | ⬜ Chưa làm |
| 10.3 | Test trường hợp biên: ảnh mờ, ảnh không phải danh thiếp, danh thiếp 2 mặt, file quá lớn, sai định dạng, mất kết nối OAuth / CLIProxy chết | Q | M | 2h | ⬜ Chưa làm |
| 10.4 | Chạy `pytest` toàn bộ, đảm bảo xanh | Q | M | 1h | ⬜ Chưa làm |
| 10.5 | `docs/test-scenarios.md`: 10 kịch bản test đầu–cuối (upload → review → confirm → enrich → chat → export) | T | M | 1.5h | ⬜ Chưa làm |
| 10.6 | Chạy kịch bản test nhóm F2, ghi bug vào `docs/bugs-f2.md`; bug thuộc F1/F3 thì báo Q tại daily | T | M | 2h | ⬜ Chưa làm |
| 10.7 | Sửa bug nhóm F2 | T | M | 3h | ⬜ Chưa làm |
| 10.8 | Đo lại độ chính xác OCR sau khi tinh chỉnh `prompts/ocr.py` (task 9.3), cập nhật `docs/accuracy.md` | Q | M | 1h | ⬜ Chưa làm |
| 10.9 | Đánh giá lại chất lượng hồ sơ DN sau khi tinh chỉnh prompt enrichment → `docs/profile-quality.md` (file riêng, tránh đụng `accuracy.md` của Q) | T | M | 1h | ⬜ Chưa làm |

**Tiêu chí hoàn thành:** Không còn bug mức Blocker/Critical trong cả hai file bug log; toàn bộ test xanh.

---

## D11 — Tài liệu & bàn giao demo

**Mục tiêu ngày:** Sản phẩm và tài liệu sẵn sàng trình bày.

| # | Task | Người | Ưu tiên | Ước tính | Trạng thái |
|---|------|-------|---------|----------|--------|
| 11.1 | Hoàn thiện `README.md`: kiến trúc, yêu cầu môi trường, cài đặt, chạy, cấu hình CLIProxy, troubleshooting | Q | M | 2.5h | ⬜ Chưa làm |
| 11.2 | Nạp dữ liệu demo sạch qua `scripts/seed.py` (10 danh thiếp đẹp + 5 hồ sơ DN sinh sẵn, lấy từ `samples/demo/` của T) | Q | M | 1.5h | ⬜ Chưa làm |
| 11.3 | Dọn repo, gắn tag `v1.0-demo`, kiểm tra không commit nhầm secret | Q | M | 1h | ⬜ Chưa làm |
| 11.4 | Ghi màn hình video demo dự phòng (phòng khi mạng/OAuth lỗi lúc trình bày) | Q | S | 1h | ⬜ Chưa làm |
| 11.5 | Sửa các điểm vấp thuộc module của Q sau tổng duyệt | Q | M | 1.5h | ⬜ Chưa làm |
| 11.6 | `docs/user-guide.md`: hướng dẫn sử dụng kèm ảnh chụp màn hình | T | M | 2.5h | ⬜ Chưa làm |
| 11.7 | `docs/demo-runbook.md`: kịch bản demo 10 phút (thứ tự thao tác, dữ liệu dùng, câu hỏi cho trợ lý AI) + chuẩn bị `samples/demo/` | T | M | 2h | ⬜ Chưa làm |
| 11.8 | Chủ trì tổng duyệt demo 2 lượt trên máy sạch, bấm giờ, ghi điểm vấp vào `docs/demo-runbook.md` và chia về đúng chủ module | T | M | 2h | ⬜ Chưa làm |
| 11.9 | Slide trình bày (bối cảnh → giải pháp → kiến trúc → demo → kết quả đo → hướng phát triển) | T | M | 2h | ⬜ Chưa làm |

**Tiêu chí hoàn thành:** Chạy demo hoàn chỉnh không vấp; README + user guide + runbook + slide + video dự phòng đã có.

---

## D12–D15 — Dự phòng (4 ngày)

> **Nguyên tắc: KHÔNG đặt trước công việc nào vào 4 ngày này.**
> Toàn bộ phạm vi đã biết của dự án nằm trọn trong D1–D11. Bốn ngày dự phòng chỉ dùng cho
> việc **phát sinh trong lúc chạy dự án**. Nếu D1–D11 chạy đúng kế hoạch và không có gì phát
> sinh, D12–D15 dùng để kiểm thử thêm và giữ sản phẩm ở trạng thái ổn định — không tự ý mở
> thêm phạm vi.

### Ba loại việc được phép dùng ngày dự phòng

| Loại | Nội dung | Điều kiện |
|------|----------|-----------|
| **1. Kiểm tra** | Kiểm thử hồi quy toàn hệ thống, test lại các luồng đã sửa, thử trên máy sạch, tổng duyệt demo | Luôn được, kể cả khi không có bug |
| **2. Sửa lỗi** | Bug phát hiện ở D10–D11 chưa đóng kịp, bug mới phát hiện trong lúc kiểm thử hồi quy | Bug phải có trong `docs/bugs-f1-f3.md` hoặc `docs/bugs-f2.md` |
| **3. Chức năng mới phát sinh** | Yêu cầu mới từ người hướng dẫn/khách hàng xuất hiện sau khi kế hoạch đã chốt | Hai thành viên thống nhất + ghi vào bảng dưới trước khi bắt tay làm |

Nếu một giai đoạn D1–D11 bị trễ, phần trễ được **kéo sang ngày dự phòng gần nhất** và ghi vào
bảng bên dưới như một mục phát sinh, kèm lý do trễ — không được lên lịch trước điều này.
Việc phát sinh vẫn tuân thủ quy ước chống xung đột: **mỗi mục đúng 1 owner**, và giao cho **chủ sở hữu file liên quan**.

### Nhật ký sử dụng ngày dự phòng *(điền khi thực tế phát sinh)*

| Ngày | Loại (Kiểm tra / Sửa lỗi / Phát sinh) | Nội dung | Người | Trạng thái |
|------|---------------------------------------|----------|-------|------------|
| D12 | — | *(chưa phát sinh)* | — | ⬜ Chưa dùng |
| D13 | — | *(chưa phát sinh)* | — | ⬜ Chưa dùng |
| D14 | — | *(chưa phát sinh)* | — | ⬜ Chưa dùng |
| D15 | — | *(chưa phát sinh)* | — | ⬜ Chưa dùng |

### Việc mặc định khi không có gì phát sinh

Không phải task bắt buộc, chỉ là việc lấp chỗ trống để giữ chất lượng — không tính vào phạm vi:

- Chạy lại toàn bộ 10 kịch bản trong `docs/test-scenarios.md` trên máy sạch.
- Tổng duyệt demo thêm 1 lượt, bấm giờ.
- Đảm bảo `main` luôn ở trạng thái chạy được: `docker compose up -d` từ volume rỗng.

---

## Bảng tổng hợp phân bổ công việc

| Ngày | Quân (Q) | Tùng (T) |
|------|----------|----------|
| D1 | Repo, skeleton + router stub, `base.html`, Docker, ERD, migration khởi tạo | Biên bản phạm vi, API spec, khảo sát CLIProxy, bảng sở hữu file, ảnh mẫu |
| D2 | Service cliproxy, `cliproxy_client.py`, `llm.py`, router integration, trang settings + **nút OAuth** | Chốt model embedding + spike web search (2 ADR), schema hồ sơ DN, prompt enrichment |
| D3 | Upload + tiền xử lý ảnh + prompt OCR + `ocr.py` + lưu DB + `normalize.py` (chuẩn hoá SĐT/email) | `normalize_company.py` (chuẩn hoá tên công ty), `company_matching.py`, bộ ảnh mẫu, **service `embedder`** |
| D4 | API danh sách/chi tiết/confirm (không auto-enrich) + trang list & upload, test normalize | Repository company, `enrichment.py`, validate nguồn, test normalize tên công ty |
| D5 | Trang review danh thiếp, batch upload + trang tiến trình | API companies (enrich đơn + **enrich-batch** + job tiến trình), xử lý lỗi enrich |
| D6 | conftest + test OCR, `kb.py`, repo `kb_chunks`, client `embeddings.py` + reindex | **Màn hình lập hồ sơ đối tác (tích chọn nhiều + nút tạo)**, trang chi tiết công ty, sửa hồ sơ tay |
| D7 | `retriever.py`, hybrid search, auto-ingest phía card, test retrieval, đo độ chính xác OCR | Gọi ingest ở enrich, export, dashboard, danh sách liên hệ, **badge tiến trình lập hồ sơ** |
| D8 | Prompt trợ lý, API chat, lịch sử hội thoại, trang assistant, lọc metadata, (SSE – C) | Bộ câu hỏi kiểm thử, rà soát UI F2, test API companies, test enrichment *(gộp công ty đã cắt)* |
| D9 | Hoàn thiện Docker, entrypoint + seed, đa ngôn ngữ, xử lý lỗi, logging | Mở rộng test, nâng chất lượng hồ sơ DN, đề xuất index, dọn code F2, kiểm thử lại F2 |
| D10 | Chạy & sửa bug F1/F3, test biên, pytest xanh, đo lại độ chính xác OCR | Kịch bản test, chạy & sửa bug F2, đánh giá lại chất lượng hồ sơ DN |
| D11 | README, seed dữ liệu demo, tag release, video, sửa điểm vấp phần Q | User guide, runbook + dữ liệu demo, chủ trì tổng duyệt, slide |
| D12–D15 | *Không có task đặt trước* — kiểm thử hồi quy, sửa bug & việc phát sinh | *Không có task đặt trước* — kiểm thử hồi quy, sửa bug & việc phát sinh |
