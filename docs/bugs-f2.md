# Nhật ký bug F2 — Hồ sơ doanh nghiệp

> Chủ sở hữu: **T** · Task: **10.6** (ghi) / **10.7** (sửa) · Bug F1/F3 phát hiện trong lúc test F2 **không ghi ở đây**:
> báo Q, ghi vào bảng issue của `Task.md` để Q chuyển sang `docs/bugs-f1-f3.md` (quy ước số 7).

## Thang mức độ

Tiêu chí D10 đếm Blocker/Critical **trên cả hai file bug**, nên thang này phải trùng với `docs/bugs-f1-f3.md` của Q.
⚠️ Chưa chốt với Q — đề xuất:

| Mức | Nghĩa | Ví dụ |
|-----|-------|-------|
| **Blocker** | Chặn một luồng chính của demo, không có đường vòng | Bấm *Tạo hồ sơ* luôn báo lỗi |
| **Critical** | Sai hoặc mất dữ liệu **âm thầm** trên luồng chính, hoặc trượt một tiêu chí nghiệm thu A1–A7 | Hồ sơ lưu MST của công ty khác |
| **Major** | Sai dữ liệu nhưng người dùng nhìn thấy và sửa tay được; tính năng phụ hỏng | Công ty bị tách thành hai bản ghi |
| **Minor** | Hiển thị, câu chữ, trường hợp hiếm | Nhãn tiếng Anh lọt vào giao diện |

## Danh sách

| ID | Mức | Tóm tắt | Phát hiện | Người sửa | Trạng thái |
|----|-----|---------|-----------|-----------|------------|
| B2-01 | Major | Tên công ty dạng *"Chi nhánh … tại …"* cho khoá chuẩn hoá khác hẳn công ty mẹ → tách thành hai công ty, và `cong ty tnhh` còn nằm trong khoá | 2026-09-18, review thẻ V.T.E Travel | **Q** | ⬜ Chưa sửa — giao Q, xem **I-31** trong `Task.md` |
| B2-02 | Major | *"Tập đoàn"*, *"Tổng công ty"*, *"Group"* không được bỏ khi chuẩn hoá → cùng một pháp nhân (CTCP FPT) ra 3 khoá, sinh 3 công ty | 2026-09-18, thẻ FPT tiếng Việt + tiếng Nhật | T | ✅ Đã sửa 2026-09-21 (10.7) |
| B2-03 | Major | Không huỷ được lượt tạo hồ sơ đang chạy/đang chờ; hồ sơ `draft` kẹt sau khi api khởi động lại không có cách gỡ | 2026-09-18, test thực tế | T | ✅ Đã sửa 2026-09-21 (10.7) |
| B2-04 | Minor | Không ẩn được hồ sơ không còn cần dùng → làm loãng danh sách, ô tìm kiếm và câu trả lời của trợ lý AI | 2026-09-18, test thực tế | T | ✅ Đã sửa 2026-09-21 (10.7) |

## Chi tiết

### B2-01 — Chi nhánh không được gộp về công ty mẹ

**Tái hiện** (`app/services/normalize_company.py`, task 3.7):

```python
normalize_company_name("CHI NHÁNH CÔNG TY TNHH THƯƠNG MẠI DỊCH VỤ DU LỊCH QUỐC TẾ HOÀNG CHƯƠNG TẠI HÀ NỘI")
# 'chi nhanh cong ty tnhh thuong mai dich vu du lich quoc te hoang chuong tai ha noi'

normalize_company_name("Công ty TNHH Thương mại Dịch vụ Du lịch Quốc tế Hoàng Chương")
# 'thuong mai dich vu du lich quoc te hoang chuong'
```

**Nguyên nhân:** quy tắc bỏ hình thức pháp lý chỉ chạy ở **đầu** tên. Có chữ *"Chi nhánh"* đứng trước thì
*"Công ty TNHH"* không còn ở đầu nên bị giữ lại, cộng thêm phần đuôi *"tại Hà Nội"*.

**Hệ quả:**

- Xác nhận một thẻ của chi nhánh và một thẻ của trụ sở thì sinh **hai** bản ghi `companies`. Chống trùng là rủi ro
  **R6** trong `Plan.md`, và không có đường gộp tay vì 8.10 (gộp công ty) đã bị cắt.
- Hai chi nhánh ở hai thành phố của cùng một công ty cũng thành hai công ty.
- Enrichment tra cứu bằng nguyên tên dài của chi nhánh.

**Mức Major, không phải Critical:** dữ liệu tách đôi nhưng hiện rõ trên màn hình Doanh nghiệp, không âm thầm ghi sai.

**Người sửa: Q** (T giao ngày 2026-09-18). `app/services/normalize_company.py` và `tests/test_normalize_company.py` là file của T; **T đồng ý để Q sửa hai
file này cho riêng B2-01**, không cần chuyển qua T.

**Cần chốt trước khi sửa:** chi nhánh có mã số thuế riêng (dạng `0101234567-001`), nên *"gộp về công ty mẹ"*
không đúng tuyệt đối về mặt pháp lý. Q chốt hướng và báo T khi mở PR. Đề xuất cho demo: khoá chuẩn hoá bỏ tiền tố *"chi nhánh"* và đuôi
*"tại <địa danh>"*, còn tên đầy đủ của chi nhánh giữ lại làm `aliases`. Liên quan **I-21** (va chạm khoá "Phú Cơ", vẫn của T) — cùng hàm, nên báo nhau trước khi sửa để
không sửa chồng; chạy lại toàn bộ `tests/test_normalize_company.py` sau khi sửa.

### B2-02 — "Tập đoàn / Tổng công ty / Group" không được coi là hình thức doanh nghiệp

**Tái hiện** (trước khi sửa): `Tập đoàn FPT` → `tap doan fpt`, `FPT Group` → `fpt group`, còn `Công ty Cổ phần FPT`
và `FPT Corporation` → `fpt`. Khoá `fpt` chỉ 3 ký tự, dưới ngưỡng so khớp mờ 10 ký tự, nên ba tấm thẻ của cùng một
pháp nhân sinh ba công ty.

**Sửa:** thêm *"tập đoàn"*, *"tổng công ty"* vào nhóm tiền tố như *"công ty"* (tự sinh các tổ hợp như *"tổng công ty
cổ phần"*), thêm *"group"* vào hậu tố tiếng Anh. Cả 5 cách viết giờ ra `fpt`. *"CP Group"* vẫn giữ `cp group` nhờ quy
tắc bảo vệ thương hiệu có sẵn.

**Không phải bug — FPT Japan:** `FPTジャパン株式会社` vẫn là công ty riêng, **đúng**. Hồ sơ đã chứng minh là hai pháp
nhân: CTCP FPT (MST `0101248141`, Hà Nội) và ＦＰＴジャパンホールディングス株式会社 (mã số doanh nghiệp Nhật
`5010701021223`, Tokyo). Gộp lại sẽ ghép MST Việt Nam với địa chỉ Tokyo. Thay vào đó, trang chi tiết có thêm hai khối
gợi ý (không tự gộp):

- **Trùng mã số thuế**: hồ sơ có cùng MST với công ty khác → cảnh báo *có thể cùng một pháp nhân*. Bắt được trùng bất
  kể tên in trên thẻ viết thế nào, kể cả khác ngôn ngữ.
- **Cùng tên miền email / website** trên danh thiếp (bỏ qua gmail, yahoo…) → gợi ý *cùng tập đoàn / công ty con*.
  Chỉ để tham khảo: thẻ FPT Japan in `fpt.com` dù website thật của họ là `fptsoftware.jp`, nên tự gộp theo tên miền
  sẽ nhập sai hai pháp nhân.

⚠️ Công ty **đã lưu** trước khi sửa giữ nguyên khoá cũ (ví dụ `tap doan fpt` trong DB dev); chỉ công ty tạo mới
dùng khoá mới. Muốn gộp dữ liệu cũ thì quét lại thẻ trên DB sạch.

### B2-03 — Huỷ lượt tạo hồ sơ

- `POST /api/companies/{id}/enrich/cancel` (một công ty) và `POST /api/companies/enrich-jobs/{job_id}/cancel` (cả lô).
- Công ty **đang chờ**: chuyển item sang `cancelled`, không bao giờ được gọi LLM. Công ty **đang chạy**: huỷ đúng
  task của nó, cắt ngang lời gọi HTTP tới LLM.
- Lần tạo đầu: gỡ hồ sơ `draft`. Tạo lại: **giữ nguyên hồ sơ cũ**.
- Item `running` mồ côi (api khởi động lại giữa chừng) và hồ sơ `draft` bị bỏ lại sau khi item đã hết hạn cũng gỡ
  được bằng cùng nút *Huỷ*.
- Không cần migration: cột trạng thái không có ràng buộc giá trị, index chống trùng chỉ tính `pending`/`running` nên
  huỷ xong chạy lại được ngay.
- Giới hạn: huỷ tiết kiệm **thời gian chờ**, chưa chắc tiết kiệm quota — phía Google có thể vẫn chạy nốt lời gọi đã
  gửi đi.

### B2-04 — Ẩn / hiện lại hồ sơ (xoá mềm)

- Trạng thái hồ sơ mới `archived`, **không cần migration**. `POST /api/companies/{id}/profile/archive` và
  `/profile/restore`.
- Ẩn: gỡ chunk khỏi KB → trợ lý AI không trả lời bằng hồ sơ đó nữa. Nút reindex của Q chỉ nạp `generated`/`verified`
  nên hồ sơ ẩn **không tự quay lại KB**. Công ty biến khỏi danh sách và ô tìm kiếm mặc định; bộ lọc *Đã ẩn* để xem lại.
- Hiện lại: trạng thái được suy ra — còn trường có giá trị mà không có nguồn (tức đã sửa tay) thì `verified`, không thì
  `generated` — rồi index lại vào KB.
- Hồ sơ đang ẩn không sửa tay được (409) để không tự bị hiện lại; *Tạo lại hồ sơ* thì vẫn được và hồ sơ hiện lại.
- Export vẫn xuất hồ sơ ẩn, cột `profile_status = archived`: export là bản sao lưu đầy đủ.
