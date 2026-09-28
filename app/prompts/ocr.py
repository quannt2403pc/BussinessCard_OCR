"""Prompt trích xuất danh thiếp: JSON schema cố định, confidence từng trường, cấm suy đoán.

Chủ sở hữu: Q | Task: 3.3

Prompt thiết kế quanh **rủi ro R3 — OCR sai với danh thiếp Hàn/Nhật/Trung hoặc ảnh mờ**. Ba lớp
phòng thủ:

1. **Cấm suy đoán** — không đọc được thì `null`, tuyệt đối không điền giá trị "hợp lý" suy ra từ
   tên công ty hay website. Trường trống người dùng điền được trong 10 giây; trường sai trông như
   thật thì không ai phát hiện.
2. **`confidence` từng trường** — màn hình review tô vàng trường điểm thấp để mắt người rơi vào
   đúng chỗ cần kiểm.
3. **`language_detected`** — quyết định mã vùng khi chuẩn hoá số điện thoại.

Phạm vi ngôn ngữ là **không giới hạn**: mã nào đọc được thì trả mã đó (639-1, không có thì 639-3).
Quy tắc 3 nói rõ "mọi hệ chữ viết" và nói thẳng rằng **có bước Việt hoá riêng ở sau**, để model
không tự ý phiên âm giúp.

Khoá JSON trùng tên cột trong bảng `business_cards` để `services/ocr.py` không phải dựng thêm một
lớp ánh xạ tên. Đổi khoá ở đây thì phải đổi cả `app/schemas/card.py`.

⚠️ Prompt có ghi "chỉ trả JSON, không bọc trong khối ```". **Đừng tin là đủ** — `gemini-3-flash`
vẫn bọc (I-15).

⚠️ Quy tắc 4 xếp số điện thoại theo **thứ tự ưu tiên a → b** và liệt kê nhãn của nhiều ngôn ngữ.
Bản cũ viết "số di động **hoặc** số đứng đầu" — hai vế chỉ về hai số khác nhau trên thẻ Nhật, và
model tự chọn: hợp lý nhưng là hành vi ngầm, đổi model là đổi theo.
"""

#: Bảy trường bắt buộc của tiêu chí A3 — "ngày upload" do hệ thống tự ghi nên không nằm trong prompt.
REQUIRED_FIELDS: tuple[str, ...] = (
    "full_name",
    "job_title",
    "company_name_raw",
    "email",
    "phone",
    "address",
    "website",
)

#: Toàn bộ trường nội dung model phải trả về, kể cả trường không bắt buộc.
CONTENT_FIELDS: tuple[str, ...] = (*REQUIRED_FIELDS, "phone_alt")

JSON_SCHEMA_DESCRIPTION = """{
  "full_name":         string | null,  // ho ten nguoi tren danh thiep, giu nguyen chu viet goc
  "job_title":         string | null,  // chuc vu, vi du "Giam doc kinh doanh", "Sales Manager"
  "company_name_raw":  string | null,  // ten cong ty IN TREN THE, giu nguyen, khong tu rut gon
  "email":             string | null,
  "phone":             string | null,  // so lien he chinh, giu nguyen dinh dang in tren the
  "phone_alt":         string | null,  // so thu hai neu the in nhieu so, khong co thi null
  "address":           string | null,  // dia chi day du tren mot dong
  "website":           string | null,
  "language_detected": string,         // ma ISO 639-1 cua ngon ngu chinh, VD: en vi ko ja zh th ru de
                                       // (khong gioi han trong vi du; khong co ma 2 chu thi dung 639-3)
  "is_business_card":  boolean,        // false neu anh khong phai danh thiep
  "confidence": {                      // diem tin cay 0.0-1.0 cho tung truong o tren
    "full_name": number, "job_title": number, "company_name_raw": number,
    "email": number, "phone": number, "phone_alt": number,
    "address": number, "website": number
  }
}"""

SYSTEM_PROMPT = """Bạn là hệ thống trích xuất dữ liệu từ ảnh danh thiếp. Nhiệm vụ duy nhất:
đọc chữ có thật trên ảnh và xếp vào đúng trường. Bạn KHÔNG phải trợ lý trò chuyện.

QUY TẮC TỐI QUAN TRỌNG — đọc kỹ trước khi trả lời:

1. CHỈ ghi những gì NHÌN THẤY trên ảnh. Không suy đoán, không bổ sung từ kiến thức của bạn.
   Không đọc được hoặc thẻ không in trường đó → `null`. Để trống KHÔNG bị coi là thất bại;
   điền một giá trị không có trên ảnh MỚI là thất bại.

2. Không tự suy ra trường này từ trường khác. Ví dụ sai: thấy website "abc.com" rồi đoán email
   là "info@abc.com"; thấy tên công ty rồi đoán địa chỉ trụ sở. Chỉ chép cái đã in.

3. GIỮ NGUYÊN chữ viết gốc, ở MỌI ngôn ngữ và MỌI hệ chữ viết (Latin, Hán, Kana, Hangul, Kirin,
   Ả Rập, Thái, Devanagari…). Chép đúng chữ in trên thẻ, không dịch, không phiên âm sang chữ
   Latin. Danh thiếp Việt giữ nguyên dấu. Hệ thống có bước Việt hoá riêng ở sau, việc của bạn
   chỉ là đọc đúng chữ.
   Thẻ in song ngữ (một mặt chữ bản địa, một mặt tiếng Anh): ưu tiên bản chữ bản địa cho
   `full_name` và `company_name_raw`.

4. Số điện thoại: chép nguyên định dạng in trên thẻ, kể cả dấu `+`, ngoặc và dấu cách; KHÔNG
   tự đổi sang định dạng khác (hệ thống có bước chuẩn hoá riêng). Bỏ nhãn đứng trước số, ở
   mọi ngôn ngữ: "Tel:", "Mobile:", "TEL:", "携帯:", "电话:", "휴대폰:", "ĐT:".
   Thẻ in nhiều số thì theo ĐÚNG thứ tự ưu tiên sau, không đổi:
   a. Có số di động phân biệt được (nhãn "Mobile"/"Cell"/"携帯"/"手机"/"휴대폰", hoặc đầu số di
      động của nước đó) → số đó vào `phone`.
   b. Không phân biệt được số nào là di động → số **in trước** vào `phone`.
   Số còn lại vào `phone_alt`. Nhiều hơn hai số thì bỏ từ số thứ ba trở đi.

5. `confidence` phải phản ánh thật mức độ chắc chắn khi ĐỌC CHỮ: 1.0 = chữ rõ, chắc chắn đúng
   từng ký tự; 0.5 = đoán được nhưng có ký tự mờ/nhoè; 0.0 = trường để `null`. Đừng chấm 1.0
   cho mọi trường — điểm đó là thứ người dùng dựa vào để biết chỗ nào cần kiểm lại.

6. `is_business_card` = false nếu ảnh không phải danh thiếp (ảnh phong cảnh, tài liệu, màn hình
   chụp…). Khi đó để mọi trường nội dung là `null`, đừng cố vét chữ trong ảnh.

7. Trả về ĐÚNG một object JSON theo schema dưới đây. Không thêm lời dẫn, không giải thích,
   không bọc trong khối ```."""

USER_PROMPT = f"""Trích xuất thông tin từ ảnh danh thiếp này thành JSON theo đúng schema sau:

{JSON_SCHEMA_DESCRIPTION}

Chỉ trả về JSON."""


def build_prompt(hint: str | None = None) -> str:
    """Prompt gửi kèm ảnh. `hint` để thêm gợi ý khi tinh chỉnh theo lỗi thực tế.

    Tách thành hàm vì vòng lặp đo — sửa prompt — đo lại cần chỗ cắm thêm ngữ cảnh mà không phải
    sửa chữ ký của `services/ocr.py`.
    """
    if not hint:
        return USER_PROMPT
    return f"{USER_PROMPT}\n\nGợi ý thêm về ảnh này: {hint}"
