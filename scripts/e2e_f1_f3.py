"""Kịch bản kiểm thử đầu–cuối nhóm F1 (OCR danh thiếp) + F3 (trợ lý AI/RAG) trên hệ thật.

Chủ sở hữu: Q | Task: 10.1, 10.3 | xem Task.md

    python -m scripts.e2e_f1_f3                  # chạy cả hai bộ: flow + edge
    python -m scripts.e2e_f1_f3 --suite flow     # chỉ 10.1 — luồng nghiệp vụ
    python -m scripts.e2e_f1_f3 --suite edge     # chỉ 10.3 — trường hợp biên
    python -m scripts.e2e_f1_f3 --suite outage   # 10.3 phần cắt dịch vụ (tự stop/start container)
    python -m scripts.e2e_f1_f3 --keep           # giữ lại dữ liệu đã tạo để xem trên UI

**Chạy ở MÁY, không phải trong container** — cùng lý do với `scripts/check_multilang_ocr.py`
(task 9.3): ảnh danh thiếp dựng bằng phông chữ của Windows, image `python:3.12-slim` không có
phông nào. Script gọi API qua `localhost:8000` nên stack phải đang chạy (`docker compose up -d`).

Ranh giới của bộ này — nói trước để không ai đọc nhầm kết quả:

* **Không phải phép đo tiêu chí A3.** A3 đòi 30 ảnh *chụp thật* (task 3.9 của T, vẫn chưa có),
  phép đo là task 7.8. Ở đây ảnh dựng bằng phông nên chữ sắc nét tuyệt đối. Bộ này trả lời câu
  "luồng có chạy đúng đầu–cuối không", không phải "model đọc ảnh thật chính xác bao nhiêu".
* **Không đụng F2.** Hồ sơ doanh nghiệp là của T (task 10.6/10.7). Chỗ duy nhất chạm tới F2 là
  kiểm `confirm` có gắn được `company_id` — đó là điểm nối đã chốt ở họp D2, không phải test F2.
* **Mọi dữ liệu tạo ra đều bị xoá ở cuối** (trừ `--keep`). Chạy trên DB dev đang có dữ liệu
  demo nên script không được để lại rác — I-24 đã cho thấy rác dữ liệu thử làm hỏng truy hồi.

Bộ `outage` **dừng container thật** (`cliproxy`, `embedder`) rồi bật lại trong `finally`. Đây là
cách duy nhất kiểm được đúng điều D10 yêu cầu: "mất kết nối OAuth / CLIProxy chết". Không chạy
chung với `flow`/`edge` vì trong lúc nó chạy thì mọi lời gọi model đều hỏng.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# Windows: stdout mặc định cp1252, in tiếng Việt có dấu ra pipe/file là UnicodeEncodeError.
# Cùng cách xử lý với `.claude/hooks/*.py`. Không có dòng này thì `... | tee log.txt` chết ngay
# dòng in đầu tiên — và bộ kiểm thử mà không ghi log lại được thì dùng vào việc gì.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

BASE_URL = "http://localhost:8000"

#: Gọi model mất 3–5 giây/ảnh (đo ở 9.3). Chờ rộng tay: timeout ngắn làm test đỏ vì mạng chậm
#: chứ không vì mã sai, và đó là loại đỏ dạy người ta bỏ qua màu đỏ.
TIMEOUT = 180.0

#: Phông Latin có sẵn trên Windows.
LATIN_FONTS = ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf")

#: Ngưỡng UI tô vàng trường đáng kiểm (`routers/cards.py::LOW_CONFIDENCE`).
LOW_CONFIDENCE = 0.75


# --------------------------------------------------------------------------- dựng ảnh


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in LATIN_FONTS:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise FileNotFoundError(f"Không có phông Latin nào trong {LATIN_FONTS}")


def render_card(lines: tuple[tuple[str, int], ...], *, seed: str = "") -> bytes:
    """Vẽ một danh thiếp 1000×600 nền trắng chữ đen.

    `seed` in mờ ở góc dưới bằng màu gần trắng: hai thẻ khác `seed` ra hai file khác nhau nên
    hash khác nhau, tức chống trùng (task 3.1) không nuốt mất thẻ thứ hai — nhưng chữ mờ tới
    mức không vào được trường nào. Cần thế vì nhiều ca phải upload *hai thẻ khác nhau về nội
    dung* mà nội dung lại do chính ca đó quy định.
    """
    image = Image.new("RGB", (1000, 600), "white")
    draw = ImageDraw.Draw(image)
    y = 60
    for text, size in lines:
        if text:
            draw.text((60, y), text, font=_font(size), fill="black")
        y += size + 14
    if seed:
        draw.text((10, 580), seed, font=_font(9), fill=(250, 250, 250))

    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def blur(data: bytes, radius: float = 3.2) -> bytes:
    """Làm nhoè một ảnh đã dựng — mô phỏng ảnh chụp rung/lệch nét."""
    with Image.open(BytesIO(data)) as image:
        blurred = image.convert("RGB").filter(ImageFilter.GaussianBlur(radius))
        buffer = BytesIO()
        blurred.save(buffer, format="JPEG", quality=90)
        return buffer.getvalue()


def landscape() -> bytes:
    """Ảnh KHÔNG phải danh thiếp: dải màu trời–cỏ, không một chữ nào."""
    image = Image.new("RGB", (900, 600))
    draw = ImageDraw.Draw(image)
    for y in range(600):
        if y < 380:
            draw.line([(0, y), (900, y)], fill=(120 + y // 6, 170 + y // 9, 235))
        else:
            draw.line([(0, y), (900, y)], fill=(40 + (y - 380) // 3, 120 + (y - 380) // 4, 45))
    draw.ellipse((700, 60, 820, 180), fill=(255, 240, 150))
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


# --------------------------------------------------------------------------- dữ liệu mẫu

VN_CARD = (
    ("CÔNG TY CỔ PHẦN LOGISTICS HẢI ĐĂNG", 28),
    ("", 8),
    ("Nguyễn Thị Mai Anh", 30),
    ("Giám đốc Kinh doanh", 22),
    ("", 8),
    ("ĐT: 0912 345 678", 20),
    ("Email: maianh.nguyen@haidang-logistics.vn", 19),
    ("Địa chỉ: 145 Nguyễn Văn Linh, Quận 7, TP. Hồ Chí Minh", 18),
    ("www.haidang-logistics.vn", 20),
)

EN_CARD = (
    ("NORTHWIND MARINE SUPPLIES LTD.", 28),
    ("", 8),
    ("Daniel Whitmore", 30),
    ("Chief Technology Officer", 22),
    ("", 8),
    ("Mobile: +44 7700 900123", 20),
    ("daniel.whitmore@northwind-marine.co.uk", 19),
    ("22 Harbour Road, Southampton SO14 3QT, United Kingdom", 18),
    ("northwind-marine.co.uk", 20),
)

#: Thẻ hai mặt: mặt trước tên/chức vụ, mặt sau liên hệ. Ghép hai mặt thành MỘT bản ghi nằm
#: ngoài phạm vi bản demo (Plan.md mục 1.4) — ca này kiểm hệ thống *cư xử đúng như đã tuyên bố*:
#: hai ảnh thành hai bản ghi, không cái nào chết, không cái nào giả vờ ghép.
TWO_SIDED_FRONT = (
    ("ANDO KOUGYOU CO., LTD.", 26),
    ("", 8),
    ("Hiroshi Ando", 30),
    ("Managing Director", 22),
)
TWO_SIDED_BACK = (
    ("ANDO KOUGYOU CO., LTD.", 26),
    ("", 8),
    ("Tel: +81 6 6123 4567", 20),
    ("hiroshi.ando@ando-kougyou.jp", 19),
    ("3-5-2 Honmachi, Chuo-ku, Osaka 541-0053, Japan", 18),
)


# --------------------------------------------------------------------------- khung chạy


@dataclass
class Case:
    """Một ca kiểm thử đã chạy xong."""

    code: str
    title: str
    ok: bool
    detail: str = ""


@dataclass
class Runner:
    """Gom kết quả + dọn dẹp. Mỗi ca tự quyết đạt/trượt bằng `check()`."""

    http: httpx.Client
    cases: list[Case] = field(default_factory=list)
    card_ids: set[str] = field(default_factory=set)
    _failed_in_case: list[str] = field(default_factory=list)

    def check(self, ok: bool, message: str) -> bool:
        """Ghi nhận một khẳng định trong ca đang chạy. Trả lại chính `ok` để rẽ nhánh tiếp."""
        if not ok:
            self._failed_in_case.append(message)
        return ok

    def run_case(self, code: str, title: str, body: Callable[[], str | None]) -> Case:
        self._failed_in_case = []
        print(f"[{code}] {title} …", flush=True)
        try:
            detail = body() or ""
        except Exception as exc:  # noqa: BLE001 — một ca nổ không được dừng cả bộ
            case = Case(code, title, ok=False, detail=f"ngoại lệ: {exc!r}")
        else:
            if self._failed_in_case:
                case = Case(code, title, ok=False, detail=" | ".join(self._failed_in_case))
            else:
                case = Case(code, title, ok=True, detail=detail)
        self.cases.append(case)
        print(f"      {'ĐẠT ' if case.ok else 'TRƯỢT'} {case.detail}", flush=True)
        return case

    # --- tiện ích gọi API ---------------------------------------------------

    def upload(self, image: bytes, filename: str) -> httpx.Response:
        response = self.http.post(
            "/api/cards/upload",
            files={"file": (filename, image, "image/jpeg")},
            timeout=TIMEOUT,
        )
        if response.status_code in (200, 201):
            self.card_ids.add(response.json()["card"]["id"])
        return response

    def cleanup(self) -> None:
        for card_id in sorted(self.card_ids):
            try:
                self.http.delete(f"/api/cards/{card_id}", timeout=30.0)
            except httpx.HTTPError as exc:
                print(f"  (không xoá được card {card_id}: {exc})")


# --------------------------------------------------------------------------- bộ FLOW (10.1)


def suite_flow(run: Runner) -> None:
    """Luồng nghiệp vụ F1 + F3: upload → review → sửa → xác nhận → KB → hỏi đáp → xoá."""
    state: dict[str, object] = {}
    vn_image = render_card(VN_CARD, seed="e2e-flow-vn")

    # ---------------------------------------------------------------- F1
    def f1_01() -> str:
        response = run.upload(vn_image, "e2e-vn.jpg")
        if not run.check(response.status_code == 201, f"mong 201, nhận {response.status_code}"):
            return response.text[:200]
        body = response.json()
        card = body["card"]
        state["card_id"] = card["id"]
        run.check(not body["duplicate"], "ảnh mới mà bị đánh dấu trùng")
        run.check(body.get("ocr_error") is None, f"ocr_error={body.get('ocr_error')!r}")

        required = (
            "full_name",
            "job_title",
            "company_name_raw",
            "email",
            "phone",
            "address",
            "website",
        )
        missing = [name for name in required if not card.get(name)]
        run.check(not missing, f"thiếu trường: {missing}")
        run.check(card["status"] in ("needs_review", "confirmed"), f"status lạ: {card['status']!r}")
        run.check(
            card.get("language_detected") == "vi",
            f"language_detected={card.get('language_detected')!r}, mong 'vi'",
        )
        return f"7/7 trường, {body.get('ocr_ms')}ms, status={card['status']}"

    run.run_case("F1-01", "Upload 1 ảnh danh thiếp tiếng Việt → trích xuất đủ 7 trường", f1_01)

    def f1_02() -> str:
        response = run.upload(vn_image, "e2e-vn-lan-2.jpg")
        run.check(response.status_code == 200, f"mong 200, nhận {response.status_code}")
        body = response.json()
        run.check(body["duplicate"] is True, "không nhận ra ảnh đã quét")
        run.check(body["card"]["id"] == state.get("card_id"), "trả về bản ghi khác bản ghi cũ")
        run.check(body.get("ocr_ms") is None, "vẫn gọi lại model cho ảnh trùng")
        return f"trả lại card cũ trong {body['elapsed_ms']}ms, không gọi model"

    run.run_case("F1-02", "Upload lại đúng ảnh đó → nhận ra trùng, không gọi lại model", f1_02)

    def f1_03() -> str:
        card_id = state["card_id"]
        found = run.http.get("/api/cards", params={"q": "Hải Đăng"}, timeout=TIMEOUT).json()
        run.check(
            any(item["id"] == card_id for item in found["items"]),
            "tìm theo tên công ty không ra thẻ vừa upload",
        )

        by_status = run.http.get(
            "/api/cards", params={"status": "needs_review", "size": 50}, timeout=TIMEOUT
        ).json()
        run.check(
            all(item["status"] == "needs_review" for item in by_status["items"]),
            "bộ lọc status trả về cả thẻ trạng thái khác",
        )

        paged = run.http.get("/api/cards", params={"size": 1, "page": 1}, timeout=TIMEOUT).json()
        run.check(len(paged["items"]) <= 1, "size=1 mà trả nhiều hơn 1 dòng")
        run.check(paged["pages"] == max(1, -(-paged["total"] // 1)), "số trang tính sai")
        return f"tìm/lọc/phân trang đúng trên {paged['total']} bản ghi"

    run.run_case("F1-03", "Danh sách: tìm kiếm + lọc trạng thái + phân trang", f1_03)

    def f1_04() -> str:
        detail = run.http.get(f"/api/cards/{state['card_id']}", timeout=TIMEOUT).json()
        run.check(detail.get("ocr_raw_json") is not None, "chi tiết không kèm ocr_raw_json")
        confidence = detail.get("confidence") or {}
        run.check(bool(confidence), "không có điểm confidence nào")
        run.check(
            all(0.0 <= float(v) <= 1.0 for v in confidence.values()),
            f"confidence ngoài khoảng 0–1: {confidence}",
        )
        return f"{len(confidence)} trường có confidence, thấp nhất {min(confidence.values()):.2f}"

    run.run_case("F1-04", "Chi tiết thẻ kèm ocr_raw_json + confidence từng trường", f1_04)

    def f1_05() -> str:
        response = run.http.get(f"/api/cards/{state['card_id']}/image", timeout=TIMEOUT)
        run.check(response.status_code == 200, f"mong 200, nhận {response.status_code}")
        run.check(
            response.headers.get("content-type") == "image/jpeg",
            f"content-type={response.headers.get('content-type')!r}",
        )
        run.check(len(response.content) > 1000, "ảnh trả về quá nhỏ, nghi hỏng")
        return f"{len(response.content)} byte JPEG"

    run.run_case("F1-05", "Tải ảnh đã tiền xử lý của thẻ", f1_05)

    def f1_06() -> str:
        payload = {"phone": "0912 345 678", "email": "  MaiAnh.Nguyen@HaiDang-Logistics.VN  "}
        response = run.http.patch(f"/api/cards/{state['card_id']}", json=payload, timeout=TIMEOUT)
        run.check(response.status_code == 200, f"mong 200, nhận {response.status_code}")
        card = response.json()
        run.check(card["phone"] == "+84912345678", f"SĐT chưa chuẩn hoá: {card['phone']!r}")
        run.check(
            card["email"] == "maianh.nguyen@haidang-logistics.vn",
            f"email chưa chuẩn hoá: {card['email']!r}",
        )
        run.check(card["status"] != "confirmed", "PATCH tự ý xác nhận thẻ")
        return f"phone={card['phone']}, email={card['email']}, status giữ nguyên"

    run.run_case("F1-06", "Sửa tay → chuẩn hoá SĐT/email, không tự xác nhận", f1_06)

    def f1_07() -> str:
        response = run.http.post(f"/api/cards/{state['card_id']}/confirm", json={}, timeout=TIMEOUT)
        run.check(response.status_code == 200, f"mong 200, nhận {response.status_code}")
        body = response.json()
        run.check(body["status"] == "confirmed", f"status={body['status']!r}")
        run.check(body["company_matched"] is True, f"chưa gắn công ty: {body.get('detail')!r}")
        run.check(body["kb_indexed"] is True, f"chưa vào KB: {body.get('detail')!r}")
        state["company_id"] = body["company_id"]
        return f"confirmed, company_id={body['company_id']}, kb_indexed=True"

    run.run_case("F1-07", "Xác nhận thẻ → gắn công ty + đưa vào Knowledge Base", f1_07)

    def f1_08() -> str:
        response = run.http.patch(
            f"/api/cards/{state['card_id']}",
            json={"job_title": "Giám đốc Kinh doanh Miền Nam"},
            timeout=TIMEOUT,
        )
        run.check(response.status_code == 200, f"mong 200, nhận {response.status_code}")
        card = response.json()
        run.check(card["job_title"] == "Giám đốc Kinh doanh Miền Nam", "không lưu được sửa đổi")
        run.check(card["status"] == "confirmed", "sửa thẻ đã xác nhận làm mất trạng thái")
        return "thẻ đã xác nhận vẫn sửa được, KB đồng bộ lại ngay (routers/cards.py::_sync_kb)"

    run.run_case("F1-08", "Sửa thẻ đã xác nhận → KB đồng bộ lại, trạng thái giữ nguyên", f1_08)

    def f1_09() -> str:
        images = [
            ("e2e-batch-1.jpg", render_card(EN_CARD, seed="e2e-batch-1")),
            ("e2e-batch-2.jpg", render_card(VN_CARD, seed="e2e-batch-2")),
            ("e2e-batch-3.jpg", render_card(EN_CARD, seed="e2e-batch-3")),
        ]
        response = run.http.post(
            "/api/cards/batch-upload",
            files=[("files", (name, data, "image/jpeg")) for name, data in images],
            timeout=TIMEOUT,
        )
        if not run.check(response.status_code == 202, f"mong 202, nhận {response.status_code}"):
            return response.text[:200]
        job_id = response.json()["job_id"]

        deadline = time.monotonic() + 180
        job: dict = {}
        while time.monotonic() < deadline:
            job = run.http.get(f"/api/cards/batch-jobs/{job_id}", timeout=TIMEOUT).json()
            if job["finished"]:
                break
            time.sleep(2)

        run.check(bool(job.get("finished")), "job không kết thúc trong 180 giây")
        for item in job.get("items", []):
            if item.get("card_id"):
                run.card_ids.add(item["card_id"])
        run.check(job.get("failed") == 0, f"có {job.get('failed')} ảnh hỏng: {job.get('items')}")
        run.check(job.get("done") == 3, f"done={job.get('done')}, mong 3")
        return f"3/3 ảnh quét xong, {job.get('done')} done / {job.get('failed')} failed"

    run.run_case("F1-09", "Upload hàng loạt 3 ảnh → theo dõi tiến trình tới khi xong", f1_09)

    # ---------------------------------------------------------------- F3
    def f3_01() -> str:
        response = run.http.post(
            "/api/chat",
            json={"question": "Ai phụ trách kinh doanh ở công ty Hải Đăng Logistics?"},
            timeout=TIMEOUT,
        )
        if not run.check(response.status_code == 200, f"mong 200, nhận {response.status_code}"):
            return response.text[:300]
        body = response.json()
        state["session_id"] = body["session_id"]
        run.check(body["context_chunks"] > 0, "không truy hồi được chunk nào")
        run.check(bool(body["citations"]), "trả lời mà không có trích dẫn nào")
        run.check(
            "Mai Anh" in body["answer"],
            f"câu trả lời không nêu đúng người: {body['answer'][:160]!r}",
        )
        return f"{body['context_chunks']} chunk, {len(body['citations'])} trích dẫn, {body['elapsed_ms']}ms"

    run.run_case("F3-01", "Hỏi về người trên thẻ vừa xác nhận → trả lời đúng kèm trích dẫn", f3_01)

    def f3_02() -> str:
        response = run.http.post(
            "/api/chat",
            json={"question": "Email của người đó là gì?", "session_id": state.get("session_id")},
            timeout=TIMEOUT,
        )
        if not run.check(response.status_code == 200, f"mong 200, nhận {response.status_code}"):
            return response.text[:300]
        body = response.json()
        run.check(body["session_id"] == state.get("session_id"), "mở phiên mới thay vì hỏi tiếp")
        run.check(
            "maianh.nguyen@haidang-logistics.vn" in body["answer"].lower(),
            f"không hiểu 'người đó' là ai: {body['answer'][:160]!r}",
        )
        return "hỏi tiếp bằng đại từ vẫn bám đúng ngữ cảnh lượt trước"

    run.run_case("F3-02", "Hỏi tiếp trong cùng phiên bằng đại từ → dùng được lịch sử", f3_02)

    def f3_03() -> str:
        body = run.http.get(f"/api/chat/{state['session_id']}", timeout=TIMEOUT).json()
        roles = [message["role"] for message in body["messages"]]
        run.check(roles == ["user", "assistant", "user", "assistant"], f"lịch sử lệch: {roles}")
        last = body["messages"][-1]
        run.check(last.get("citations") is not None, "lượt trả lời không lưu trích dẫn")
        return f"{len(roles)} lượt, trích dẫn được lưu cùng câu trả lời"

    run.run_case("F3-03", "Đọc lại phiên hội thoại → đủ lượt, kèm trích dẫn đã lưu", f3_03)

    def f3_04() -> str:
        response = run.http.post(
            "/api/chat",
            json={
                "question": "Công ty Hải Đăng Logistics có những ai?",
                "filters": {"source_type": "card"},
            },
            timeout=TIMEOUT,
        )
        if not run.check(response.status_code == 200, f"mong 200, nhận {response.status_code}"):
            return response.text[:300]
        body = response.json()
        kinds = {citation["source_type"] for citation in body["citations"]}
        run.check(kinds <= {"card"}, f"bộ lọc source_type=card vẫn lọt nguồn khác: {kinds}")
        return f"{len(body['citations'])} trích dẫn, tất cả đều là danh thiếp"

    run.run_case("F3-04", "Lọc phạm vi truy hồi theo source_type=card", f3_04)

    def f3_05() -> str:
        response = run.http.post(
            "/api/chat",
            json={"question": "Giá cổ phiếu của Vinamilk hôm nay là bao nhiêu?"},
            timeout=TIMEOUT,
        )
        if not run.check(response.status_code == 200, f"mong 200, nhận {response.status_code}"):
            return response.text[:300]
        body = response.json()
        answer = body["answer"].lower()
        admits = any(
            phrase in answer
            for phrase in (
                "không có thông tin",
                "không tìm thấy",
                "không đủ thông tin",
                "không có dữ liệu",
                "chưa có thông tin",
            )
        )
        run.check(admits, f"bịa câu trả lời ngoài KB: {body['answer'][:200]!r}")
        return f"nhận là không biết ({body['context_chunks']} chunk ngữ cảnh)"

    run.run_case("F3-05", "Hỏi thứ không có trong KB → nhận không biết, không bịa", f3_05)

    def f3_06() -> str:
        response = run.http.post("/api/kb/reindex", timeout=TIMEOUT)
        if not run.check(response.status_code == 200, f"mong 200, nhận {response.status_code}"):
            return response.text[:300]
        body = response.json()
        run.check(body["total_chunks"] > 0, "KB rỗng sau khi index lại")
        run.check(body["cards"] > 0, "không index lại danh thiếp nào")
        return (
            f"{body['cards']} thẻ + {body['profiles']} hồ sơ → {body['chunks']} chunk, "
            f"tổng {body['total_chunks']}, {body['elapsed_ms']}ms"
        )

    run.run_case("F3-06", "Index lại toàn bộ Knowledge Base", f3_06)

    def f3_07() -> str:
        card_id = str(state["card_id"])
        response = run.http.delete(f"/api/cards/{card_id}", timeout=TIMEOUT)
        run.check(response.status_code == 204, f"mong 204, nhận {response.status_code}")
        run.card_ids.discard(card_id)

        gone = run.http.get(f"/api/cards/{card_id}", timeout=TIMEOUT)
        run.check(gone.status_code == 404, f"thẻ đã xoá vẫn đọc được: {gone.status_code}")

        asked = run.http.post(
            "/api/chat",
            json={"question": "Ai phụ trách kinh doanh ở công ty Hải Đăng Logistics?"},
            timeout=TIMEOUT,
        ).json()
        still = [c for c in asked["citations"] if c.get("source_id") == card_id]
        run.check(not still, f"trợ lý vẫn trích dẫn thẻ đã xoá: {still}")
        return "xoá thẻ → chunk KB đi theo, trợ lý không còn trích dẫn nguồn đã xoá"

    run.run_case("F3-07", "Xoá thẻ đã xác nhận → chunk KB bị gỡ, không còn bị trích dẫn", f3_07)


# --------------------------------------------------------------------------- bộ EDGE (10.3)


def suite_edge(run: Runner) -> None:
    """Trường hợp biên: ảnh mờ, ảnh không phải danh thiếp, thẻ 2 mặt, file quá lớn/sai/rỗng."""

    def e_01() -> str:
        response = run.upload(blur(render_card(EN_CARD, seed="e2e-blur")), "e2e-blur.jpg")
        if not run.check(response.status_code == 201, f"mong 201, nhận {response.status_code}"):
            return response.text[:200]
        card = response.json()["card"]
        confidence = card.get("confidence") or {}
        scored = {k: float(v) for k, v in confidence.items() if card.get(k)}
        low = {k: v for k, v in scored.items() if v < LOW_CONFIDENCE}
        run.check(card["status"] != "confirmed", "ảnh mờ mà tự động xác nhận")
        run.check(bool(scored), "không chấm confidence cho trường nào")
        run.check(
            bool(low) or card["status"] == "needs_review",
            f"ảnh mờ mà mọi trường đều ≥ {LOW_CONFIDENCE}: {scored}",
        )
        return (
            f"status={card['status']}, {len(low)}/{len(scored)} trường dưới "
            f"{LOW_CONFIDENCE} → UI tô vàng đúng chỗ cần kiểm"
        )

    run.run_case("E-01", "Ảnh mờ → vẫn nhận, hạ confidence, không tự xác nhận", e_01)

    def e_02() -> str:
        response = run.upload(landscape(), "e2e-not-a-card.jpg")
        if not run.check(response.status_code == 201, f"mong 201, nhận {response.status_code}"):
            return response.text[:200]
        card = response.json()["card"]
        # `ocr_raw_json` chỉ có trong `CardDetailOut`, không có trong `CardOut` của response
        # upload — phải gọi thêm một lượt chi tiết. Lượt chạy đầu 2026-09-21 báo TRƯỢT ở đây
        # chính vì đọc nhầm chỗ, xem `docs/bugs-f1-f3.md` mục "Ghi nhận không phải bug".
        detail = run.http.get(f"/api/cards/{card['id']}", timeout=TIMEOUT).json()
        raw = detail.get("ocr_raw_json") or {}
        run.check(
            raw.get("is_business_card") is False,
            f"is_business_card={raw.get('is_business_card')!r}, mong False",
        )
        filled = [
            name
            for name in ("full_name", "company_name_raw", "email", "phone", "address")
            if card.get(name)
        ]
        run.check(not filled, f"ảnh phong cảnh mà vẫn điền được trường: {filled}")
        run.check(bool(card.get("notes")), "không ghi chú lý do cho người dùng biết vì sao trống")
        return f"is_business_card=False, 0 trường bịa, notes={str(card.get('notes'))[:60]!r}"

    run.run_case(
        "E-02", "Ảnh không phải danh thiếp → is_business_card=false, không bịa trường", e_02
    )

    def e_03() -> str:
        front = run.upload(render_card(TWO_SIDED_FRONT, seed="e2e-2mat-truoc"), "2mat-truoc.jpg")
        back = run.upload(render_card(TWO_SIDED_BACK, seed="e2e-2mat-sau"), "2mat-sau.jpg")
        run.check(
            front.status_code == 201 and back.status_code == 201,
            f"mong 201/201, nhận {front.status_code}/{back.status_code}",
        )
        a, b = front.json()["card"], back.json()["card"]
        run.check(a["id"] != b["id"], "hai mặt bị gộp nhầm thành một bản ghi")
        run.check(bool(a.get("full_name")), "mặt trước không đọc ra tên")
        run.check(bool(b.get("email")), "mặt sau không đọc ra email")
        return (
            "hai mặt = hai bản ghi độc lập, cả hai đều quét được. Ghép hai mặt thành MỘT "
            "bản ghi nằm ngoài phạm vi bản demo (Plan.md 1.4) — hạn chế đã biết, không phải bug"
        )

    run.run_case("E-03", "Danh thiếp 2 mặt → 2 bản ghi riêng, đúng phạm vi đã tuyên bố", e_03)

    def e_04() -> str:
        oversized = b"\xff\xd8\xff\xe0" + b"\x00" * (11 * 1024 * 1024)
        response = run.http.post(
            "/api/cards/upload",
            files={"file": ("e2e-qua-lon.jpg", oversized, "image/jpeg")},
            timeout=TIMEOUT,
        )
        run.check(response.status_code == 413, f"mong 413, nhận {response.status_code}")
        detail = str(response.json().get("detail", ""))
        run.check("MB" in detail, f"thông báo không nói giới hạn là bao nhiêu: {detail!r}")
        return f"413 — {detail}"

    run.run_case("E-04", "File vượt giới hạn dung lượng → 413, dừng ngay lúc đọc", e_04)

    def e_05() -> str:
        pdf = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
        response = run.http.post(
            "/api/cards/upload",
            files={"file": ("e2e-tai-lieu.pdf", pdf, "application/pdf")},
            timeout=TIMEOUT,
        )
        run.check(response.status_code == 400, f"mong 400, nhận {response.status_code}")
        return f"400 — {str(response.json().get('detail', ''))[:120]}"

    run.run_case("E-05", "File sai định dạng (PDF) → 400 với lý do đọc được", e_05)

    def e_06() -> str:
        response = run.http.post(
            "/api/cards/upload",
            files={"file": ("e2e-rong.jpg", b"", "image/jpeg")},
            timeout=TIMEOUT,
        )
        run.check(response.status_code == 400, f"mong 400, nhận {response.status_code}")
        return f"400 — {str(response.json().get('detail', ''))[:120]}"

    run.run_case("E-06", "File rỗng → 400", e_06)

    def e_07() -> str:
        tiny = render_card((("X", 20),), seed="e2e-tran")
        files = [("files", (f"qua-nhieu-{i}.jpg", tiny, "image/jpeg")) for i in range(51)]
        response = run.http.post("/api/cards/batch-upload", files=files, timeout=TIMEOUT)
        run.check(response.status_code == 400, f"mong 400, nhận {response.status_code}")
        return f"400 — {str(response.json().get('detail', ''))[:120]}"

    run.run_case("E-07", "Batch quá 50 ảnh → 400, từ chối trước khi ghi gì", e_07)

    def e_08() -> str:
        response = run.http.get("/api/cards", params={"status": "khong-ton-tai"}, timeout=TIMEOUT)
        run.check(response.status_code == 400, f"mong 400, nhận {response.status_code}")
        run.check(
            "pending" in str(response.json().get("detail", "")),
            "không liệt kê giá trị hợp lệ cho người dùng",
        )
        return "400 và nói rõ các giá trị hợp lệ, không trả danh sách rỗng gây hiểu nhầm"

    run.run_case("E-08", "Lọc theo trạng thái không tồn tại → 400, không trả rỗng", e_08)

    def e_09() -> str:
        missing = run.http.get("/api/cards/00000000-0000-0000-0000-000000000000", timeout=TIMEOUT)
        run.check(missing.status_code == 404, f"UUID lạ: mong 404, nhận {missing.status_code}")
        malformed = run.http.get("/api/cards/khong-phai-uuid", timeout=TIMEOUT)
        run.check(
            malformed.status_code == 422,
            f"UUID sai định dạng: mong 422, nhận {malformed.status_code}",
        )
        session = run.http.get("/api/chat/00000000-0000-0000-0000-000000000000", timeout=TIMEOUT)
        run.check(session.status_code == 404, f"phiên lạ: mong 404, nhận {session.status_code}")
        return "404 cho id không có, 422 cho id sai định dạng — cả thẻ lẫn phiên chat"

    run.run_case("E-09", "ID không tồn tại / sai định dạng → 404 và 422", e_09)

    def e_10() -> str:
        response = run.http.post("/api/chat", json={"question": "   "}, timeout=TIMEOUT)
        run.check(response.status_code == 422, f"mong 422, nhận {response.status_code}")
        long_question = "a" * 2001
        too_long = run.http.post("/api/chat", json={"question": long_question}, timeout=TIMEOUT)
        run.check(
            too_long.status_code == 422, f"câu quá dài: mong 422, nhận {too_long.status_code}"
        )
        return "câu hỏi rỗng và câu hỏi quá 2000 ký tự đều bị chặn ở tầng schema"

    run.run_case("E-10", "Câu hỏi rỗng / quá dài → 422 trước khi chạm truy hồi", e_10)


# --------------------------------------------------------------------------- bộ OUTAGE (10.3)


def _compose(*args: str) -> None:
    subprocess.run(
        ["docker", "compose", *args],
        cwd=Path(__file__).resolve().parent.parent,
        check=True,
        capture_output=True,
    )


def suite_outage(run: Runner) -> None:
    """Cắt dịch vụ thật: CLIProxy chết (mất OAuth) và embedder chết."""

    def o_01() -> str:
        _compose("stop", "cliproxy")
        try:
            status = run.http.get("/api/integration/status", timeout=TIMEOUT).json()
            # Hợp đồng ghi ở đầu `routers/integration.py` mục 2: **CLIProxy chết ≠ đã ngắt kết
            # nối**. Token nằm trong volume `cliproxy_auths`, container chết không làm mất nó,
            # nên `connected` giữ giá trị cache còn `reachable` mới là thứ tụt xuống false —
            # `templates/settings.html::refreshStatus()` đọc đúng `reachable` và vẽ badge xám
            # "Không gọi được CLIProxy", không vẽ đỏ "Chưa kết nối". Lượt chạy đầu 2026-09-21
            # báo TRƯỢT ở đây vì bài test đòi `connected=false`, tức đòi sai; xem
            # `docs/bugs-f1-f3.md` mục "Ghi nhận không phải bug".
            run.check(
                status["reachable"] is False,
                f"CLIProxy đã chết mà vẫn báo gọi được: reachable={status['reachable']}",
            )
            run.check(
                status["from_cache"] is True,
                "không đánh dấu đây là giá trị cache lần kiểm tra cuối",
            )
            run.check(bool(status.get("detail")), "không nói lý do mất kết nối")

            response = run.upload(render_card(EN_CARD, seed="e2e-outage"), "e2e-outage.jpg")
            run.check(
                response.status_code == 201,
                f"CLIProxy chết làm hỏng cả lượt upload: {response.status_code}",
            )
            if response.status_code == 201:
                body = response.json()
                run.check(bool(body.get("ocr_error")), "không ghi lại lý do quét hỏng")
                run.check(
                    body["card"]["status"] == "pending",
                    f"status={body['card']['status']!r}, mong 'pending'",
                )

            chat = run.http.post(
                "/api/chat", json={"question": "Công ty nào làm logistics?"}, timeout=TIMEOUT
            )
            run.check(
                chat.status_code in (502, 503),
                f"chat khi model chết: mong 502/503, nhận {chat.status_code}",
            )
            return (
                "ảnh vẫn được lưu ở trạng thái pending kèm lý do; chat trả "
                f"{chat.status_code} chứ không 500"
            )
        finally:
            _compose("start", "cliproxy")
            time.sleep(6)

    run.run_case("O-01", "CLIProxy chết / mất OAuth → upload vẫn lưu ảnh, chat báo 503", o_01)

    def o_02() -> str:
        card_id: str | None = None
        _compose("stop", "embedder")
        try:
            response = run.upload(render_card(VN_CARD, seed="e2e-no-embedder"), "e2e-noemb.jpg")
            if not run.check(response.status_code == 201, f"mong 201, nhận {response.status_code}"):
                return response.text[:200]
            card_id = response.json()["card"]["id"]

            confirmed = run.http.post(f"/api/cards/{card_id}/confirm", json={}, timeout=TIMEOUT)
            run.check(
                confirmed.status_code == 200,
                f"embedder chết làm hỏng nút Xác nhận: {confirmed.status_code}",
            )
            if confirmed.status_code == 200:
                body = confirmed.json()
                run.check(body["status"] == "confirmed", "không xác nhận được")
                run.check(body["kb_indexed"] is False, "báo đã index trong khi embedder đang chết")
                run.check(
                    "embedder" in str(body.get("detail", "")).lower()
                    or "Index lại" in str(body.get("detail", "")),
                    f"thông báo không chỉ được việc cần làm: {body.get('detail')!r}",
                )

            chat = run.http.post(
                "/api/chat", json={"question": "Công ty nào làm logistics?"}, timeout=TIMEOUT
            )
            run.check(
                chat.status_code == 503,
                f"chat khi embedder chết: mong 503, nhận {chat.status_code}",
            )
            return "xác nhận vẫn xong (F3 hỏng không chặn F1), chat trả 503 kèm hướng xử lý"
        finally:
            _compose("start", "embedder")
            for _ in range(40):
                try:
                    if httpx.get("http://localhost:8001/health", timeout=3.0).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(3)
            if card_id:
                run.http.post("/api/kb/reindex", timeout=TIMEOUT)

    run.run_case("O-02", "Embedder chết → xác nhận vẫn xong, KB vá lại bằng reindex", o_02)


# --------------------------------------------------------------------------- main

SUITES: dict[str, Callable[[Runner], None]] = {
    "flow": suite_flow,
    "edge": suite_edge,
    "outage": suite_outage,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Kiểm thử đầu–cuối F1 + F3 (task 10.1, 10.3)")
    parser.add_argument("--suite", choices=(*SUITES, "all"), default="all")
    parser.add_argument("--keep", action="store_true", help="Giữ lại dữ liệu đã tạo")
    args = parser.parse_args()

    names = list(SUITES) if args.suite == "all" else [args.suite]
    # `outage` dừng container nên không chạy lẫn với hai bộ kia khi gọi `--suite all`.
    if args.suite == "all":
        names = ["flow", "edge"]

    started = time.perf_counter()
    with httpx.Client(base_url=BASE_URL) as http:
        try:
            http.get("/health", timeout=10.0).raise_for_status()
        except httpx.HTTPError as exc:
            print(f"Không gọi được {BASE_URL}/health — stack chưa chạy? ({exc})")
            return 2

        run = Runner(http=http)
        try:
            for name in names:
                print(f"\n=========== BỘ {name.upper()} ===========")
                SUITES[name](run)
        finally:
            if not args.keep:
                run.cleanup()

    failed = [case for case in run.cases if not case.ok]
    print(f"\n=========== TỔNG KẾT ({time.perf_counter() - started:.0f}s) ===========")
    for case in run.cases:
        print(f"  {'ĐẠT ' if case.ok else 'TRƯỢT'} {case.code}  {case.title}")
    print(f"\n{len(run.cases) - len(failed)}/{len(run.cases)} ca ĐẠT")
    for case in failed:
        print(f"  ✗ {case.code}: {case.detail}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
