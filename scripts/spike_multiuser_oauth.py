"""Spike: CLIProxy có định tuyến lời gọi LLM theo từng credential không?

Chủ sở hữu: Q | Task: 12.1 | Rủi ro: R8 | Kết luận ghi ở `docs/adr-multiuser-oauth.md`

    docker compose exec api python -m scripts.spike_multiuser_oauth
    docker compose exec api python -m scripts.spike_multiuser_oauth --calls 8

**Câu hỏi phải trả lời.** Từ D13 mỗi người dùng tự bấm OAuth bằng Gmail của mình, nên CLIProxy sẽ
giữ nhiều credential cùng lúc. Nếu nó tự xoay vòng giữa các credential thì lời gọi của người A có
thể đi bằng tài khoản Google của người B — hỏng **âm thầm**, không lỗi nào báo, và làm trượt tiêu
chí **A10**. `docs/cliproxy-notes.md` (khảo sát 1.9) không có một dòng nào về chuyện này.

**Cách đo — không bao giờ đọc token.** Mỗi bản ghi trong `GET /v0/management/auth-files` có sẵn
bộ đếm `success`/`failed` riêng. Gọi model một lượt rồi so bộ đếm trước/sau là biết **đích danh**
credential nào vừa phục vụ. Đo gián tiếp như vậy an toàn hơn hẳn việc mở file credential ra xem,
và cũng là thứ duy nhất chạy được khi file token bị khoá quyền đọc.

**Ba pha:**

1. **Đếm credential.** Dưới 2 credential thì không có gì để nói về định tuyến — script vẫn chạy
   pha 3 (thứ kiểm được với 1 tài khoản) rồi dừng và nói rõ còn thiếu gì.
2. **Xoay vòng hay cố định?** Bắn N lượt gọi *không* tiền tố, quy mỗi lượt về đúng credential đã
   phục vụ nó. Phân bố đều → `round-robin` (mặc định của CLIProxy). Dồn hết vào một credential →
   `fill-first` hoặc session-affinity đang bật.
3. **Có chọn được credential theo từng request không?** CLIProxy cho gắn `prefix` vào credential,
   khi đó lời gọi tên `"<prefix>/<model>"` nhắm đúng credential ấy. Pha này kiểm hai chiều:
   tiền tố **có thật** phải làm bộ đếm của đúng credential đó tăng (và **chỉ** nó), còn tiền tố
   **bịa ra** phải bị từ chối.

⚠️ **Tiền tố lạ trả về đúng câu lỗi của I-03** — `400 unknown provider for model …`. Câu này nay
có **ba** nguyên nhân khác hẳn nhau: chưa kết nối OAuth, sai tên model, và tiền tố không credential
nào nhận. Đọc log mà không biết điều này thì rất dễ đi sửa nhầm chỗ.

⚠️ **Gọi sai management key 5 lần là bị ban IP 30 phút** (I-05). Ban tính theo **IP nguồn**: đo
2026-09-21 thấy host (`172.19.0.1`) bị chặn 403 tức thì trong khi container `api` (`172.19.0.6`)
vẫn 200. Vì vậy script này **phải chạy trong container `api`**, đừng chạy từ máy thật.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from dataclasses import dataclass

import httpx

from app.core.config import settings

MODEL = settings.llm_model
PING = {
    "contents": [{"role": "user", "parts": [{"text": "ping"}]}],
    "generationConfig": {"maxOutputTokens": 8},
}


@dataclass(frozen=True, slots=True)
class Credential:
    """Một credential như CLIProxy nhìn thấy. Không giữ token, chỉ giữ thứ đọc được công khai."""

    name: str
    email: str
    prefix: str | None
    success: int
    failed: int

    @property
    def label(self) -> str:
        return self.email or self.name


class Proxy:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def credentials(self) -> list[Credential]:
        r = await self.client.get("/v0/management/auth-files")
        r.raise_for_status()
        return [
            Credential(
                name=str(f.get("name", "")),
                email=str(f.get("email") or f.get("account") or ""),
                # ⚠️ `auth-files` **không trả trường `prefix`** — đo 2026-09-21: bản ghi có 26
                # khoá, không khoá nào là prefix. Nên tiền tố phải truyền vào bằng `--prefix`,
                # và chỉ kiểm được bằng **hành vi**, không kiểm bằng cách đọc lại cấu hình.
                prefix=None,
                success=int(f.get("success") or 0),
                failed=int(f.get("failed") or 0),
            )
            for f in r.json().get("files", [])
        ]

    async def call(self, model: str) -> tuple[int, str]:
        r = await self.client.post(f"/v1beta/models/{model}:generateContent", json=PING)
        return r.status_code, r.text

    async def served_by(self, model: str) -> tuple[str | None, int, str]:
        """Gọi một lượt, trả `(credential đã phục vụ, mã HTTP, trích lỗi)`.

        Quy về credential bằng **hiệu bộ đếm** chứ không tin bất cứ thứ gì trong response: response
        của Gemini không nói nó đi bằng tài khoản nào — đó chính là lý do rủi ro R8 khó thấy.
        """
        before = {c.name: c.success for c in await self.credentials()}
        code, text = await self.call(model)
        after = {c.name: c.success for c in await self.credentials()}
        moved = [name for name, n in after.items() if n > before.get(name, 0)]
        if len(moved) == 1:
            return moved[0], code, ""
        return None, code, text[:160]


async def phase_rotation(proxy: Proxy, creds: list[Credential], calls: int) -> Counter[str]:
    print(f"\n── Pha 2: bắn {calls} lượt gọi KHÔNG tiền tố, xem ai phục vụ ──")
    hits: Counter[str] = Counter()
    by_name = {c.name: c.label for c in creds}
    for i in range(1, calls + 1):
        name, code, err = await proxy.served_by(MODEL)
        if name is None:
            print(f"  lượt {i}: HTTP {code} — không quy được về credential nào. {err}")
            continue
        hits[name] += 1
        print(f"  lượt {i}: {by_name.get(name, name)}")
    return hits


async def phase_prefix(proxy: Proxy, creds: list[Credential], prefixes: dict[str, str]) -> bool:
    """Kiểm cơ chế `prefix`. Trả True nếu chọn được credential theo từng request.

    `prefixes` là ánh xạ `email -> tiền tố`, truyền qua `--prefix`. Không đọc từ `auth-files`
    được vì endpoint đó không trả trường này — xem ghi chú trong `Proxy.credentials()`.
    """
    print("\n── Pha 3: chọn credential bằng tiền tố model ──")

    code, text = await proxy.call(f"khong-credential-nao-nhan/{MODEL}")
    print(f"  tiền tố bịa ra → HTTP {code}")
    if code == 200:
        print("  ⚠️ BẤT THƯỜNG: tiền tố lạ vẫn chạy → CLIProxy bỏ qua tiền tố, không dùng để chọn.")
        return False
    print(f"    (đúng như mong đợi — {text[:120]})")

    tagged = [(c, prefixes[c.label]) for c in creds if c.label in prefixes]
    if not tagged:
        print("  Chưa truyền `--prefix` nào → chưa kiểm được chiều khẳng định.")
        print("  Cách gắn tiền tố + cú pháp: xem `docs/adr-multiuser-oauth.md` mục 4.")
        return False

    ok = True
    for cred, prefix in tagged:
        name, code, err = await proxy.served_by(f"{prefix}/{MODEL}")
        hit = name == cred.name
        ok = ok and hit
        print(
            f"  {prefix}/{MODEL} → HTTP {code} · "
            + (f"ĐÚNG credential {cred.label}" if hit else f"SAI — phục vụ bởi {name}. {err}")
        )
    return ok


async def run(calls: int, prefixes: dict[str, str]) -> int:
    base = str(settings.cliproxy_base_url).rstrip("/")
    headers = {"Authorization": f"Bearer {settings.cliproxy_mgmt_key}"}

    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=120) as client:
        proxy = Proxy(client)

        print("── Pha 1: credential đang có ──")
        try:
            creds = await proxy.credentials()
        except httpx.HTTPStatusError as exc:
            print(f"Không đọc được auth-files: HTTP {exc.response.status_code}", file=sys.stderr)
            if exc.response.status_code == 403:
                print(
                    "403 tức thì thường là **IP đang bị ban** vì sai key 5 lần (I-05). "
                    "Chạy script này TRONG container `api`, chờ 30 phút nếu vẫn 403.",
                    file=sys.stderr,
                )
            return 2

        for c in creds:
            print(
                f"  {c.label}  (prefix={c.prefix or '—'}, success={c.success}, failed={c.failed})"
            )
        if not creds:
            print("Chưa kết nối OAuth lần nào — bấm nút ở /settings trước.", file=sys.stderr)
            return 2

        selectable = await phase_prefix(proxy, creds, prefixes)

        if len(creds) < 2:
            print(
                "\n⚠️ Mới có 1 credential nên KHÔNG kết luận được về xoay vòng (pha 2).\n"
                "   Việc còn lại cần người làm tay: mở /settings ở một trình duyệt khác (hoặc cửa sổ\n"
                "   ẩn danh), bấm Kết nối bằng **Gmail thứ hai**, rồi chạy lại đúng lệnh này."
            )
            return 1

        hits = await phase_rotation(proxy, creds, calls)
        print("\n── Kết quả ──")
        by_name = {c.name: c.label for c in creds}
        for name, n in hits.most_common():
            print(f"  {by_name.get(name, name)}: {n}/{calls} lượt")
        spread = len([n for n in hits.values() if n]) > 1

        print(
            "\nĐịnh tuyến khi KHÔNG tiền tố: "
            + ("**xoay vòng giữa nhiều credential**" if spread else "dồn vào một credential")
        )
        print(
            "Chọn credential theo từng request: "
            + ("**ĐƯỢC** (qua tiền tố)" if selectable else "**CHƯA chứng minh được**")
        )
        print(
            "\n→ Phương án R8: "
            + (
                "(a) dùng thẳng cơ chế tiền tố"
                if selectable
                else "(b) mỗi người một container CLIProxy, hoặc (c) hạ yêu cầu — BÁO CHỦ DỰ ÁN NGAY"
            )
        )
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Spike định tuyến credential CLIProxy (task 12.1)")
    parser.add_argument(
        "--calls", type=int, default=6, help="Số lượt gọi không tiền tố ở pha 2 (mặc định 6)"
    )
    parser.add_argument(
        "--prefix",
        action="append",
        default=[],
        metavar="EMAIL=TIEN_TO",
        help="Tien to da gan cho mot credential, lap lai duoc. VD: --prefix a@gmail.com=ua",
    )
    args = parser.parse_args()
    prefixes: dict[str, str] = {}
    for item in args.prefix:
        email, _, prefix = item.partition("=")
        if not prefix:
            parser.error(f"--prefix phai co dang EMAIL=TIEN_TO, nhan duoc {item!r}")
        prefixes[email.strip()] = prefix.strip()
    return asyncio.run(run(calls=max(2, args.calls), prefixes=prefixes))


if __name__ == "__main__":
    raise SystemExit(main())
