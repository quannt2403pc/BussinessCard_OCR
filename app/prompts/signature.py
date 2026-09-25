"""Prompt trích xuất liên hệ từ **khối chữ ký email**.

Chủ sở hữu: T | Task: NEXT-08 | xem Task.md

Dùng lại **nguyên schema JSON** của `prompts/ocr.py` (file của Q): đầu ra phải đi qua đúng
`ocr.parse_response()`, đúng bước chuẩn hoá SĐT/email, và rơi vào đúng màn hình review đã có.
Chỉ đổi phần mô tả *đầu vào* — ảnh thành chữ — và thêm những luật mà riêng chữ ký email mới cần.

Ba luật ấy đều đến từ chỗ chữ ký email khác tấm danh thiếp:

1. **Chữ ký hay có cả một đoạn miễn trừ trách nhiệm pháp lý** dài hơn phần thông tin. Model phải
   biết bỏ qua, nếu không nó nhét cả đoạn ấy vào `address`.
2. **Chuỗi thư trả lời qua lại** thường dính theo. Chỉ lấy chữ ký ở khối được dán, không đi lục
   những người khác trong chuỗi — lấy nhầm là tạo liên hệ cho một người không ai định lưu.
3. **Không có ảnh để đối chiếu.** Nên `confidence` phải phản ánh đúng việc model đang đọc chữ
   sạch chứ không đoán từ pixel mờ: chữ ký ghi rõ thì tin cao, còn suy diễn thì phải hạ xuống.
"""

from app.prompts.ocr import JSON_SCHEMA_DESCRIPTION

MAX_SIGNATURE_CHARS = 4000

SYSTEM_PROMPT = """Bạn là hệ thống trích xuất thông tin liên hệ từ khối chữ ký cuối email.
Nhiệm vụ duy nhất: đọc đoạn văn bản được dán vào và trả về JSON đúng schema.

Quy tắc:
1. Chỉ trả JSON, không giải thích, không bọc trong khối mã.
2. Trường không tìm thấy thì để `null`, tuyệt đối không bịa.
3. Giữ nguyên chữ như trong chữ ký, kể cả chữ Hàn/Nhật/Trung/Ả Rập — không tự dịch, không tự
   phiên âm. Bước Việt hoá là một lượt riêng sau đó.
4. **Bỏ qua đoạn miễn trừ trách nhiệm pháp lý** ("This email and any files transmitted…",
   "Thông tin trong thư này là bảo mật…"). Nó không phải địa chỉ và không phải chức vụ.
5. **Chỉ lấy một người** — người có chữ ký trong đoạn được dán. Nếu đoạn văn bản chứa cả chuỗi
   thư trả lời qua lại, bỏ qua mọi người khác trong chuỗi.
6. Bỏ qua dòng quảng cáo, khẩu hiệu, lời kêu gọi bảo vệ môi trường, và liên kết mạng xã hội.
7. `confidence` phản ánh mức chắc chắn thật: trường ghi rõ ràng trong chữ ký thì cao, trường
   phải suy ra (ví dụ đoán chức vụ từ tên phòng ban) thì thấp."""

PROMPT_HEAD = f"""Trích xuất thông tin liên hệ từ khối chữ ký email dưới đây thành JSON theo \
đúng schema sau:

{JSON_SCHEMA_DESCRIPTION}

Khối chữ ký:"""

PROMPT_TAIL = "Chỉ trả về JSON."

FENCE = '"""'


def build_prompt(signature: str) -> str:
    """Ghép chuỗi thay vì `.format()`.

    `JSON_SCHEMA_DESCRIPTION` **là một khối JSON**, tức đầy dấu `{` và `}`. Gọi `.format()` lên
    một chuỗi đã chứa nó thì Python hiểu mấy dấu ấy là chỗ thay thế và ném `KeyError` ngay lượt
    gọi đầu tiên — lỗi chỉ lộ ra lúc chạy thật, không phải lúc gõ. Test của `NEXT-08` bắt được
    đúng chuyện này.
    """
    body = signature.strip()
    return f"{PROMPT_HEAD}\n{FENCE}\n{body}\n{FENCE}\n\n{PROMPT_TAIL}"
