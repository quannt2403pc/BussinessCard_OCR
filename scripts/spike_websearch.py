"""Thử tìm kiếm Google qua CLIProxy; kiểm tra groundingChunks[].web.uri có ra URL thật không.

Chủ sở hữu: T | Task: 2.7

⚠️ **CHƯA CHẠY ĐƯỢC LẦN NÀO** — viết dựa trên đọc mã nguồn CLIProxyAPI, chưa kiểm chứng bằng lời
gọi thật. Xem `docs/adr-websearch.md` để biết cái gì đã chắc, cái gì còn đoán.

    python scripts/spike_websearch.py --list-models     # model nào hỗ trợ web search
    python scripts/spike_websearch.py --model gemini-3-flash

Ba câu hỏi phải trả lời cho F2: model nào chạy được `googleSearch`, gọi thế nào để bật, và
`groundingChunks[].web.uri` có ra **URL thật** không. Câu cuối là quan trọng nhất — nếu grounding
chỉ đưa link rút gọn không mở được thì cả thiết kế chống bịa R4 phải làm lại.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

BASE_URL = os.getenv("CLIPROXY_BASE_URL", "http://localhost:8317")
MGMT_KEY = os.getenv("CLIPROXY_MGMT_KEY", "")

#: Khoá tool bật tìm kiếm. camelCase, khớp `tool.Get("googleSearch")` ở
#: internal/runtime/executor/antigravity_executor.go:829.
TOOL_WEB_SEARCH: dict[str, dict[str, object]] = {"googleSearch": {}}

#: Nơi CLIProxy đặt kết quả grounding, theo
#: internal/runtime/executor/helps/antigravity_grounding_urls.go:71-74.
#: Có hai dạng — bọc trong "response" hoặc không — nên phải thử cả hai.
GROUNDING_PATHS = (
    "response.candidates.0.groundingMetadata.groundingChunks",
    "candidates.0.groundingMetadata.groundingChunks",
)

TEST_QUESTION = "Công ty Cổ phần FPT có mã số thuế là bao nhiêu? Trả lời ngắn gọn kèm nguồn."


def _get_path(data: object, path: str) -> object:
    """Đi theo đường dẫn kiểu 'a.0.b' trong dict/list lồng nhau, không có thì trả None."""
    current = data
    for part in path.split("."):
        if isinstance(current, list):
            if not part.isdigit() or int(part) >= len(current):
                return None
            current = current[int(part)]
        elif isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        else:
            return None
    return current


def list_models(client: httpx.Client) -> int:
    """Hỏi CLIProxy model nào của channel antigravity chạy được googleSearch.

    `supports_web_search` KHÔNG có trong models.json tĩnh — CLIProxy nạp lúc chạy, nên chỉ đọc
    được sau khi OAuth xong.
    """
    r = client.get(
        f"{BASE_URL}/v0/management/model-definitions/antigravity",
        headers={"Authorization": f"Bearer {MGMT_KEY}"},
    )
    r.raise_for_status()
    data = r.json()
    models = data if isinstance(data, list) else data.get("models", data)
    if not isinstance(models, list):
        print("Không đọc được danh sách model, response thô:")
        print(json.dumps(data, ensure_ascii=False, indent=2)[:1500])
        return 1

    print(f"{'Model':<32}{'web search?'}")
    print("-" * 46)
    for m in models:
        if isinstance(m, dict):
            supported = m.get("supports_web_search", False)
            print(f"{m.get('id', '?'):<32}{'CÓ' if supported else 'không'}")
    return 0


def try_search(client: httpx.Client, model: str) -> int:
    """Gọi generateContent có bật googleSearch, in ra URL grounding thu được."""
    body = {
        "contents": [{"role": "user", "parts": [{"text": TEST_QUESTION}]}],
        "tools": [TOOL_WEB_SEARCH],
    }
    r = client.post(f"{BASE_URL}/v1beta/models/{model}:generateContent", json=body, timeout=120.0)
    print(f"HTTP {r.status_code}")
    if r.status_code != 200:
        print(r.text[:800])
        return 1

    # Bẫy đã biết: route /v1beta/models/*action không có nhánh `default` trong switch
    # (docs/cliproxy-notes.md mục 6) → action sai trả 200 với body RỖNG, không phải 404.
    if not r.content.strip():
        print("Body rỗng — dấu hiệu action không được xử lý. Xem docs/cliproxy-notes.md mục 6.")
        return 1

    data = r.json()
    print("\n--- Văn bản trả lời ---")
    print(_get_path(data, "candidates.0.content.parts.0.text") or "(không có)")

    for path in GROUNDING_PATHS:
        chunks = _get_path(data, path)
        if isinstance(chunks, list) and chunks:
            print(f"\n--- groundingChunks tại '{path}' ({len(chunks)} nguồn) ---")
            for i, chunk in enumerate(chunks, 1):
                web = chunk.get("web", {}) if isinstance(chunk, dict) else {}
                print(f"{i}. {web.get('title', '(không tiêu đề)')}")
                print(f"   {web.get('uri', '(không có uri)')}")
            print(
                "\n>>> KIỂM TRA THỦ CÔNG: mở thử các URL trên. Nếu là link redirect của"
                " Vertex/Google mà không ra trang gốc thì phải xem lại thiết kế chống bịa R4"
                " (xem docs/adr-websearch.md)."
            )
            return 0

    print("\nKhông tìm thấy groundingChunks ở bất kỳ đường dẫn nào đã biết.")
    print("Toàn bộ response để soi bằng mắt:")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:2000])
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Spike web search qua CLIProxy (task 2.7)")
    parser.add_argument("--model", default="gemini-3-flash", help="Tên model cần thử")
    parser.add_argument(
        "--list-models", action="store_true", help="Chỉ liệt kê model hỗ trợ web search"
    )
    args = parser.parse_args()

    if not MGMT_KEY:
        print("Thiếu CLIPROXY_MGMT_KEY. Đặt biến môi trường rồi chạy lại.", file=sys.stderr)
        return 2

    with httpx.Client() as client:
        try:
            if args.list_models:
                return list_models(client)
            return try_search(client, args.model)
        except httpx.ConnectError:
            print(
                f"Không kết nối được {BASE_URL}. Service `cliproxy` đã chạy chưa? (task 2.1 của Q)",
                file=sys.stderr,
            )
            return 2
        except httpx.HTTPStatusError as err:
            # Sai management key 5 lần là bị ban IP 30 phút — KHÔNG retry (Task.md, I-05).
            print(
                f"Lỗi HTTP {err.response.status_code}: {err.response.text[:400]}", file=sys.stderr
            )
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
