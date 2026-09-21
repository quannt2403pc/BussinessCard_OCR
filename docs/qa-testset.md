# Bộ câu hỏi kiểm thử trợ lý AI

> Chủ sở hữu: **T** · Task: **8.7** · Dùng để nghiệm thu: tiêu chí **D8** (≥ 7/10) và **A6** trong `Plan.md` (≥ 8/10)
> Người chạy đo: **Q** (sau khi 8.1–8.4 xong) · Cập nhật: 2026-09-18

## 1. Vì sao bộ câu hỏi bám vào một bộ dữ liệu cố định

Câu hỏi về một công ty không có trong KB thì chỉ đo được mỗi khả năng nói "không biết". Muốn đo
trợ lý trả lời **đúng** thì câu hỏi phải có đáp án nằm sẵn trong KB lúc chạy đo.

Bộ này dùng đúng bộ dữ liệu của `scripts/eval_retrieval.py::seed()` (task 7.4 của Q): **4 danh
thiếp + 3 hồ sơ doanh nghiệp, đủ Việt / Hàn / Nhật**. Hai lợi ích:

- Recall của tầng truy hồi đã được đo trên chính bộ này (hybrid recall@3 = 9/10). Câu nào trả lời
  sai thì biết ngay lỗi nằm ở bước truy hồi hay ở prompt, không phải đoán.
- Không phụ thuộc vào ảnh mẫu (3.9) hay kết quả enrich thật, vốn thay đổi giữa các lần chạy.

⚠️ `eval_retrieval.py` chạy trong một transaction rồi **rollback**, nên chat thật (8.2) không thấy
dữ liệu đó. Muốn đo qua `POST /api/chat` thì bộ dữ liệu phải được ghi thật vào DB rồi
`POST /api/kb/reindex`. Đề xuất cho **Q**: `scripts/seed.py` (9.2) nạp lại đúng hàm `seed()` này
thay vì dựng bộ dữ liệu thứ hai — hai bộ dữ liệu song song là hai bộ đáp án phải giữ khớp nhau.
Nếu `seed.py` dùng dữ liệu khác thì báo **T** sửa file này.

## 2. Dữ kiện trong KB

### Hồ sơ doanh nghiệp (trích dẫn trỏ về `/companies/{company_id}`)

| Nhãn | Công ty | MST | Ngành | Sản phẩm / dịch vụ |
|------|---------|-----|-------|--------------------|
| P1 | Công ty TNHH Logistics Đại Việt (*Đại Việt Logistics*) | 0301234567 | Logistics; Vận tải và kho bãi | Vận tải đường bộ Bắc - Nam; Kho ngoại quan; Giao nhận hàng hoá |
| P2 | Công ty CP Sữa Mộc Châu (*Mocchau Milk*) | 0100233468 | Chế biến thực phẩm; Sản xuất sữa | Sữa tươi thanh trùng; Sữa chua ăn; Bơ và phô mai |
| P3 | Hanwha Precision Vietnam (*한화정밀기계*) | 0312345678 | Cơ khí chính xác; Sản xuất linh kiện | Linh kiện cơ khí chính xác; Máy gắn linh kiện SMT |

Cả ba hồ sơ cùng có: thành lập 2005, quy mô *Vừa*, 200-500 nhân sự, địa chỉ *Hà Nội, Việt Nam*.

### Danh thiếp (trích dẫn trỏ về `/cards/{id}`)

| Nhãn | Họ tên | Chức vụ | Công ty | Email | SĐT |
|------|--------|---------|---------|-------|-----|
| C1 | Nguyễn Văn An | Giám đốc kinh doanh | Logistics Đại Việt | an.nguyen@daiviet-logistics.vn | +84912345678 |
| C2 | Trần Thị Bình | Trưởng phòng Marketing | Sữa Mộc Châu | binh.tran@mocchaumilk.vn | +84987654321 |
| C3 | Kim Min-jun | Sales Manager / 영업 과장 | Hanwha Precision Vietnam | minjun.kim@hanwha.co.kr | +82212345678 |
| C4 | 田中 太郎 | 営業部長 | 東京テック株式会社 (**chưa gắn công ty**) | tanaka@tokyotech.co.jp | +81312345678 |

## 3. Luật chấm

Một câu **đạt** khi thoả đủ ba điều:

1. **Đủ dữ kiện bắt buộc** ở cột *Phải có*. So khớp không phân biệt hoa thường; số điện thoại chấp
   nhận cả dạng nội địa (`0912 345 678`) lẫn E.164 (`+84912345678`); tên riêng chấp nhận viết có
   hoặc không có dấu phụ.
2. **Không có dữ kiện sai hay bịa thêm.** Nói đúng MST nhưng kèm một năm thành lập sai vẫn là
   trượt: người dùng không phân biệt được câu nào đúng câu nào bịa.
3. **Có ít nhất một trích dẫn trỏ đúng nguồn** ở cột *Nguồn* và bấm vào mở được đúng trang. Trích
   dẫn thừa (một nguồn liên quan khác) không trừ điểm.

Không chấm văn phong, độ dài hay ngôn ngữ trả lời.

## 4. Mười câu tính điểm

| # | Câu hỏi | Phải có | Nguồn | Đang thử điều gì |
|---|---------|---------|-------|------------------|
| 1 | Công ty nào làm về logistics? | Logistics Đại Việt | P1 | Tìm theo ngành bằng ngôn ngữ tự nhiên (tiêu chí D7) |
| 2 | Mã số thuế của Công ty CP Sữa Mộc Châu là bao nhiêu? | 0100233468 | P2 | Tra một trường cụ thể của hồ sơ |
| 3 | Hanwha Precision Vietnam cung cấp sản phẩm gì? | Linh kiện cơ khí chính xác; Máy gắn linh kiện SMT | P3 | Trả lời bằng danh sách, không bỏ sót mục nào |
| 4 | Email của Trần Thị Bình là gì? | binh.tran@mocchaumilk.vn | C2 | Tra danh thiếp theo tên người |
| 5 | Số 0912 345 678 là của ai? | Nguyễn Văn An | C1 | Tìm theo định danh (nhánh full-text 7.2), số nhập khác định dạng lưu |
| 6 | Ai là giám đốc kinh doanh của Logistics Đại Việt? | Nguyễn Văn An | C1 | Tìm theo chức vụ + công ty |
| 7 | Kim Min-jun làm chức vụ gì, ở công ty nào? | Sales Manager (hoặc 영업 과장); Hanwha Precision Vietnam | C3 | Danh thiếp tiếng Hàn |
| 8 | Có ai làm ở công ty Nhật không? | 田中 太郎 (hoặc Tanaka); 東京テック株式会社 (hoặc Tokyo Tech) | C4 | Câu hỏi tiếng Việt, dữ liệu tiếng Nhật; thẻ **chưa gắn công ty** |
| 9 | Tôi muốn liên hệ công ty sản xuất sữa thì gọi cho ai? | Trần Thị Bình; +84987654321 (hoặc email) | C2 | Nối hồ sơ DN với danh thiếp: câu hỏi nói ngành, đáp án là một người |
| 10 | Mã số thuế 0301234567 là của công ty nào? | Logistics Đại Việt | P1 | Tìm ngược từ định danh ra công ty |

## 5. Câu ngoài phạm vi KB — bắt buộc đạt cả 3

Không tính vào 10 câu trên nhưng là **điều kiện chặn**: trượt một câu ở đây thì kết quả đo không
được dùng để nghiệm thu, dù 10 câu trên đạt bao nhiêu.

Lý do: 7.4 đã đo được ngưỡng điểm tương đồng **không** tách được câu lạc đề (câu đúng thấp nhất
0.773, câu lạc đề cao nhất 0.819). Tầng truy hồi luôn trả về vài chunk, nên việc từ chối trả lời
chỉ còn trông vào prompt 8.1. Bộ câu hỏi không kiểm việc này thì không bắt được lỗi bịa.

**Đạt** khi: nói rõ không có thông tin trong dữ liệu, **không nêu con số hay dữ kiện cụ thể nào**,
`citations` rỗng.

| # | Câu hỏi | Vì sao chọn |
|---|---------|-------------|
| X1 | Giá vàng hôm nay bao nhiêu? | Lạc đề hoàn toàn |
| X2 | Hướng dẫn nấu phở bò | Câu lạc đề có điểm tương đồng **cao nhất** trong số đo 7.4 (0.819) |
| X3 | Mã số thuế của Vinamilk là gì? | **Câu nguy hiểm nhất**: đúng lĩnh vực, KB có một công ty sữa khác, và model *biết sẵn* đáp án từ dữ liệu huấn luyện. Trả lời ra một MST là bịa ngoài KB — đúng lỗi R4 trong `Plan.md`. ⚠️ Chỉ có giá trị khi KB **chưa** có hồ sơ Vinamilk: buổi demo (`docs/demo-runbook.md`) tạo hồ sơ Vinamilk thật, nên phải chấm bộ này **trước** phần tạo hồ sơ hoặc sau `samples/demo/reset_demo.sql` |

## 6. Hội thoại nhiều lượt (task 8.3) — không tính điểm

Kiểm việc đưa lịch sử vào prompt. Chạy trong **cùng một `session_id`**:

| Lượt | Câu hỏi | Phải có | Nguồn |
|------|---------|---------|-------|
| 1 | Ai là trưởng phòng marketing của Sữa Mộc Châu? | Trần Thị Bình | C2 |
| 2 | Số điện thoại của chị ấy là gì? | +84987654321 (hoặc 0987 654 321) | C2 |

Lượt 2 không nhắc tên ai. Trả lời được nghĩa là lịch sử đã vào prompt; hỏi lại "chị ấy là ai" hay
trả lời số của người khác là 8.3 chưa chạy đúng.

## 7. Điểm yếu đã biết — không đưa vào bộ tính điểm

| Câu | Kết quả dự kiến | Theo dõi ở |
|-----|-----------------|------------|
| `cong ty nao san xuat sua` (gõ không dấu) | Trượt cả hai nhánh truy hồi | **I-23** trong `Task.md` |

Cố ý để ngoài 10 câu: đây là giới hạn đã đo được của tầng truy hồi, đưa vào thì một điểm của bộ
test luôn mất vì lý do đã biết, che mất lỗi mới. Khi I-23 được gỡ thì chuyển câu này vào mục 4.

## 8. Mẫu ghi kết quả

Chép bảng này vào cuối file mỗi lần đo, ghi rõ ngày, model và commit.

```
Ngày: ____  ·  LLM_MODEL: ____  ·  Embedding: ____  ·  Commit: ____

| #   | Đạt? | Ghi chú (sai ở đâu: truy hồi / prompt / trích dẫn) |
|-----|------|-----------------------------------------------------|
| 1   |      |                                                     |
| ... |      |                                                     |
| 10  |      |                                                     |
| X1  |      |                                                     |
| X2  |      |                                                     |
| X3  |      |                                                     |

Tổng: __ / 10  ·  Ngoài phạm vi: __ / 3  ·  Nhiều lượt: đạt / trượt
```

## 9. Kết quả đo

### Lượt 1 — 2026-09-21

```
Ngày: 2026-09-21  ·  LLM_MODEL: gemini-3.6-flash-high  ·  Embedding: intfloat/multilingual-e5-small  ·  Commit: 61e1385 (main)
Người chạy: T, qua POST /api/chat (script tổng duyệt 11.8), trước phần tạo hồ sơ demo
```

| #   | Đạt? | Ghi chú |
|-----|------|---------|
| 1   | ✅ | Logistics Đại Việt, trích hồ sơ P1 |
| 2   | ✅ | `0100233468`, trích hồ sơ P2 |
| 3   | ✅ | Đủ 2 sản phẩm, trích hồ sơ P3 |
| 4   | ✅ | Email đúng, trích thẻ C2 |
| 5   | ✅ | Nguyễn Văn An — số nhập dạng nội địa vẫn khớp số lưu E.164 |
| 6   | ✅ | Nguyễn Văn An, trích thẻ C1 |
| 7   | ✅ | *Sales Manager / 영업 과장*, Hanwha, trích thẻ C3 |
| 8   | ✅ | 田中 太郎 + 東京テック株式会社; kể thêm *田中 健太 (FPT Japan)* — dữ liệu **thật** có trong DB dev lúc đo, không phải bịa |
| 9   | ✅ | Trần Thị Bình + SĐT + email, trích cả hồ sơ P2 lẫn thẻ C2 |
| 10  | ✅ | Logistics Đại Việt, trích hồ sơ P1 |
| X1  | ✅ | *"Không có thông tin này trong dữ liệu đã nhập."*, 0 trích dẫn |
| X2  | ✅ | Như X1 |
| X3  | ✅ | Như X1 — không lộ MST Vinamilk mà model biết sẵn |

**Tổng: 10 / 10  ·  Ngoài phạm vi: 3 / 3  ·  Nhiều lượt: đạt** (lượt 2 *"Số điện thoại của chị ấy là gì?"* →
`+84987654321`, trích thẻ C2). Thời gian: 15 câu trong 110 giây, khoảng 7 giây/câu.

→ Đạt tiêu chí **D8** (≥ 7/10) và **A6** (≥ 8/10). Mới đo một lượt với một model; nên đo lại với `gemini-3-flash`
nếu buổi demo dùng model đó.
