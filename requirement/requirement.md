# #2: Chuyển hoá danh thiếp thành dữ liệu đối tác chuẩn hoá

## Bối cảnh
Các đơn vị thường xuyên trao đổi card visit tại hội thảo, triển lãm, sự kiện kết nối — mỗi sự kiện có thể thu thập hàng chục đến hàng trăm tấm. Hiện tại việc xử lý hoàn toàn thủ công: nhập tay vào bảng tính, rồi tự tra cứu thêm thông tin đối tác trên Internet.

## Thách thức
* Nhập tay tốn thời gian, dễ sai sót, thường bị trì hoãn
* Card visit chỉ có thông tin cơ bản; tra cứu bổ sung (lĩnh vực, quy mô, sản phẩm) trên nhiều nguồn rất mất thời gian
* Dữ liệu liên hệ phân tán, không có hệ thống lưu trữ tập trung để chia sẻ và tái sử dụng
* Card visit đa ngôn ngữ (Anh, Hàn, Nhật, Trung) gây khó khăn trong nhận diện và tra cứu

> **MONG MUỐN**  
> Giải pháp cho phép chụp/scan danh thiếp, tự động trích xuất thông tin, tra cứu bổ sung từ nguồn công khai, và tổng hợp thành hồ sơ đối tác chuẩn hóa sẵn sàng tái sử dụng.

*Hình 1. Slide tóm tắt bối cảnh, thách thức và mong muốn giải pháp*

---
## Nút thắt Nhập liệu
* **Đầu vào:** Danh thiếp đa ngôn ngữ (Anh, Hàn, Nhật, Trung)
* **Quy trình:** Tốn thời gian, dễ sai sót (Nhập tay) $\rightarrow$ Tra cứu thủ công trên Internet
* **Kết quả:** Dữ liệu phân tán

## Chuyển đổi với AI
* **Đầu vào:** Chụp/scan danh thiếp qua ứng dụng di động
* **Xử lý:** Chuyển dữ liệu qua **AI OCR Engine**
* **Luồng trích xuất:**
  * Trích xuất tự động (Thông tin cá nhân, Số điện thoại, Email, Chức danh)
  * Quét nguồn công khai (Lĩnh vực, quy mô, sản phẩm)
* **Kết quả:** Hồ sơ đối tác chuẩn hóa
