# Nhật ký bug — nhóm F1 (OCR danh thiếp) & F3 (Trợ lý AI / RAG)

> Chủ sở hữu: **Q** · Task: **10.1** (ghi) / **10.2** (sửa) · Ngày kiểm: **2026-09-21**
> Bug nhóm **F2** nằm ở [`bugs-f2.md`](./bugs-f2.md) — T giữ. Ai phát hiện bug thuộc module của
> người kia thì báo chủ module tại daily, **không** ghi chéo sang file của nhau (quy ước số 7).

## Cách kiểm

Chạy trên hệ thật đang chạy trong Docker, OAuth đã kết nối (`quanpyke@gmail.com`), model
`gemini-3-flash`, embedding `intfloat/multilingual-e5-small`.

| Bộ | Lệnh | Phủ gì |
|----|------|--------|
| Luồng F1 + F3 | `python -m scripts.e2e_f1_f3 --suite flow` | 16 ca: upload → danh sách → review → sửa → xác nhận → KB → hỏi đáp → xoá |
| Trường hợp biên | `python -m scripts.e2e_f1_f3 --suite edge` | 10 ca: ảnh mờ, ảnh không phải danh thiếp, thẻ 2 mặt, file quá lớn / sai định dạng / rỗng, id sai |
| Cắt dịch vụ | `python -m scripts.e2e_f1_f3 --suite outage` | 2 ca: CLIProxy chết (mất OAuth), embedder chết — script tự `docker compose stop/start` |
| Chấm trợ lý AI | `python -m scripts.eval_assistant` | 10 câu tính điểm + 3 câu chặn + cặp nhiều lượt của `docs/qa-testset.md` |
| Độ chính xác OCR | `python -m scripts.check_multilang_ocr --blur 4.0` | 5 ngôn ngữ × (sắc nét + nhoè) — số đo ghi ở [`accuracy.md`](./accuracy.md) |
| Đơn vị / tích hợp | `pytest` | 416 test, có Postgres thật |

## Mức độ

| Mức | Nghĩa | Ràng buộc |
|-----|-------|-----------|
| **Blocker** | Không demo được chức năng đó | Tiêu chí D10: phải đóng hết |
| **Critical** | Chạy được nhưng cho ra dữ liệu sai mà người dùng không nhận ra | Tiêu chí D10: phải đóng hết |
| **Major** | Làm trượt một tiêu chí nghiệm thu, hoặc người dùng phải lách | Đóng nếu kịp |
| **Minor** | Khó chịu, không ảnh hưởng kết quả | Ghi lại, có thể để sau |

---

## Bảng bug

| Mã | Mức | Vấn đề | Nguyên nhân | Trạng thái |
|----|-----|--------|-------------|------------|
| **B-01** | Minor | `scripts/check_multilang_ocr.py` chết ngay dòng in đầu tiên khi stdout không phải console (`... \| tail`, `> log.txt`, CI) — chưa gọi API lần nào | Windows đặt stdout về **cp1252** khi không phải console; chữ `đ` trong "đã dựng ảnh" là `UnicodeEncodeError`. `.claude/hooks/*.py` đã gặp và xử lý đúng lỗi này từ trước, script không chép lại | ✅ Sửa 2026-09-21 — `sys.stdout.reconfigure(encoding="utf-8")` ở đầu cả ba script `scripts/*` của D10 |
| **B-02** | Minor | Đo lại lần hai trên cùng bộ ảnh thì cả 5 thẻ báo `LỖI: HTTP 200` | Chống trùng của 3.1 trả **200 + `duplicate:true`** và cố tình không gọi lại model. Đúng cho người dùng, **sai cho phép đo**: chấm trên bản cũ nghĩa là tưởng đang đo prompt hiện tại trong khi đọc lại kết quả lượt trước | ✅ Sửa 2026-09-21 — gặp ảnh trùng thì **xoá bản cũ rồi quét lại**, không chấm bản cũ |
| **B-03** | **Major** | Câu 8 của `docs/qa-testset.md` — *"Có ai làm ở công ty Nhật không?"* — trả "Không có thông tin này trong dữ liệu đã nhập", dù KB có đủ hai thẻ Nhật. Làm **trượt tiêu chí A6** và là câu duy nhất kiểm tiêu chí **A4** (đa ngôn ngữ) qua đường hỏi–đáp | **Không phải lỗi truy hồi** — đo trực tiếp `retriever.search()`: thẻ `田中 太郎 · 東京テック株式会社` xếp **hạng 1**. Lỗi ở **nội dung chunk**: `services/kb.py` ghi `Ngôn ngữ: ja`, không chỗ nào trong ngữ cảnh nói thẻ này là *tiếng Nhật*. Muốn trả lời thì model phải tự biết `東京テック株式会社` là công ty Nhật — đúng thứ **quy tắc 1** của `prompts/assistant.py` cấm. Model làm đúng luật, dữ liệu sai | ✅ Sửa 2026-09-21 — `LANGUAGE_LABELS` trong `services/kb.py`, chunk nay ghi `Ngôn ngữ: Tiếng Nhật (ja)`. **Cố ý không nới quy tắc 1**: nới ra là mở lại đường cho câu chặn X3 ("mã số thuế của Vinamilk"), mà trượt một câu chặn thì cả lượt nghiệm thu bỏ đi. 5 test mới ở `tests/test_rag_kb.py` |
| **B-04** | **Critical** | `POST /api/cards/{id}/confirm` không truyền `email`/`website` xuống `company_matching.upsert_company()`, nên quy tắc gộp công ty theo tên miền **chưa bao giờ chạy từ giao diện** (đã ghi là **I-18** từ D4) | `routers/cards.py::_upsert_company()` gọi `upsert(db, raw_name)`. `extract_domains(None, None)` trả rỗng → chỉ còn so tên. Cắt cả hai chiều: "Công ty TNHH Phú Cơ" gặp "Công ty TNHH Phú" **bị gộp** (I-21), còn hai bản ghi cùng tên miền thì **không gộp**. Gộp nhầm là hỏng im lặng — task gộp tay 8.10 đã cắt nên không sửa lại được | ✅ Sửa 2026-09-21 — truyền đủ `email=` / `website=`. 3 test mới ở `tests/test_card_api.py` bám vào **chữ ký lời gọi**, không bám kết quả gộp (kết quả là hành vi trong file của T) |
| **B-05** | **Major** | `tests/test_card_api.py` và `tests/test_rag.py` vẫn là **stub 6 dòng** từ D1. Toàn bộ API danh thiếp (3.1, 4.1, 4.2, 4.3, 5.2) không có test trực tiếp nào — chỉ được chạm gián tiếp trong `tests/test_rag_retrieval.py` mục 7.3 | Sót khi chia việc: 6.1 dựng hạ tầng test và các task sau viết test vào file *theo chủ đề* (`test_ocr.py`, `test_rag_*.py`), không ai quay lại hai file stub. Hệ quả: hồi quy ở F1 chỉ bắt được bằng bộ E2E của 10.1 — mà bộ đó cần Docker + model thật nên **CI không chạy được** | 🔄 Vá một phần 2026-09-21 — `tests/test_card_api.py` nay có **9 test** phủ đúng phần 10.2 vừa sửa + các đường 4xx. **Chưa phủ** upload/batch/ảnh (cần dựng ảnh + mock vision). `tests/test_rag.py` vẫn stub. Đề xuất: gộp việc này vào D12–D15 nếu còn giờ |
| **B-06** | **Major** | Bấm ô *Đã xác nhận* / *Chờ duyệt* trên `/dashboard` sang `/cards?status=confirmed` thì ra **nguyên danh sách đầy đủ** — người dùng kết luận bộ lọc hỏng (đã ghi là **I-27** từ D7) | `templates/cards/list.html` chỉ đọc bộ lọc từ ô `#f-status` lúc khởi tạo, **không đọc `location.search`**. Phía T đã sửa đúng lỗi này cho `/companies` ở 8.8 | ✅ Sửa 2026-09-21 — đọc `q` / `status` / `language` từ URL lúc khởi tạo, **chỉ nhận giá trị có thật trong ô chọn** (`?status=xoa-so` mà nhét thẳng vào thì API trả 400 và trang báo lỗi cho một thứ người dùng không gõ), kèm `history.replaceState` giữ URL khớp bộ lọc đang xem |
| **B-07** | Minor | `/dashboard` không có lối vào nào từ thanh nav — phải bấm nút trên `/companies` hoặc gõ thẳng URL, dù `docs/api.md` mục 7 liệt kê nó là trang chính thức (đã ghi là **I-26** từ D7) | `templates/base.html` khai `nav_items` cố định và là **file của Q**, nên 7.7 của T không tự thêm được (quy ước số 2) | ✅ Sửa 2026-09-21 — thêm mục *Bảng số liệu*; khoá `dashboard` khớp `active_nav` mà `routers/stats.py` truyền xuống |
| **B-08** | Minor | Xác nhận danh thiếp tạo bản ghi trong `companies`, nhưng **xoá danh thiếp không dọn công ty mồ côi** và **không có endpoint nào xoá công ty**. Sau một lượt chạy E2E, `/companies` còn lại "CÔNG TY CỔ PHẦN LOGISTICS HẢI ĐĂNG" không gắn với danh thiếp hay hồ sơ nào | Họ hàng với **I-25** (chunk mồ côi). Phần danh thiếp của I-25 **đã đóng**: `DELETE /api/cards/{id}` gỡ chunk trong cùng transaction, ca **F3-07** đo được là trợ lý không còn trích dẫn thẻ đã xoá. Phần còn lại chỉ chạm tới được bằng SQL tay vì `routers/companies.py` (của T) không có route xoá | ⬜ Chưa sửa — **cố ý**. Thêm một endpoint xoá công ty là mở phạm vi mới vào ngày kiểm thử, mà bảng `companies` lại là file của T. Dọn tay trước khi demo: `DELETE FROM companies c WHERE NOT EXISTS (…business_cards…) AND NOT EXISTS (…company_profiles…)`. Báo T tại daily D11 |
| **B-09** | Minor | DB dev có **5 bản ghi trùng** của cùng một người (`山田 太郎` ×5, `Trần Thị Bích Ngọc` ×3) chiếm chỗ trong top-k truy hồi (đã ghi là **I-24**, tái hiện lần thứ tư) | Cùng một danh thiếp quét lại từ nhiều file ảnh khác nhau → `image_hash` khác nhau → chống trùng của 3.1 (băm **file gốc**) không bắt được | ✅ Xử lý 2026-09-21 theo **hướng (b)** đã nghiêng từ D8: coi là rác dữ liệu thử, dọn sạch trước demo (giữ 1 bản mỗi người), **không** lọc trùng trong `retriever._fuse()`. Lý do chốt: lọc ở tầng truy hồi giấu mất vấn đề dữ liệu, và `scripts/seed.py --reset` ở 11.2 vốn đã nạp lại bộ sạch |
| **B-10** | Minor | Câu hỏi tiếng Việt **gõ không dấu** kiểu mô tả trượt cả hai nhánh: `cong ty nao san xuat sua` → "không có thông tin", trong khi đúng câu đó viết có dấu ra hạng 1 (đã ghi là **I-23** từ D7) | `to_tsvector('simple', …)` **không bỏ dấu tiếng Việt** nên không token nào trùng; `multilingual-e5-small` không kéo "san xuat sua" về gần "sản xuất sữa". Lưu ý: câu hỏi không dấu chứa **tên riêng** thì vẫn ra (`Sua Moc Chau o dau` → đúng) — chỉ câu mô tả mới trượt | ⬜ Chưa sửa — **quyết định đã chốt 2026-09-21**. I-23 ghi rõ "chờ số đo A6 thật rồi quyết: trượt A6 vì lý do này thì làm, không thì để nguyên". A6 đo được **10/10 không cần nó**, nên giữ nguyên và ghi vào hạn chế đã biết. Sửa đúng cách cần extension `unaccent` + một hàm IMMUTABLE bọc lại để dùng được trong index biểu thức + một Alembic revision — không phải việc nên làm trong ngày kiểm thử |
| **B-11** | Minor | Truy vấn **một từ là tên riêng** (`Tanaka`, `Vinamilk`) cho `query_terms()` ra **danh sách rỗng**, nên nhánh full-text không chạy — mất phần bổ trợ mà 7.2 dựng ra cho đúng loại truy vấn này | `retriever.query_terms()` bỏ từ ở **vị trí 0** vì "ai cũng viết hoa chữ đầu câu". Với câu văn xuôi thì đúng; với truy vấn một từ thì từ duy nhất luôn ở vị trí 0. Chữ CJK cũng ra rỗng vì không có khái niệm hoa/thường | ⬜ Chưa sửa — Minor. Nhánh vector vẫn kéo đúng nguồn về (đo: `Tanaka` và `田中 太郎` đều ra thẻ đúng ở **hạng 1**), nên không có triệu chứng nào lộ ra cho người dùng. Nới quy tắc này mà không đo lại recall là đi thẳng vào kiểu hỏng mà chính docstring của `query_terms()` cảnh báo: nhánh full-text kéo về gần hết KB |
| **B-12** | Minor | Ô *Điện thoại phụ* **trống vì thẻ không in số phụ** vẫn hiện "độ tin cậy 0%", bị tô vàng và đếm vào banner *"Cần kiểm: 1 trường"* — báo động giả trên **7/7 thẻ demo**. T ghi lại ở lượt tổng duyệt 1 (`docs/demo-runbook.md` mục 6), chia về Q | Không phải model chấm sai mà là màn hình đọc sai hợp đồng của chính dự án: quy tắc 5 của `app/prompts/ocr.py` định nghĩa **`confidence = 0.0` nghĩa là "trường để `null`"**, còn `templates/cards/detail.html` lại đối xử với nó y như "đọc chữ không chắc". Hai chuyện khác hẳn nhau: một bên là *trên thẻ không có gì để đọc*, bên kia là *có chữ nhưng máy không chắc* | ✅ Sửa 2026-09-21 (task **11.5**) — ô trống thì bỏ qua điểm tin cậy: không chip, không tô vàng, không đếm vào banner. Cảnh báo *"Trường bắt buộc còn trống"* nằm ở nhánh khác nên **không mất cảnh báo nào**. Kiểm bằng cách trích đúng khối mã trong template rồi chạy trên 4 ca (trống+0.0 · có chữ+0.4 · có chữ+1.0 · trống+không điểm): **4/4 đạt** |

**Trạng thái theo tiêu chí hoàn thành D10:** không còn bug mức **Blocker** hay **Critical** nào mở.
B-04 (Critical) đã đóng. Bốn dòng còn mở đều là **Minor**, mỗi dòng có lý do để mở và việc phải làm tiếp.

---

## Ghi nhận: **không** phải bug

Bốn lần bộ kiểm thử báo đỏ mà lỗi nằm ở chính bài test hoặc ở kỳ vọng. Ghi lại vì lần sau
gặp lại sẽ lại tưởng là bug, và vì một bài test đòi sai còn nguy hiểm hơn không có bài test.

| # | Triệu chứng | Sự thật |
|---|-------------|---------|
| N-01 | Ca **E-02** báo `is_business_card=None` cho ảnh phong cảnh | Bài test đọc `ocr_raw_json` từ response của `POST /upload`, nhưng trường đó chỉ có trong `CardDetailOut` (`GET /api/cards/{id}`), không có trong `CardOut`. Ứng dụng trả **đúng** `is_business_card: false`, mọi trường `null`, kèm `notes` giải thích. Đã sửa bài test |
| N-02 | Ca **O-01** báo "CLIProxy đã chết mà badge vẫn xanh: connected=True" | Hợp đồng ghi ngay đầu `routers/integration.py` mục 2: **CLIProxy chết ≠ đã ngắt kết nối** — token nằm trong volume `cliproxy_auths`, container chết không làm mất token. `connected` giữ giá trị cache, `reachable` mới là thứ tụt xuống `false`, và `templates/settings.html::refreshStatus()` đọc đúng `reachable` để vẽ badge xám *"Không gọi được CLIProxy"*. Bài test đòi sai. Đã sửa thành kiểm `reachable=false` + `from_cache=true` |
| N-03 | Câu 1 và 10 của bộ chấm A6 báo "nguồn P1 không có trong DB" | `đ`/`Đ` **không phải** chữ `d` cộng dấu tổ hợp mà là ký tự riêng (U+0111 / U+0110), nên `unicodedata.normalize("NFD", …)` không bóc ra được: "Đại Việt" bỏ dấu kiểu ngây thơ vẫn ra `đai viet`, không khớp `dai viet`. Trợ lý trả lời **đúng** cả hai câu. Đã sửa bộ chấm. Cùng họ với B-10 |
| N-04 | Thẻ Nhật in `TEL: 03-…` trước, `携帯: 090-…` sau, nhưng `phone` nhận **số di động** (số in sau) | Đúng **quy tắc 4a** của `prompts/ocr.py`: phân biệt được số di động thì số đó vào `phone`, số còn lại vào `phone_alt`. Thứ tự in trên thẻ chỉ dùng khi không phân biệt được (quy tắc 4b). Task 9.3 sửa quy tắc này chính vì bản cũ để model tự chọn |

---

## Việc cần người khác

| Việc | Cho ai | Vì sao |
|------|--------|--------|
| `samples/cards/` vẫn **trống 0 ảnh** (task 3.9) | **T** | Chặn task **7.8** từ D7 tới giờ, và chặn luôn phép đo tiêu chí **A3** (≥85% trên 30 ảnh chụp thật) — xem `accuracy.md` mục 1. 10.8 chỉ đo được trên ảnh dựng bằng phông |
| ~~`samples/demo/` cũng **trống 0 ảnh** (task 11.7)~~ | ~~**T**~~ | ✅ **Hết vướng 2026-09-21** — T đã nạp 7 thẻ demo + `cards.json` + `reset_demo.sql` (PR #29), **11.2 của Q làm xong ngay trong ngày**. Giữ dòng này để thấy vướng mắc được gỡ ở đâu, không xoá dấu vết |
| Dán bảng kết quả đo A6 vào `docs/qa-testset.md` mục 8 | **T** | File đó của T (quy ước số 2). Số đo đầy đủ nằm ở [`accuracy.md`](./accuracy.md) phần B, chép sang là xong |
| Chuyển câu `cong ty nao san xuat sua` từ mục 7 lên mục 4 của `qa-testset.md` | **T** | Chỉ làm **khi nào** B-10/I-23 được gỡ. Hiện vẫn là hạn chế đã biết, để nguyên ở mục 7 |
