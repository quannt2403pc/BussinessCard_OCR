# Chất lượng hồ sơ doanh nghiệp

> Chủ sở hữu: **T** · Task: **10.9** · Tiêu chí: **A5** trong `Plan.md` (10 công ty mẫu, ≥ 5 trường MST, quy mô, ngành
> nghề, sản phẩm, địa chỉ kèm nguồn) · Đo ngày 2026-09-21

## 1. Kết luận

| | `gemini-3-flash` | `gemini-3.6-flash-high` |
|---|---|---|
| Gọi thành công | **10/10** | **10/10** |
| ≥ 5 trường có nguồn | **10/10** | **10/10** |
| Đủ cả 5 trường A5 có nguồn | **9/10** (Vinamilk thiếu *quy mô*) | 8/10 (Thế Giới Di Động, Samsung VN thiếu *quy mô*) |
| **Mã số thuế tìm thấy đúng trong trang nguồn** | **10/10** | **10/10** |
| Công ty bịa tên | **0 trường** | **0 trường** |
| Thời gian trung vị | 43 giây / công ty | **32 giây / công ty** |

**Tiêu chí A5 đạt với cả hai model**: 10/10 công ty có ≥ 5 trường có nguồn, và mọi mã số thuế đều được tìm thấy đúng
trong trang nguồn đã trích. Trường hay thiếu nhất là *quy mô*: khi model không chỉ ra được trang nào chứng minh con số
nhân sự thì trường đó bị để trống thay vì điền đoán (nguyên tắc R4).

**Chọn model cho demo:** cả hai đều dùng được. `gemini-3.6-flash-high` nhanh hơn ~11 giây mỗi công ty trong lượt đo
này — ngược với cảm nhận "model đời mới chậm hơn" lúc test tay; `gemini-3-flash` đủ trường A5 hơn một công ty. **Mỗi
model mới đo một lượt**, nên chênh một công ty và vài giây nằm trong sai số của kết quả tìm kiếm Google thay đổi theo
lần gọi. Khuyến nghị: giữ model đang cấu hình nếu không có lý do đổi; `gemini-3-flash` là mặc định của dự án
(`.env.example`) và là model duy nhất đã theo dõi grounding từ D2.

Tên task ghi *"sau khi tinh chỉnh prompt enrichment"*, nhưng phần tinh chỉnh nằm ở 9.7 và **chưa làm**, nên đây là số
đo của **prompt hiện tại** (thiết kế hai lượt gọi từ 4.7).

## 2. Cách đo

```bash
docker compose exec api python -m scripts.spike_profile_quality --model gemini-3-flash
docker compose exec api python -m scripts.spike_profile_quality --model gemini-3.6-flash-high
```

`scripts/spike_profile_quality.py` gọi đúng `services.enrichment.enrich_company()` mà luồng tạo hồ sơ dùng, **không
ghi gì vào DB**, lần lượt từng công ty, nghỉ 3 giây giữa hai công ty cho khỏi chạm giới hạn gọi.

Ba thứ được đo cho mỗi công ty:

1. **Số trường có nguồn** và **đủ 5 trường A5 chưa** (quy mô tính là đạt nếu có `size_label` hoặc `employee_range`).
2. **Độ đúng của mã số thuế**, kiểm tự động, không tin lời model: tải từng trang nguồn mà hồ sơ trích cho trường MST,
   bỏ mọi ký tự không phải số, rồi tìm đúng dãy số đó trong trang. *Khớp nguồn* nghĩa là trang được trích thật sự chứa
   con số đó.
3. **Chống bịa**: thêm một tên công ty không tồn tại (*Công ty TNHH Kỹ thuật Lam Vân Zeta 2031*). Kết quả đúng duy
   nhất là **0 trường**.

Mười công ty chọn trải đủ loại hình: công nghệ, thực phẩm, thép, ngân hàng, bán lẻ, doanh nghiệp FDI, tập đoàn đa
ngành, dược, logistics cỡ vừa, xây dựng.

## 3a. Số đo — `gemini-3-flash`

| Công ty | Giây | Trường có nguồn | 5 trường A5 | Số nguồn | MST · kiểm với nguồn | Trường bị gỡ vì không có nguồn |
|---|---:|---:|---|---:|---|---|
| Công ty Cổ phần FPT | 39 | 10 | 5/5 | 3 | `0101248141` · khớp nguồn | employee_range |
| Công ty Cổ phần Sữa Việt Nam | 35 | 9 | 4/5 (thiếu Quy mô) | 4 | `0300588569` · khớp nguồn | — |
| Công ty Cổ phần Tập đoàn Hòa Phát | 49 | 11 | 5/5 | 10 | `0900189284` · khớp nguồn | — |
| Ngân hàng TMCP Ngoại thương Việt Nam | 37 | 10 | 5/5 | 4 | `0100112437` · khớp nguồn | — |
| Công ty Cổ phần Đầu tư Thế Giới Di Động | 47 | 11 | 5/5 | 7 | `0306731335` · khớp nguồn | — |
| Công ty TNHH Samsung Electronics Việt Nam | 45 | 11 | 5/5 | 7 | `2300325764` · khớp nguồn | — |
| Công ty Cổ phần Tập đoàn Masan | 46 | 11 | 5/5 | 9 | `0303576603` · khớp nguồn | — |
| Công ty Cổ phần Traphaco | 46 | 10 | 5/5 | 4 | `0100108656` · khớp nguồn | — |
| Công ty Cổ phần Vận tải và Xếp dỡ Hải An | 41 | 10 | 5/5 | 6 | `0103818809` · khớp nguồn | — |
| Công ty Cổ phần Xây dựng Coteccons | 31 | 11 | 5/5 | 11 | `0303443233` · khớp nguồn | — |
| *Công ty TNHH Kỹ thuật Lam Vân Zeta 2031 (bịa)* | 21 | **0** | 0/5 | 0 | — | — |

## 3b. Số đo — `gemini-3.6-flash-high`

| Công ty | Giây | Trường có nguồn | 5 trường A5 | Số nguồn | MST · kiểm với nguồn | Trường bị gỡ vì không có nguồn |
|---|---:|---:|---|---:|---|---|
| Công ty Cổ phần FPT | 25 | 10 | 5/5 | 7 | `0101248141` · khớp nguồn | — |
| Công ty Cổ phần Sữa Việt Nam | 40 | 11 | 5/5 | 8 | `0300588569` · khớp nguồn | — |
| Công ty Cổ phần Tập đoàn Hòa Phát | 42 | 11 | 5/5 | 9 | `0900189284` · khớp nguồn | — |
| Ngân hàng TMCP Ngoại thương Việt Nam | 44 | 11 | 5/5 | 9 | `0100112437` · khớp nguồn | — |
| Công ty Cổ phần Đầu tư Thế Giới Di Động | 28 | 9 | 4/5 (thiếu Quy mô) | 9 | `0306731335` · khớp nguồn | — |
| Công ty TNHH Samsung Electronics Việt Nam | 14 | 8 | 4/5 (thiếu Quy mô) | 5 | `2300325764` · khớp nguồn | — |
| Công ty Cổ phần Tập đoàn Masan | 37 | 11 | 5/5 | 9 | `0303576603` · khớp nguồn | — |
| Công ty Cổ phần Traphaco | 49 | 11 | 5/5 | 5 | `0100108656` · khớp nguồn | — |
| Công ty Cổ phần Vận tải và Xếp dỡ Hải An | 21 | 10 | 5/5 | 5 | `0103818809` · khớp nguồn | size_label |
| Công ty Cổ phần Xây dựng Coteccons | 28 | 11 | 5/5 | 9 | `0303443233` · khớp nguồn | — |
| *Công ty TNHH Kỹ thuật Lam Vân Zeta 2031 (bịa)* | 34 | **0** | 0/5 | 0 | — | — |

Đọc bảng:

- **Cột cuối là cơ chế chống bịa đang làm việc**: `gemini-3-flash` có trả `employee_range` cho FPT, `gemini-3.6-flash-high` có trả `size_label` cho Hải An, nhưng không kèm trang nguồn nào trong danh sách grounding, nên trường bị gỡ trước khi lưu.
- Hai trang nguồn cùng một tên miền được đếm riêng nếu là hai URL khác nhau; *Số nguồn* là số URL khác nhau.
- Mỗi công ty tốn 2 lời gọi model; thời gian gồm cả bước theo redirect của Google để lấy URL gốc.

## 4. Giới hạn của phép đo

- **Một lượt đo, một thời điểm**: kết quả tìm kiếm của Google thay đổi theo ngày; một công ty đạt 5/5 hôm nay có thể
  4/5 lần sau. Đủ để nghiệm thu A5, chưa đủ để nói về độ ổn định.
- **Mới kiểm độ đúng của MST**, trường có định danh rõ nhất. Địa chỉ, quy mô, sản phẩm mới kiểm *có nguồn*, chưa kiểm
  *nguồn nói đúng như hồ sơ ghi*.
- **Toàn công ty lớn, nhiều thông tin công khai.** Công ty nhỏ ít dấu vết trên mạng sẽ có ít trường hơn — đúng với
  thiết kế (thiếu nguồn thì để trống), nhưng nghĩa là A5 dễ đạt hơn thực tế sử dụng.
