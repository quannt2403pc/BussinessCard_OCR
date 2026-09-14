from collections.abc import Mapping, Sequence
from typing import Any

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

HINT_LABELS = {
    "website": "Website",
    "address": "Địa chỉ",
    "country": "Quốc gia",
    "email_domain": "Tên miền email",
    "phone": "Điện thoại",
}

RESEARCH_TASK = """Cần tìm: tên pháp lý đầy đủ (tên đăng ký kinh doanh, không phải tên thương hiệu),
mã số thuế, năm thành lập, nhãn quy mô, khoảng số nhân sự, ngành nghề, sản phẩm/dịch vụ chính,
địa chỉ trụ sở chính, website chính thức, điện thoại, email, mô tả ngắn 2-4 câu.

Quy tắc:
1. Chỉ ghi thông tin đọc được trên một trang web cụ thể, nói rõ mỗi thông tin lấy từ trang nào.
   Không đoán, không dùng kiến thức chung, không điền giá trị "hợp lý".
2. Mã số thuế chỉ ghi khi thấy con số đó in rõ trên trang nguồn. Không lấy của công ty cùng tên
   ở nước khác.
3. Cẩn thận công ty trùng tên: đối chiếu với gợi ý. Không chắc đúng công ty thì ghi
   "không chắc chắn".
4. Giữ nguyên ngôn ngữ gốc của tên riêng và địa chỉ, không dịch.
5. Không tìm thấy thì ghi "không tìm thấy". Thiếu thông tin không phải là thất bại, bịa mới là
   thất bại."""

STRUCTURE_PROMPT = """Dưới đây là KẾT QUẢ TRA CỨU về một công ty và DANH SÁCH NGUỒN đã dùng khi tra cứu.
Hãy chuyển kết quả tra cứu thành đúng một object JSON theo ĐỊNH DẠNG.

QUY TẮC:
1. Chỉ dùng thông tin có trong KẾT QUẢ TRA CỨU. Không thêm kiến thức riêng.
2. URL trong "sources" CHỈ được lấy từ DANH SÁCH NGUỒN, chép nguyên văn. Không tự tạo URL,
   không dùng link tìm kiếm.
3. Mỗi trường có giá trị phải có ít nhất một URL trong "sources" là trang chứa thông tin đó.
   Không có nguồn phù hợp trong danh sách → để null (hoặc [] với industry/products).
4. Thông tin ghi "không tìm thấy" hoặc "không chắc chắn" → null.
5. "founded_year" là số nguyên 4 chữ số.
6. Chỉ trả về JSON, không giải thích, không bọc trong ```.

ĐỊNH DẠNG:
{schema}

KẾT QUẢ TRA CỨU:
{research}

DANH SÁCH NGUỒN:
{sources}"""


def build_research_prompt(company_name: str, hints: Mapping[str, Any] | None = None) -> str:
    lines = [
        "Tra cứu Internet thông tin công khai về công ty sau.",
        "",
        f"Tên công ty in trên danh thiếp: {company_name}",
    ]
    hint_lines = [
        f"- {HINT_LABELS.get(key, key)}: {value}" for key, value in (hints or {}).items() if value
    ]
    if hint_lines:
        lines.append("Gợi ý từ danh thiếp để xác định đúng công ty:")
        lines.extend(hint_lines)
    lines.extend(["", RESEARCH_TASK])
    return "\n".join(lines)


def build_structure_prompt(research: str, sources: Sequence[tuple[str | None, str]]) -> str:
    source_lines = "\n".join(
        f"[{index}] {title or url} — {url}" for index, (title, url) in enumerate(sources, 1)
    )
    return STRUCTURE_PROMPT.format(
        schema=JSON_SCHEMA_DESCRIPTION, research=research, sources=source_lines
    )
