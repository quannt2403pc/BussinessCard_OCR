"""Prompt sinh hồ sơ DN: tra cứu Internet, mọi trường kèm URL nguồn, không nguồn thì null.

Chủ sở hữu: T | Task: 2.9 | xem Task.md

Prompt được thiết kế quanh **rủi ro R4 — LLM bịa thông tin doanh nghiệp** (Plan.md mục 6).
Ba lớp phòng thủ, xếp theo thứ tự hiệu quả giảm dần:

1. **Cấu trúc bắt buộc**: `sources` là một object riêng, model phải tự liệt kê URL cho từng
   trường. Bịa một giá trị thì phải bịa kèm URL — khó hơn nhiều so với chỉ bịa giá trị.
2. **Quy tắc viết bằng lời**: nêu rõ "không có nguồn thì để null", kèm ví dụ đúng/sai.
3. **Kiểm tra phía code**: `CompanyProfileSchema.truong_thieu_nguon()` (task 2.8) đối chiếu
   lại, task 4.8 xoá những trường model vẫn cố tình trả về mà không có nguồn.

Prompt chỉ mô tả *hợp đồng dữ liệu*. Cách bật tìm kiếm Internet (tool `google_search` của
Gemini hay cách khác) do task 2.7 chốt và do `services/enrichment.py` (task 4.7) truyền vào.
"""

from typing import Any

#: Model phải trả JSON đúng các khoá này — khớp `CompanyProfileSchema` (task 2.8).
#: Đổi ở đây thì phải đổi cả schema, nếu không parse sẽ rớt trường.
JSON_SCHEMA_DESCRIPTION = """{
  "legal_name":     string | null,   // tên đăng ký kinh doanh đầy đủ, không phải tên thương hiệu
  "tax_code":       string | null,   // mã số thuế / mã số doanh nghiệp
  "founded_year":   number | null,   // năm thành lập, dạng số nguyên 4 chữ số
  "size_label":     string | null,   // nhãn quy mô, ví dụ "Doanh nghiệp lớn", "SME", "Startup"
  "employee_range": string | null,   // khoảng nhân sự, ví dụ "50-200", "1000+"
  "industry":       string[],        // ngành nghề kinh doanh, mảng rỗng nếu không rõ
  "products":       string[],        // sản phẩm/dịch vụ chính, mảng rỗng nếu không rõ
  "address":        string | null,   // địa chỉ trụ sở chính
  "website":        string | null,   // website chính thức
  "phone":          string | null,
  "email":          string | null,
  "description":    string | null,   // 2-4 câu mô tả doanh nghiệp làm gì
  "sources": {                       // BẮT BUỘC: nguồn cho từng trường ở trên
    "<ten_truong>": [ { "url": string, "title": string } ]
  }
}"""

SYSTEM_PROMPT = f"""Bạn là trợ lý tra cứu thông tin doanh nghiệp. Nhiệm vụ: tìm thông tin công
khai trên Internet về một công ty và tổng hợp thành hồ sơ có cấu trúc.

QUY TẮC TỐI QUAN TRỌNG — đọc kỹ trước khi trả lời:

1. CHỈ ghi thông tin bạn THỰC SỰ tìm thấy trên một trang web cụ thể và đọc được nội dung.
   Không suy đoán, không dựa vào kiến thức chung, không điền giá trị "hợp lý".

2. MỌI trường có giá trị đều PHẢI có ít nhất một URL trong "sources".
   Không tìm được nguồn → để `null` (hoặc mảng rỗng với "industry"/"products").
   Trường để trống KHÔNG bị coi là thất bại. Bịa một giá trị MỚI là thất bại.

3. Mã số thuế là trường dễ sai nhất. Chỉ ghi khi thấy con số đó in rõ trên trang nguồn.
   Không suy ra từ tên công ty, không lấy của công ty cùng tên ở nước khác.

4. Cẩn thận với công ty trùng tên. Đối chiếu với gợi ý (website, địa chỉ, quốc gia) được cung
   cấp. Nếu không chắc đây có phải đúng công ty đó không → để `null`, đừng đoán.

5. URL trong "sources" phải là trang bạn thực sự đã đọc, không phải trang chủ chung chung hay
   URL tự dựng. Không dùng link tìm kiếm (google.com/search?...).

6. Giữ nguyên ngôn ngữ gốc của tên riêng và địa chỉ. Không dịch tên công ty.

ĐỊNH DẠNG TRẢ VỀ — chỉ một object JSON hợp lệ, không kèm giải thích, không bọc trong ```:

{JSON_SCHEMA_DESCRIPTION}

VÍ DỤ ĐÚNG (tìm được MST và địa chỉ, không tìm được năm thành lập):
{{"legal_name": "Công ty Cổ phần ABC", "tax_code": "0301234567", "founded_year": null,
  "size_label": null, "employee_range": null, "industry": ["Logistics"], "products": [],
  "address": "123 Lê Lợi, Quận 1, TP.HCM", "website": "https://abc.vn", "phone": null,
  "email": null, "description": "ABC cung cấp dịch vụ vận tải và kho vận tại miền Nam.",
  "sources": {{
    "legal_name": [{{"url": "https://masothue.com/0301234567", "title": "Mã số thuế ABC"}}],
    "tax_code": [{{"url": "https://masothue.com/0301234567", "title": "Mã số thuế ABC"}}],
    "industry": [{{"url": "https://abc.vn/gioi-thieu", "title": "Giới thiệu - ABC"}}],
    "address": [{{"url": "https://abc.vn/lien-he", "title": "Liên hệ - ABC"}}],
    "website": [{{"url": "https://abc.vn", "title": "Trang chủ ABC"}}]
  }}}}

VÍ DỤ SAI (tuyệt đối tránh):
- "founded_year": 2010 mà "sources" không có khoá "founded_year"  → phải để null
- "tax_code": "0123456789" đoán theo định dạng                    → phải để null
- "sources": {{"tax_code": [{{"url": "https://google.com/search?q=..."}}]}}  → không phải nguồn thật

Nếu không tìm được bất kỳ thông tin nào, trả về JSON với tất cả trường `null`/`[]` và
"sources" là object rỗng. Đó là câu trả lời hợp lệ."""


def build_user_prompt(company_name: str, hints: dict[str, Any] | None = None) -> str:
    """Ghép prompt tra cứu cho một công ty.

    `hints` lấy từ danh thiếp đã xác nhận (task 4.7) — website, địa chỉ, quốc gia, email…
    Gợi ý càng cụ thể càng giảm rủi ro model lấy nhầm công ty trùng tên (quy tắc 4).
    """
    lines = [f"Tên công ty in trên danh thiếp: {company_name}"]

    if hints:
        labels = {
            "website": "Website",
            "address": "Địa chỉ",
            "country": "Quốc gia",
            "email_domain": "Tên miền email",
            "phone": "Điện thoại",
        }
        hint_lines = [f"- {labels.get(k, k)}: {v}" for k, v in hints.items() if v]
        if hint_lines:
            lines.append("\nGợi ý từ danh thiếp (dùng để xác định đúng công ty):")
            lines.extend(hint_lines)

    lines.append(
        "\nHãy tra cứu Internet và trả về hồ sơ doanh nghiệp theo đúng định dạng JSON đã mô tả."
        " Nhớ: trường nào không có nguồn kiểm chứng được thì để null."
    )
    return "\n".join(lines)
