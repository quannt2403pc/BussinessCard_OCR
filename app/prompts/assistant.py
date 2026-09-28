"""System prompt trợ lý: chỉ trả lời theo context, luôn trích dẫn nguồn.

Chủ sở hữu: Q | Task: 8.1

**File này là nơi duy nhất quyết định việc trợ lý có bịa hay không**, và đó là kết luận đo được:
điểm tương đồng của chunk đúng (thấp nhất 0.773) và của câu ngoài KB (cao nhất 0.819) **chồng lên
nhau**, nên không ngưỡng điểm nào tách được hai thứ. Tầng truy hồi **luôn** trả về vài chunk, kể
cả khi người dùng hỏi giá vàng — việc từ chối trả lời chỉ còn trông vào prompt ở đây.

Câu khó nhất của bộ nghiệm thu là *"Mã số thuế của Vinamilk là gì?"*: đúng lĩnh vực, KB có một
công ty sữa **khác**, và model *biết sẵn* đáp án từ dữ liệu huấn luyện. Quy tắc 3 sinh ra cho
đúng câu đó.

Định dạng đầu ra là **văn bản thường kèm dấu `[n]`**, không phải JSON: JSON vỡ (I-15) thì mất
**cả** câu trả lời, còn với `[n]` thì hỏng nặng nhất là trả lời đúng mà thiếu trích dẫn.

Không có `[n]` nào thì `citations` rỗng, và đó là sự thật: hàm parse **không bao giờ tự gán một
nguồn mà model không chỉ tới** — trích dẫn tự bịa trông y như trích dẫn thật.
"""

from __future__ import annotations

from collections.abc import Sequence

#: Câu từ chối chuẩn. `routers/chat.py` cũng trả đúng nó khi tầng truy hồi không tìm được chunk
#: nào — lúc đó không gọi model.
NO_ANSWER_TEXT = "Không có thông tin này trong dữ liệu đã nhập."

#: Câu nhắc khi KB rỗng hoàn toàn. Tách khỏi `NO_ANSWER_TEXT` vì việc người dùng cần làm khác
#: hẳn: một bên là "hỏi cái khác", một bên là "đi nhập dữ liệu đã".
EMPTY_KB_TEXT = (
    "Knowledge Base chưa có dữ liệu nào. Hãy quét và xác nhận vài danh thiếp, "
    "hoặc lập hồ sơ doanh nghiệp, rồi hỏi lại."
)

SYSTEM_PROMPT = f"""Bạn là trợ lý tra cứu nội bộ của một hệ thống quản lý danh thiếp và hồ sơ
doanh nghiệp đối tác. Bạn trả lời **chỉ dựa trên phần NGỮ CẢNH** được đưa kèm mỗi câu hỏi.

BẢY QUY TẮC — đọc kỹ, quy tắc 1-3 quan trọng hơn việc trả lời được câu hỏi:

1. CHỈ dùng dữ kiện có trong NGỮ CẢNH. Kiến thức sẵn có của bạn về các công ty, con người, mã số
   thuế, địa chỉ… KHÔNG được dùng, kể cả khi bạn chắc chắn nó đúng. Ngữ cảnh là toàn bộ thế giới
   của bạn trong lượt trả lời này.

2. Ngữ cảnh không chứa đáp án → trả lời đúng một câu, nguyên văn:
   "{NO_ANSWER_TEXT}"
   Không kèm trích dẫn, không kèm phỏng đoán, không gợi ý đáp án bạn tự biết. Nói không biết
   KHÔNG bị coi là thất bại; trả lời một dữ kiện không có trong ngữ cảnh MỚI là thất bại.

3. Ngữ cảnh nói về một công ty/người KHÁC với cái được hỏi thì đó KHÔNG phải câu trả lời. Ví dụ:
   được hỏi về công ty A, ngữ cảnh chỉ có công ty B cùng ngành → áp dụng quy tắc 2. Tuyệt đối
   không mượn con số của B để trả lời về A, và cũng đừng lấy con số bạn tự nhớ về A.

4. Mỗi dữ kiện nêu ra phải kèm dấu trích dẫn [n] ngay sau nó, n là số hiệu khối trong NGỮ CẢNH
   đã dùng. Nhiều nguồn cho cùng một ý thì ghi [1][3]. CHỈ trích dẫn khối bạn thật sự dùng, và
   chỉ dùng số hiệu có thật trong ngữ cảnh.

5. Câu hỏi dạng liệt kê ("có những ai", "sản phẩm gì") thì nêu ĐỦ các mục tìm được trong ngữ
   cảnh, đừng dừng ở mục đầu tiên.

6. Trả lời bằng ngôn ngữ của câu hỏi (mặc định tiếng Việt). Giữ nguyên chữ viết gốc của tên
   riêng, email, số điện thoại — không dịch, không phiên âm, không đổi định dạng số.

7. Ngắn gọn, đi thẳng vào đáp án. Không mở bài, không nhắc lại câu hỏi, không nói "theo ngữ cảnh
   được cung cấp" — dấu [n] đã làm việc đó rồi."""

#: Nhãn khối ngữ cảnh theo `kb_chunks.source_type`, dùng khi metadata thiếu `title`.
_SOURCE_LABELS = {
    "card": "Danh thiếp",
    "company_profile": "Hồ sơ doanh nghiệp",
}


def build_context(blocks: Sequence[str]) -> str:
    """Đánh số các khối ngữ cảnh thành `[1] …`, `[2] …` — cùng hệ số hiệu mà quy tắc 4 nói tới.

    Bắt đầu từ **1**, không phải 0: lệch quy ước thì model tự sửa lại thành 1 và mọi dấu `[n]`
    trỏ lệch đúng một khối — sai âm thầm, câu trả lời vẫn trôi chảy.
    """
    return "\n\n".join(f"[{index}] {block.strip()}" for index, block in enumerate(blocks, start=1))


def build_prompt(question: str, context: str, *, history: str = "") -> str:
    """Dựng prompt người dùng: lịch sử (nếu có) + ngữ cảnh + câu hỏi.

    Thứ tự **ngữ cảnh trước, câu hỏi sau** là cố ý: câu hỏi nằm ngay trước chỗ model bắt đầu sinh
    chữ nên không bị vùi dưới mấy nghìn ký tự ngữ cảnh.

    `history` nằm **trên** ngữ cảnh vì nó là bối cảnh phụ: quy tắc 1 chỉ tính trên NGỮ CẢNH, nếu
    không thì lượt 2 sẽ trích dẫn lại câu trả lời của chính mình ở lượt 1 như thể đó là nguồn.
    """
    parts: list[str] = []
    if history.strip():
        parts.append(
            "CÁC LƯỢT TRƯỚC trong cuộc hội thoại này (chỉ để hiểu người dùng đang nhắc tới ai, "
            "KHÔNG phải nguồn dữ kiện và không được trích dẫn):\n"
            f"{history.strip()}"
        )
    parts.append(f"NGỮ CẢNH:\n{context.strip()}" if context.strip() else "NGỮ CẢNH: (trống)")
    parts.append(f"CÂU HỎI: {question.strip()}")
    return "\n\n".join(parts)


def source_label(source_type: str) -> str:
    """Nhãn tiếng Việt của một loại nguồn, dùng cho tiêu đề khối ngữ cảnh và thẻ trích dẫn."""
    return _SOURCE_LABELS.get(source_type, "Nguồn")
