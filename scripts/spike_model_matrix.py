"""Spike: model nào của channel làm được việc gì? Đo bằng lời gọi thật, không tin cờ năng lực.

Chủ sở hữu: Q | Task: EX-12 | Vấn đề: I-33, I-12 | Kết luận ghi ở `docs/adr-model-per-feature.md`

    docker compose exec api python -m scripts.spike_model_matrix
    docker compose exec api python -m scripts.spike_model_matrix --models gemini-3-flash,claude-sonnet-4-6
    docker compose exec api python -m scripts.spike_model_matrix --prefix u1a2b3c4d5e6f

**Câu hỏi phải trả lời.** `EX-15` cho người dùng tự chọn model cho từng chức năng. Thả cả danh mục
vào ô chọn là mời họ tự làm hỏng luồng của mình: channel `antigravity` có 11 model (I-03) nhưng
**chưa ai đo model nào nhận ảnh** — 2.3 chỉ kiểm chứng vision cho đúng `gemini-3-flash` (I-33).
Chọn nhầm một model không nhận `inline_data` thì **mọi lượt quét hỏng**, và lỗi chỉ lộ ra lúc
người dùng bấm upload chứ không phải lúc bấm lưu.

**Vì sao đo bằng lời gọi thật.** Cờ `supports_web_search` của CLIProxy **không dùng được để chọn
model** — I-12 đo 2026-09-11 thấy nó báo *không* cho 11/11 model kể cả model thực tế tra cứu được,
rồi 2026-09-21 lại báo *có* cho mọi model Gemini. `docs/adr-websearch.md` mục Q5 đã chốt: chọn
bằng lời gọi thật. Spike này làm đúng thế cho **cả ba** năng lực.

**Ba phép đo, đúng ba chức năng của `EX-15`:**

| Phép đo | Chức năng cần nó | Đạt nghĩa là |
|---------|------------------|--------------|
| `json`  | cả ba            | HTTP 200 + bóc được object JSON bằng đúng `services/llm_json.py` mà `ocr.py` dùng |
| `ảnh`   | Quét danh thiếp  | nhận `inline_data` và đọc ra được chữ có thật trên tấm thẻ mẫu |
| `web`   | Lập hồ sơ        | bật `googleSearch` và trả về `groundingMetadata.groundingChunks` — không có nguồn thì **trượt A5** |

⚠️ Phép đo `ảnh` cố ý hỏi một chuỗi **có thật trên ảnh**, không hỏi "bạn có thấy ảnh không".
Model không nhận ảnh vẫn trả lời trơn tru câu hỏi thứ hai — đó là *âm tính giả* tự mình tạo ra.

⚠️ Mỗi model tốn 3 lượt gọi thật. Cả danh mục 11 model là ~33 lượt: bật `--models` khi chỉ cần đo
lại một vài cái, đừng quét cả danh mục cho vui.

⚠️ **Chạy trong container `api`**, không chạy từ máy thật: sai management key 5 lần là ban IP 30
phút và host với container không chung IP (I-05, đo ở 12.1).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services import llm
from app.services.cliproxy_client import CliProxyClient, CliProxyError
from app.services.llm_json import JsonExtractError, extract_json_object

#: Ảnh dùng cho phép đo `ảnh`. Thẻ demo do T tạo ở 11.7 — nằm sẵn trong repo nên spike chạy được
#: trên máy sạch, và `samples/cards/` thì tới nay vẫn rỗng (task 3.9 đã cắt).
CARD_IMAGE = Path("samples/demo/en-01-clear.png")

#: Chuỗi phải đọc được từ tấm thẻ trên (`en-01-clear.png`: NGUYEN DUC ANH · COTECCONS
#: CONSTRUCTION JOINT STOCK COMPANY). Hỏi một chuỗi **có thật trên ảnh** là cách duy nhất phân
#: biệt "model nhìn thấy ảnh" với "model đoán bừa cho xong" — và `coteccons` thì không đoán ra
#: được từ một prompt không có ảnh.
CARD_NEEDLE = "coteccons"

JSON_PROMPT = 'Trả về đúng một object JSON, không thêm chữ nào: {"ok": true, "ten": "kiem tra"}'
VISION_PROMPT = (
    "Đọc tấm danh thiếp trong ảnh. Trả về đúng một object JSON, không thêm chữ nào: "
    '{"full_name": "<họ tên in trên thẻ>", "company": "<tên công ty in trên thẻ>"}'
)
WEB_PROMPT = (
    "Mã số thuế của Công ty Cổ phần FPT là gì? Trả lời ngắn gọn một câu, dựa trên nguồn tra cứu."
)

#: Ngắn nhất có thể mà vẫn đủ để phân biệt đạt/trượt — đây là 33 lượt gọi thật, không phải test.
MAX_TOKENS = 200


@dataclass(slots=True)
class Probe:
    """Kết quả một phép đo trên một model."""

    ok: bool
    seconds: float
    note: str = ""

    @property
    def mark(self) -> str:
        return "✅" if self.ok else "❌"


@dataclass(slots=True)
class Row:
    model: str
    json_probe: Probe
    vision: Probe
    web: Probe

    @property
    def dung_cho_ocr(self) -> bool:
        return self.json_probe.ok and self.vision.ok

    @property
    def dung_cho_enrich(self) -> bool:
        return self.json_probe.ok and self.web.ok

    @property
    def dung_cho_chat(self) -> bool:
        return self.json_probe.ok


def _full_name(model: str, prefix: str | None) -> str:
    """Tên model gửi cho CLIProxy. Có tiền tố thì nhắm đúng credential của một người (12.1)."""
    return f"{prefix}/{model}" if prefix else model


async def _probe_json(model: str, client: CliProxyClient) -> Probe:
    started = time.perf_counter()
    try:
        text = await llm.generate_text(
            JSON_PROMPT, model=model, max_output_tokens=MAX_TOKENS, client=client
        )
    except (llm.LLMError, CliProxyError) as exc:
        return Probe(False, time.perf_counter() - started, f"{type(exc).__name__}")
    elapsed = time.perf_counter() - started
    try:
        data = extract_json_object(text)
    except JsonExtractError:
        return Probe(False, elapsed, "trả về chữ nhưng không bóc được JSON")
    return Probe(bool(data.get("ok")), elapsed, "" if data.get("ok") else "JSON thiếu khoá `ok`")


async def _probe_vision(model: str, client: CliProxyClient, image: bytes) -> Probe:
    started = time.perf_counter()
    try:
        text = await llm.generate_vision(
            VISION_PROMPT,
            image,
            mime_type="image/png",
            model=model,
            max_output_tokens=MAX_TOKENS,
            client=client,
        )
    except (llm.LLMError, CliProxyError) as exc:
        return Probe(False, time.perf_counter() - started, f"{type(exc).__name__}")
    elapsed = time.perf_counter() - started
    try:
        data = extract_json_object(text)
    except JsonExtractError:
        return Probe(False, elapsed, "nhận ảnh nhưng không trả JSON")
    doc = " ".join(str(v) for v in data.values()).casefold()
    if CARD_NEEDLE.casefold() in doc:
        return Probe(True, elapsed)
    return Probe(False, elapsed, f"đọc ra {doc[:40]!r}, không khớp chữ trên thẻ")


async def _probe_web(model: str, client: CliProxyClient) -> Probe:
    """Không hỏi model "bạn tra được không" — đọc thẳng `groundingChunks` như `enrichment.py`."""
    started = time.perf_counter()
    payload: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": WEB_PROMPT}]}],
        "tools": [llm.TOOL_GOOGLE_SEARCH],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": MAX_TOKENS},
    }
    try:
        data = await llm.generate_content(payload, model=model, client=client)
    except (llm.LLMError, CliProxyError) as exc:
        return Probe(False, time.perf_counter() - started, f"{type(exc).__name__}")
    elapsed = time.perf_counter() - started

    candidates = data.get("candidates") or []
    candidate = candidates[0] if candidates else None
    metadata = candidate.get("groundingMetadata") if isinstance(candidate, Mapping) else None
    chunks = metadata.get("groundingChunks") if isinstance(metadata, Mapping) else None
    if chunks:
        return Probe(True, elapsed, f"{len(chunks)} nguồn")
    return Probe(False, elapsed, "gọi được nhưng KHÔNG có groundingChunks")


async def measure(model: str, prefix: str | None, image: bytes) -> Row:
    name = _full_name(model, prefix)
    async with CliProxyClient(timeout=llm.LLM_TIMEOUT) as client:
        json_probe = await _probe_json(name, client)
        vision = await _probe_vision(name, client, image)
        web = await _probe_web(name, client)
    return Row(model, json_probe, vision, web)


def _print_table(rows: list[Row]) -> None:
    print("\n## Bảng năng lực đo được\n")
    print("| Model | JSON | Ảnh | Web | Quét thẻ | Lập hồ sơ | Trợ lý | Ghi chú |")
    print("|-------|------|-----|-----|----------|-----------|--------|---------|")
    for row in rows:
        notes = " · ".join(
            f"{label}: {probe.note}"
            for label, probe in (
                ("json", row.json_probe),
                ("ảnh", row.vision),
                ("web", row.web),
            )
            if probe.note
        )
        print(
            f"| `{row.model}` "
            f"| {row.json_probe.mark} {row.json_probe.seconds:.1f}s "
            f"| {row.vision.mark} {row.vision.seconds:.1f}s "
            f"| {row.web.mark} {row.web.seconds:.1f}s "
            f"| {'✅' if row.dung_cho_ocr else '❌'} "
            f"| {'✅' if row.dung_cho_enrich else '❌'} "
            f"| {'✅' if row.dung_cho_chat else '❌'} "
            f"| {notes or '—'} |"
        )


def _print_allowlists(rows: list[Row]) -> None:
    """In đúng thứ `EX-15` cần: ba danh sách model được phép chọn của ba chức năng."""
    print("\n## Danh sách cho phép chọn (dán vào `docs/adr-model-per-feature.md`)\n")
    for feature, ok in (
        ("ocr — Quét danh thiếp", [r.model for r in rows if r.dung_cho_ocr]),
        ("enrich — Lập hồ sơ", [r.model for r in rows if r.dung_cho_enrich]),
        ("chat — Trợ lý AI", [r.model for r in rows if r.dung_cho_chat]),
    ):
        print(f"- **{feature}**: {', '.join(f'`{m}`' for m in ok) if ok else '(không model nào)'}")


async def run(models: list[str] | None, prefix: str | None) -> int:
    if not CARD_IMAGE.is_file():
        print(f"Không thấy ảnh mẫu {CARD_IMAGE} — chạy trong container `api`?", file=sys.stderr)
        return 2
    image = CARD_IMAGE.read_bytes()

    if models is None:
        try:
            async with CliProxyClient() as client:
                models = await client.model_ids()
        except CliProxyError as exc:
            print(f"Không lấy được danh mục model: {exc}", file=sys.stderr)
            return 2
    if not models:
        print("Danh mục model rỗng — đã kết nối OAuth chưa? (vào /settings)", file=sys.stderr)
        return 2

    print(
        f"Channel `{settings.cliproxy_auth_provider}` · {len(models)} model · 3 phép đo mỗi model"
    )
    if prefix:
        print(f"Tiền tố credential: `{prefix}` — mọi lượt gọi đi bằng đúng tài khoản đó (12.1)")
    print(f"Ảnh mẫu: {CARD_IMAGE} · chuỗi phải đọc ra: {CARD_NEEDLE!r}\n")

    rows: list[Row] = []
    for index, model in enumerate(models, start=1):
        print(f"[{index}/{len(models)}] {model} …", flush=True)
        row = await measure(model, prefix, image)
        print(
            f"    json {row.json_probe.mark}  ảnh {row.vision.mark}  web {row.web.mark}",
            flush=True,
        )
        rows.append(row)

    _print_table(rows)
    _print_allowlists(rows)

    mac_dinh = settings.llm_model
    default_row = next((r for r in rows if r.model == mac_dinh), None)
    if default_row and not (default_row.dung_cho_ocr and default_row.dung_cho_enrich):
        print(
            f"\n⚠️ Model mặc định `{mac_dinh}` KHÔNG đủ năng lực cho mọi chức năng — "
            "`NULL = dùng mặc định` của EX-13 sẽ hỏng cho chức năng đó.",
            file=sys.stderr,
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--models",
        help="Danh sách model cách nhau bằng dấu phẩy. Bỏ trống = cả danh mục của channel.",
    )
    parser.add_argument(
        "--prefix",
        help="Tiền tố credential (`users.cliproxy_auth_file` của ai đó, xem 12.1). "
        "Bỏ trống thì CLIProxy tự xoay vòng — vẫn đo được năng lực.",
    )
    args = parser.parse_args()
    models = [m.strip() for m in args.models.split(",") if m.strip()] if args.models else None
    return asyncio.run(run(models, args.prefix))


if __name__ == "__main__":
    raise SystemExit(main())
