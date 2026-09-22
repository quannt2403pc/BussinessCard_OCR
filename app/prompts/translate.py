"""Prompt Việt hoá danh thiếp sau khi quét: dịch chức vụ / loại hình pháp nhân, phiên âm tên riêng.

Chủ sở hữu: Q | Task: EX-01 | xem Task.md

**Vì sao là lượt gọi model thứ hai chứ không nhét thêm trường vào `prompts/ocr.py`:**
prompt OCR đã được đo hai lần (task 9.3 rồi 10.8) và đang ở 100% trên ảnh sắc nét; ghi chú của
9.3 chốt hẳn *"không sửa gì ở quy tắc 1–3"*. Quy tắc 3 của nó — **giữ nguyên chữ bản địa, không
dịch, không phiên âm** — là thứ giữ cho `ocr_raw_json` còn đối chiếu được với ảnh. Trộn thêm
việc dịch vào cùng một lời gọi là vừa phá quy tắc đó, vừa buộc phải đo lại toàn bộ A3, vừa làm
một lần dịch sai kéo theo cả kết quả đọc chữ. Tách ra thì lượt Việt hoá hỏng cũng chỉ mất phần
Việt hoá: thẻ vẫn quét được, vẫn review được, bấm *Dịch lại* là xong.

Ba loại chữ trên danh thiếp cần ba cách xử lý khác hẳn nhau, và đó là toàn bộ nội dung prompt:

1. **Chức vụ và loại hình pháp nhân** (`部長`, `주식회사`, `Co., Ltd.`) — **dịch nghĩa**. Đây là
   từ vựng chung, có từ tương đương trong tiếng Việt, và người đọc cần hiểu nghĩa.
2. **Tên riêng** (người, công ty, địa danh) — **không dịch nghĩa**, chuyển sang đúng cách người
   Việt vẫn viết: tiếng Nhật → Romaji, tiếng Trung → âm Hán Việt, còn lại → dạng Latin thông
   dụng. `さくらテクノロジー` là *Sakura Technology*, không phải *Công nghệ Hoa Anh Đào*.
3. **Số, email, website** — không gửi lên, `services/normalize.py` (3.6) đã lo và chúng không
   có gì để dịch.

⚠️ Như mọi prompt trong dự án: có ghi "chỉ trả JSON, không bọc trong khối ```" nhưng **đừng tin
là đủ** (I-15). `services/llm_json.py` gỡ hàng rào trước khi `json.loads()`.
"""

from __future__ import annotations

import json
from typing import Any

#: Trường được Việt hoá. Khoá đầu ra là `<tên trường>_vi`, trùng tên cột `business_cards`
#: (revision `0006`) nên `services/translate.py` không phải dựng lớp ánh xạ tên.
TRANSLATABLE_FIELDS: tuple[str, ...] = (
    "full_name",
    "job_title",
    "company_name_raw",
    "address",
)

#: Cột lưu kết quả, theo đúng thứ tự trên. `company_name_raw` → `company_name_vi`: bỏ hậu tố
#: `_raw` cho khỏi có cột tên `company_name_raw_vi` đọc như một lỗi đánh máy.
VI_COLUMNS: dict[str, str] = {
    "full_name": "full_name_vi",
    "job_title": "job_title_vi",
    "company_name_raw": "company_name_vi",
    "address": "address_vi",
}

#: Cách tên riêng được chuyển sang chữ Latin/tiếng Việt — ghi vào `translation_meta.name_method`
#: để người dùng biết vì sao `李伟` thành `Lý Vĩ` chứ không phải `Li Wei`.
NAME_METHODS: tuple[str, ...] = ("romaji", "han_viet", "romanization", "latin", "none")

JSON_SCHEMA_DESCRIPTION = """{
  "full_name_vi":    string | null,  // ho ten theo cach viet trong tieng Viet
  "job_title_vi":    string | null,  // chuc vu DICH NGHIA sang tieng Viet
  "company_name_vi": string | null,  // loai hinh phap nhan dich nghia + ten rieng phien am
  "address_vi":      string | null,  // dia chi: tu chi loai dich, ten rieng phien am
  "source_language": string,         // ma ngon ngu cua chu tren the: ja | zh | ko | th | ru | ...
  "script":          string,         // he chu viet: Latn | Jpan | Hani | Hang | Cyrl | Arab | Thai | ...
  "name_method":     string,         // romaji | han_viet | romanization | latin | none
  "note":            string | null   // mot cau ngan neu co cho khong chac, khong thi null
}"""

SYSTEM_PROMPT = """Bạn là biên tập viên Việt hoá dữ liệu danh thiếp. Đầu vào là các trường đã
được trích xuất từ một tấm danh thiếp, giữ nguyên chữ viết gốc. Nhiệm vụ: viết lại chúng theo
đúng cách một người Việt sẽ viết, để người đọc tiếng Việt dùng được ngay.

QUY TẮC — đọc hết trước khi trả lời:

1. CHỨC VỤ: dịch nghĩa sang tiếng Việt. Không giữ nguyên tiếng nước ngoài khi tiếng Việt có từ
   tương đương.
   代表取締役 / 대표이사 / 总经理 / CEO → "Tổng giám đốc"; 部長 / 부장 → "Trưởng phòng";
   営業部長 → "Trưởng phòng Kinh doanh"; Sales Manager → "Trưởng phòng Kinh doanh";
   CTO → "Giám đốc Công nghệ"; 主任 / 대리 → "Chuyên viên"; Senior Engineer → "Kỹ sư cao cấp".
   Chức vụ ghép "bộ phận + cấp bậc" thì dịch cả hai vế, cấp bậc đứng trước theo lối tiếng Việt.

2. TÊN CÔNG TY: tách làm hai phần và xử lý khác nhau.
   a. **Loại hình pháp nhân** (株式会社, 有限会社, 주식회사, 有限公司, 股份有限公司, Co., Ltd.,
      JSC, Inc., LLC, GmbH, S.A., Pte Ltd, Sdn Bhd, PT, OOO…) → DỊCH sang tiếng Việt:
      "Công ty Cổ phần", "Công ty TNHH", "Tập đoàn", "Tổng công ty". Đặt ở ĐẦU tên theo lối
      tiếng Việt, kể cả khi bản gốc đặt ở cuối.
   b. **Tên riêng** của công ty → KHÔNG dịch nghĩa, chỉ phiên âm theo quy tắc 3.
   Ví dụ: 東京テック株式会社 → "Công ty Cổ phần Tokyo Tech"; 深圳市远景电子有限公司 → "Công ty
   TNHH Điện tử Viễn Cảnh Thâm Quyến"; Hanwha Precision Machinery Co., Ltd. → "Công ty TNHH
   Máy móc Chính xác Hanwha".
   Phần mô tả ngành nghề trong tên công ty (電子 / Electronics / 機械) thì DỊCH, vì nó là từ
   chung chứ không phải tên riêng.

3. TÊN RIÊNG (tên người, tên riêng công ty, tên địa danh) — viết theo đúng thói quen tiếng Việt:
   * Tiếng Nhật → **Romaji** (Hepburn, không dấu trường âm), giữ thứ tự Họ Tên như in trên thẻ:
     田中 太郎 → "Tanaka Taro". Chữ katakana vốn mượn từ phương Tây thì trả về dạng gốc:
     テクノロジー → "Technology", サクラ → "Sakura".
   * Tiếng Trung → **âm Hán Việt**: 李伟 → "Lý Vĩ", 深圳 → "Thâm Quyến", 北京 → "Bắc Kinh".
   * Tiếng Hàn → **chuyển tự Latin** theo lối thông dụng: 김민준 → "Kim Min-jun", 서울 → "Seoul".
   * Mọi ngôn ngữ khác không dùng chữ Latin (Nga, Ả Rập, Thái, Hy Lạp, Hindi…) → chuyển tự sang
     chữ Latin theo dạng người Việt hay gặp nhất: Иванов → "Ivanov", สมชาย → "Somchai".
   * Đã viết bằng chữ Latin sẵn (Anh, Pháp, Đức, Indonesia…) → GIỮ NGUYÊN, không đổi gì.
   * Địa danh đã có tên quen dùng trong tiếng Việt thì dùng tên đó: 東京 → "Tokyo",
     上海 → "Thượng Hải", Москва → "Moskva".

4. ĐỊA CHỈ: dịch từ chỉ loại đơn vị (区 → "Quận", 市 → "Thành phố", 省 → "Tỉnh", 路/通り/Street →
   "Đường", ビル/Building → "Toà nhà", 階/Floor → "Tầng"), phiên âm tên riêng theo quy tắc 3, và
   **giữ nguyên thứ tự các thành phần như in trên thẻ**. Số nhà, mã bưu chính, số tầng giữ
   nguyên chữ số.

5. KHÔNG thêm thông tin không có trong đầu vào. Không đoán tên đầy đủ, không đoán tên công ty mẹ,
   không bổ sung quốc gia nếu thẻ không in. Không chắc cách phiên âm thì chọn dạng phổ thông
   nhất và ghi một câu vào `note`.

6. Trường đầu vào là `null` → trường ra cũng `null`. Trường vốn đã là tiếng Việt, hoặc đã ở dạng
   Latin và không cần đổi gì → chép lại nguyên văn (hệ thống tự bỏ bản trùng, bạn không phải lo).

7. Trả về ĐÚNG một object JSON theo schema được cho. Không thêm lời dẫn, không giải thích,
   không bọc trong khối ```."""


def build_prompt(fields: dict[str, Any], *, language: str | None = None) -> str:
    """Prompt cho một tấm thẻ. `fields` chỉ chứa các khoá trong `TRANSLATABLE_FIELDS`.

    Ngôn ngữ do OCR đoán được (`language_detected`) đi vào prompt như **gợi ý, không phải mệnh
    lệnh**: thẻ song ngữ hay thẻ Nhật in tên công ty bằng chữ Hán làm trường đó sai thường xuyên,
    và model nhìn thẳng vào chữ thì đoán đúng hơn. Vì vậy vẫn bắt nó tự trả `source_language` —
    trường đó mới là thứ được ghi vào `translation_meta`.
    """
    payload = json.dumps(fields, ensure_ascii=False, indent=2)
    hint = (
        f"\n\nOCR đoán ngôn ngữ chính của thẻ là `{language}` — đối chiếu với chữ thật, "
        "sai thì cứ theo chữ thật."
        if language
        else ""
    )
    return (
        "Việt hoá các trường sau của một danh thiếp:\n\n"
        f"{payload}{hint}\n\n"
        "Trả về JSON đúng schema sau:\n\n"
        f"{JSON_SCHEMA_DESCRIPTION}\n\nChỉ trả về JSON."
    )
