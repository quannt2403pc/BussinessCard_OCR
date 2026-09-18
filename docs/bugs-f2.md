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
