# Kịch bản kiểm thử đầu–cuối

> Chủ sở hữu: **T** · Task: **10.5** · Chạy ở: **10.6** (T chạy phần F2) và **10.1** (Q chạy phần F1/F3)
> Cập nhật: 2026-09-21

## 1. Nguyên tắc

- Mỗi kịch bản gắn với **tiêu chí nghiệm thu** A1–A7 trong `Plan.md` mục 8. Đọc bảng kết quả là biết ngay tiêu chí nào
  đạt, tiêu chí nào chưa.
- Dữ liệu dùng chung một bộ cố định để chạy lại được và so được giữa các lần:
  - **Ảnh danh thiếp demo**: `samples/demo/*.png` (7 thẻ, Việt / Anh / Nhật / Hàn). Công ty có thật để tra cứu ra
    nguồn, người và số điện thoại bịa, email `*.example.com`. Dữ liệu kỳ vọng từng thẻ nằm ở `samples/demo/cards.json`.
  - **Bộ dữ liệu cho trợ lý AI**: `python -m scripts.seed` (Q, 9.2) — 4 thẻ + 3 hồ sơ hư cấu mà
    `docs/qa-testset.md` chấm điểm.
- Mỗi kịch bản có **đường lỗi**, không chỉ đường thành công. Lỗi người dùng gặp thật (CLIProxy chết, F5 giữa chừng,
  bấm nhầm) đáng kiểm hơn một lần bấm trơn tru.
- Bug tìm được ghi vào `docs/bugs-f2.md` (T) hoặc báo Q ghi vào `docs/bugs-f1-f3.md` (quy ước số 7).

## 2. Chuẩn bị chung

```bash
docker compose build
docker compose up -d
docker compose exec api python -m scripts.seed
```

Mở `http://localhost:8000/settings`, kết nối OAuth bằng **tài khoản Gmail cá nhân** (xem TS-02) rồi bấm
*Kiểm tra kết nối*.

## 3. Mười kịch bản

| # | Kịch bản | Tiêu chí | Module |
|---|----------|----------|--------|
| TS-01 | Khởi động trên máy sạch | A1 | Q |
| TS-02 | Kết nối OAuth và trạng thái kết nối | A2 | Q |
| TS-03 | Quét một thẻ tiếng Việt → review → xác nhận | A3 | Q (+ T: gắn công ty) |
| TS-04 | Upload hàng loạt thẻ đa ngôn ngữ | A3, A4 | Q |
| TS-05 | Xác nhận thẻ **không** tự sinh hồ sơ | A5b | T |
| TS-06 | Chống trùng công ty khi xác nhận | R6 | T |
| TS-07 | Tạo hồ sơ hàng loạt 3 công ty, theo dõi tiến trình | A5, A5b | T |
| TS-08 | Huỷ lượt tạo hồ sơ, lỗi CLIProxy, chạy lại | A5 | T |
| TS-09 | Sửa tay, ẩn và hiện lại hồ sơ | A5, R4 | T |
| TS-10 | Trợ lý AI, export và dashboard | A6 | Q (chat) + T (export, dashboard) |

### TS-01 — Khởi động trên máy sạch (A1)

1. `docker compose down -v` (⚠️ xoá cả volume `cliproxy_auths` → mất token OAuth, phải kết nối lại ở TS-02).
2. `docker compose build` rồi `docker compose up -d`.
3. Mở `http://localhost:8000/health`.

**Đạt:** `{"status":"ok"}`; `docker compose ps` cho thấy `db`, `api`, `embedder`, `cliproxy` đều chạy;
`docker compose exec api alembic current` là **head** mà không phải tự chạy `alembic upgrade`.
**Đường lỗi:** ngắt mạng rồi `docker compose up -d` lại — `embedder` vẫn phải lên (model nhúng sẵn trong image).

### TS-02 — Kết nối OAuth (A2)

1. `/settings` → *Kết nối CLIProxy (OAuth)* → đăng nhập **Gmail cá nhân** → đồng ý.
2. Badge chuyển *Đã kết nối*, hiện tài khoản và danh sách model. Bấm *Kiểm tra kết nối*.

**Đạt:** gọi thử trả về câu trả lời của model.
**Đường lỗi đã gặp (2026-09-18):** tài khoản Google Workspace (ví dụ đuôi `@fpt.edu.vn`) được xếp gói `standard-tier`,
CLIProxy không lấy được `project_id` → mọi lời gọi trả `400 antigravity auth missing project_id`, **trong khi badge
vẫn báo *Đã kết nối***. Kỳ vọng: người dùng đọc được lý do ở kết quả *Kiểm tra kết nối*. Badge báo sai là issue của
Q (xem ghi chú cuối bài).

### TS-03 — Quét một thẻ tiếng Việt (A3)

1. `/cards/upload` → chọn `samples/demo/vi-01-clear.png`.
2. Màn hình review: so từng ô với `samples/demo/cards.json` (7 trường bắt buộc: họ tên, chức vụ, công ty, email, SĐT,
   địa chỉ, ngày upload).
3. Bấm *Xác nhận*.

**Đạt:** đủ 7/7 trường đúng; SĐT chuẩn hoá thành `+84900000001`; website có `https://`; thẻ chuyển *Đã xác nhận*
và gắn vào công ty mới *CÔNG TY CỔ PHẦN SỮA VIỆT NAM*.
**Đường lỗi:** upload lại **đúng ảnh đó** → không sinh bản ghi thứ hai (chống trùng theo `image_hash`).

### TS-04 — Upload hàng loạt đa ngôn ngữ (A3, A4)

1. `/cards/batch` → chọn 6 ảnh còn lại trong `samples/demo/` cùng lúc.
2. Chờ tất cả xong, mở review từng thẻ, xác nhận.

**Đạt:** mỗi thẻ đúng `language_detected` (`en` / `ja` / `ko`); tên Nhật `山田 花子`, tên Hàn `김민수` không vỡ chữ;
không quá số luồng giới hạn chạy cùng lúc.
**Đường lỗi:** thêm vào lượt một file không phải ảnh → chỉ đúng dòng đó báo lỗi, các ảnh khác vẫn chạy.

### TS-05 — Xác nhận thẻ không tự sinh hồ sơ (A5b)

1. Sau TS-03 và TS-04, mở `/companies`.

**Đạt:** mọi công ty mới đều *Chưa có hồ sơ*; `enrich_jobs` không có job nào được tạo tự động; dashboard *Hồ sơ doanh
nghiệp* = 0 (ngoài các hồ sơ của bộ seed).

### TS-06 — Chống trùng công ty khi xác nhận (R6)

1. Tìm *hoa phat* trên `/companies`.
2. Mở công ty *Samsung Electronics Vietnam* rồi *삼성전자*.

**Đạt:**
- `vi-02` (*Công ty Cổ phần Tập đoàn Hòa Phát*) và `en-03` (*Hoa Phat Group JSC*) về **một** công ty duy nhất có
  2 danh thiếp, tên còn lại nằm trong *tên khác* (khoá chuẩn hoá `hoa phat`, B2-02).
- *Samsung Electronics Vietnam* và *삼성전자* là **hai** công ty (hai pháp nhân), mỗi trang chi tiết có khối
  *"Cùng tên miền email / website với: … (samsung.com)"* trỏ sang công ty kia — gợi ý, không tự gộp.

### TS-07 — Tạo hồ sơ hàng loạt (A5, A5b)

1. `/companies` → tích *Sữa Việt Nam*, *Hòa Phát*, *Coteccons* → *Lập hồ sơ*.
2. Giữa chừng bấm **F5**.
3. Khi xong, mở từng hồ sơ.

**Đạt:**
- Badge từng dòng đi đúng *⏳ Đang chờ → ⏳ Đang tạo… → ✅ Xong · N trường có nguồn*, không quá 2 công ty chạy cùng
  lúc; sau F5 vẫn tiếp tục theo dõi.
- Mỗi hồ sơ **≥ 5 trường có nguồn**, trong đó có MST, quy mô, ngành nghề, sản phẩm, địa chỉ (A5).
- Khối *Nguồn tham khảo* ghi đúng trường nào lấy từ trang nào, bấm mở được trang gốc.

**Số đo tham chiếu:** `docs/profile-quality.md` (10.9) — thời gian mỗi công ty và tỉ lệ đạt A5 theo model.

### TS-08 — Huỷ, lỗi CLIProxy, chạy lại (A5)

1. Tích *Hitachi* → *Tạo hồ sơ* → ngay khi badge là *⏳ Đang tạo…*, bấm **Huỷ** trên dòng đó.
2. `docker compose stop cliproxy` → tạo hồ sơ cho *Samsung Electronics Vietnam*.
3. `docker compose start cliproxy` → bấm **Chạy lại** trên dòng đó.

**Đạt:**
1. Badge *⊘ Đã huỷ* ngay lập tức, công ty vẫn *Chưa có hồ sơ* (không có hồ sơ nửa vời).
2. Sau các lượt thử lại, badge *❌ Lỗi* kèm lý do đọc được, không treo mãi *Đang tạo…*.
3. Chạy lại thì *✅ Xong*.

**Đường lỗi:** huỷ khi đang **tạo lại** một hồ sơ đã có → hồ sơ cũ **còn nguyên**.

### TS-09 — Sửa tay, ẩn, hiện lại hồ sơ (A5, R4)

1. Hồ sơ *Sữa Việt Nam* → *Sửa hồ sơ* → đổi *Quy mô* → *Lưu*.
2. *Ẩn hồ sơ* → xác nhận.
3. Về `/companies` tìm *sua viet nam*; đổi bộ lọc sang *Đã ẩn*.
4. Hỏi trợ lý AI *"Vinamilk sản xuất gì?"*.
5. Quay lại hồ sơ → *Hiện lại*.

**Đạt:**
1. Hồ sơ chuyển *Đã kiểm*; nguồn của đúng trường *Quy mô* bị gỡ, nguồn các trường khác còn nguyên.
2. Dải thông báo *"Hồ sơ này đang ẩn"*; nút *Sửa* bị giấu.
3. Không tìm thấy ở danh sách mặc định; có ở bộ lọc *Đã ẩn*.
4. Trợ lý **không** trả lời bằng hồ sơ đã ẩn.
5. Hồ sơ trở lại *Đã kiểm* (vì đã có trường sửa tay) và trợ lý trả lời lại được.

### TS-10 — Trợ lý AI, export, dashboard (A6)

1. `/assistant` → chạy **10 câu tính điểm + 3 câu ngoài phạm vi** trong `docs/qa-testset.md`, ghi kết quả theo mẫu ở
   mục 8 của file đó. ⚠️ **Chạy bước này trước TS-07**, hoặc sau khi chạy `samples/demo/reset_demo.sql`: câu chặn
   **X3** (*"Mã số thuế của Vinamilk là gì?"*) chỉ là câu ngoài phạm vi khi KB **chưa** có hồ sơ Vinamilk. Sau TS-07
   thì hồ sơ đó có thật trong KB, trợ lý trả lời đúng là hành vi đúng, và câu X3 không còn kiểm được chống bịa.
2. Hỏi thêm *"Coteccons có mã số thuế là gì?"* (hồ sơ tạo ở TS-07).
3. Tải `/api/export/cards.csv` và `/api/export/companies.csv`, mở bằng Excel.
4. Mở `/` (trang chủ), bấm từng ô số liệu.

**Đạt:**
1. ≥ 8/10 câu đạt, 3/3 câu ngoài phạm vi từ chối trả lời (A6).
2. Trả lời đúng MST, trích dẫn trỏ về `/companies/{id}`.
3. Excel hiện đúng tiếng Việt, 山田 花子, 김민수 (CSV có BOM); cột `sources` là JSON đọc được.
4. Số trên dashboard khớp danh sách; bấm ô *Hồ sơ doanh nghiệp* ra danh sách đã lọc sẵn.

## 4. Ghi kết quả

Chép bảng này xuống cuối file mỗi lượt chạy.

```
Ngày: ____  ·  Người chạy: ____  ·  Commit: ____  ·  LLM_MODEL: ____

| TS    | Đạt? | Thời gian | Bug / ghi chú |
|-------|------|-----------|---------------|
| TS-01 |      |           |               |
| ...   |      |           |               |
| TS-10 |      |           |               |
```

## 5. Ghi chú cho Q

- **TS-02**: badge *Đã kết nối* chỉ kiểm có file token, nên tài khoản thiếu `project_id` vẫn hiện xanh. Đề xuất:
  badge dựa trên kết quả một lời gọi thử gần nhất, hoặc ít nhất hiện cảnh báo khi lời gọi thử gần nhất lỗi.
- **TS-03**: số điện thoại mã vùng cũ (I-29) và điểm tin cậy luôn `0.95` (I-28) sẽ lộ rõ khi quét thẻ thật, không lộ
  với bộ thẻ demo (số di động, ảnh rõ).

## 6. Kết quả

### Lượt 1 — 2026-09-21 (T, tổng duyệt kỹ thuật 11.8)

Máy dev của T · commit `61e1385` · `LLM_MODEL=gemini-3.6-flash-high` · chạy qua đúng các API mà giao diện gọi, có bấm
giờ; giao diện đã kiểm riêng trên trình duyệt ở 8.8 và 10.7.

| TS | Đạt? | Thời gian | Bug / ghi chú |
|----|------|-----------|---------------|
| TS-01 | — | — | Chưa chạy: cần máy sạch, làm ở lượt 2 của 11.8 |
| TS-02 | — | — | Chưa chạy lại; lỗi `project_id` với tài khoản Workspace đã gặp 2026-09-18, ghi ở `docs/user-guide.md` mục 8 |
| TS-03 | ✅ | 10 s quét | Đủ trường; ô điện thoại phụ trống vẫn bị tô vàng (I-28, Q) |
| TS-04 | ✅ | 33 s / 6 thẻ | Anh, Nhật, Hàn đều đúng |
| TS-05 | ✅ | — | Mọi công ty mới *Chưa có hồ sơ* sau khi xác nhận |
| TS-06 | ✅ | — | *Hòa Phát* 1 công ty 2 thẻ; Samsung VN ↔ 삼성전자 hai công ty, gợi ý *cùng tên miền* |
| TS-07 | ✅ | 68 s / 3 công ty | 9 / 11 / 10 trường có nguồn |
| TS-08 | ✅ | 60 s báo lỗi · 21 s chạy lại | Huỷ tức thì; báo lỗi chậm → **B2-05** (Minor) |
| TS-09 | ✅ | — | Ẩn: trợ lý không dùng hồ sơ; hiện lại: dùng lại |
| TS-10 | ✅ | 110 s / 15 câu | `qa-testset.md` **10/10**, ngoài phạm vi 3/3, nhiều lượt đạt; CSV có BOM |

**8/10 kịch bản đạt, 0 trượt, 2 chưa chạy** (cần máy sạch). Không có bug Blocker/Critical.
