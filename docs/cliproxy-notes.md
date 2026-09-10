# Khảo sát CLIProxyAPI

> Chủ sở hữu: **T** · Task **1.9** · Ngày khảo sát: **2026-09-10**
> Mã nguồn khảo sát: `C:\Users\pc\source\repos\CLIProxyAPI` — commit **`7fac6b15`**
> Phương pháp: đọc mã nguồn Go + `config.example.yaml`. Chạy thử container để ở D2 (task 2.1).

⚠️ **Bốn phát hiện làm sai kế hoạch hiện tại — đọc mục 3, 4, 5, 6 trước khi code D2.**
Ba trong số đó sẽ làm task 2.2/2.4/2.5 của Q hỏng nếu không xử lý.

---

## 0. Đính chính so với Plan.md

| Plan.md ghi | Thực tế | Ảnh hưởng |
|-------------|---------|-----------|
| Mã nguồn ở `C:\FSoft\ojt\CliProxy` | Thực tế ở `C:\Users\pc\source\repos\CLIProxyAPI` | Chỉ là đường dẫn tài liệu |
| Khảo sát tại commit `ecc9aa72` | Nay đã là `7fac6b15` | Kết luận cũ vẫn đúng — đã kiểm lại, xem mục 7 |
| Management API "chỉ bật khi `home.enabled = false`" | Đúng, nhưng `home` **không có** trong `config.example.yaml` → mặc định `false` → không phải làm gì | Không cần cấu hình |
| Dùng `get-auth-status` để hiện badge "Đã kết nối" | **Sai** — endpoint này chỉ theo dõi 1 phiên OAuth đang chạy | Xem mục 4 |
| Provider OAuth cho Gemini là `antigravity` | Đúng, nhưng **model khả dụng khác hẳn** `gemini-flash-latest` | Xem mục 5 |

---

## 1. Cách chạy

CLIProxyAPI là một binary Go, image build từ `golang:1.26-bookworm` → `debian:bookworm`,
`CMD ["./CLIProxyAPI"]`, `EXPOSE 8317`.

| Thứ | Giá trị |
|-----|---------|
| Image công khai | `eceasy/cli-proxy-api:latest` (`docker-compose.yml` của họ dùng `pull_policy: always`) |
| Cổng API chính | `8317` |
| File cấu hình trong container | `/CLIProxyAPI/config.yaml` |
| Thư mục lưu token OAuth | `/root/.cli-proxy-api` (khớp `auth-dir: "~/.cli-proxy-api"`) |
| Thư mục log | `/CLIProxyAPI/logs` |
| Thư mục plugin | `/CLIProxyAPI/plugins` |

**Dùng image công khai, không build từ mã nguồn** — ta không sửa gì trong CLIProxy, build một
image Go 1.26 chỉ tốn thời gian.

### Cổng OAuth callback — bắt buộc publish

`docker-compose.yml` của CLIProxy publish 6 cổng: `8317, 8085, 1455, 54545, 51121, 11451`.
Không phải cổng thừa: mỗi provider OAuth có một cổng callback riêng mà **trình duyệt của
người dùng** phải gọi tới được.

| Provider | Cổng callback | Hằng số trong mã nguồn |
|----------|---------------|------------------------|
| Antigravity *(ta dùng)* | **51121** | `internal/auth/antigravity/constants.go:8` |
| Anthropic | 54545 | `auth_files_oauth_callback.go:17` |
| Codex | 1455 | `auth_files_oauth_callback.go:18` |

→ **Task 2.1 (Q): compose phải publish cả `8317:8317` và `51121:51121`.**
Thiếu 51121 thì bấm nút OAuth sẽ đi tới Google, đồng ý xong trình duyệt quay về
`http://localhost:51121/oauth-callback` và **báo lỗi không kết nối được** — token không bao giờ
được lưu.

---

## 2. Management key

Khai trong `config.yaml`:

```yaml
remote-management:
  allow-remote: false        # ⚠️ xem mục 3 — ta PHẢI đổi thành true
  secret-key: ""             # để rỗng = tắt hẳn Management API (404 mọi route /v0/management)
  disable-control-panel: false
```

| Điều | Chi tiết |
|------|----------|
| Bỏ trống `secret-key` | Management API **tắt hoàn toàn**, trả 404 — không phải 401 |
| Giá trị plaintext | Được **băm lúc khởi động**, file config bị ghi đè bằng bản hash |
| Cách gửi key | `Authorization: Bearer <key>` **hoặc** header `X-Management-Key: <key>` |
| Biến môi trường thay thế | `MANAGEMENT_PASSWORD` (dùng cho Management Web UI) |

### 🚨 Bẫy: sai key 5 lần → ban IP 30 phút

`handler.go:301-302` — `maxFailures = 5`, `banDuration = 30 * time.Minute`.
Đếm theo **IP client**. Trong Docker, cả container `api` của ta dùng chung một IP →
Q gõ nhầm `CLIPROXY_MGMT_KEY` trong `.env` rồi để code retry vài lần là **tự khoá mình 30 phút**,
lỗi trả về là `403 IP banned due to too many failed attempts`.

→ **Task 2.2 (Q): client CLIProxy KHÔNG được retry khi gặp 401/403.** Chỉ retry lỗi mạng và 5xx.
Muốn gỡ ban sớm: `docker compose restart cliproxy` (bộ đếm nằm trong RAM).

---

## 3. 🚨 `allow-remote: false` sẽ chặn container `api` của ta

Đây là phát hiện quan trọng nhất của buổi khảo sát.

`internal/api/handlers/management/handler.go`:

```go
// dòng 272-273
clientIP := c.ClientIP()
localClient := clientIP == "127.0.0.1" || clientIP == "::1"

// dòng 337-339
if !localClient && !allowRemote {
    return false, http.StatusForbidden, "remote management disabled"
}
```

"Localhost" ở đây là **`127.0.0.1` / `::1` theo đúng nghĩa đen**. Container `api` gọi
`http://cliproxy:8317/...` sẽ đến với IP mạng bridge (`172.x.x.x`) → **403 `remote management disabled`**,
kể cả khi gửi đúng management key.

→ **Task 2.1 (Q): `config.yaml` của CLIProxy bắt buộc `allow-remote: true`.**
Chấp nhận được vì cổng 8317 chỉ publish ra localhost của máy dev (demo, không production).

---

## 4. 🚨 `get-auth-status` KHÔNG dùng để hiện badge "Đã kết nối"

Plan.md mục 2.4 ghi dùng `GET /v0/management/get-auth-status` cho badge trạng thái. Đọc mã
nguồn (`auth_files_provider_oauth.go:757-810`) thì endpoint này là **poller cho một phiên OAuth
đang chạy**, tham số bắt buộc là `state` lấy từ lúc xin auth-url:

| Tình huống | Trả về |
|------------|--------|
| **Không truyền `state`** | `{"status":"ok"}` ← trả "ok" kể cả khi **chưa đăng nhập bao giờ** |
| `state` sai định dạng | `400 {"status":"error","error":"invalid state"}` |
| `state` lạ / hết hạn | `{"status":"error","error":"unknown or expired state"}` |
| Đang chờ người dùng đồng ý | `{"status":"wait"}` |
| Xong | `{"status":"ok"}` |
| Lỗi | `{"status":"error","error":"<mô tả>"}` |

Dùng nó cho badge sẽ **luôn hiện "Đã kết nối"** — lỗi âm thầm, đúng loại bẫy nguy hiểm nhất.

→ **Task 2.4 (Q): badge phải hỏi `GET /v0/management/auth-files`** (liệt kê credential đã lưu).
Có phần tử với provider tương ứng = đã kết nối; mảng rỗng = chưa.
`get-auth-status?state=…` chỉ dùng để **poll 2 giây/lần trong lúc chờ người dùng đồng ý** (task 2.5).

---

## 5. 🚨 `gemini-flash-latest` không tồn tại trong provider `antigravity`

`.env.example` và `app/core/config.py` đang đặt `LLM_MODEL=gemini-flash-latest` cùng
`CLIPROXY_AUTH_PROVIDER=antigravity`. Hai giá trị này **không khớp nhau**.

Danh mục model ở `internal/registry/models/models.json` chia theo channel. Model
`gemini-flash-latest` nằm ở channel `aistudio` và `gemini`, **không** nằm ở `antigravity`.

Model mà channel `antigravity` thực sự cung cấp:

```
gemini-3-flash              gemini-3.1-flash-lite       gemini-3.1-flash-image
gemini-3.6-flash-high       gemini-3.7-flash-high       gemini-3.8-flash-high
gemini-3.1-pro-low          gemini-pro-agent
claude-opus-4-6-thinking    claude-sonnet-4-6           gpt-oss-120b-medium
```

### Vì sao vẫn phải dùng `antigravity`

Các cờ đăng nhập OAuth có sẵn trong binary (`cmd/server/main.go:95-102`) chỉ gồm:
`codex`, `claude`, **`antigravity`**, `kimi`, `xai`. Tương ứng 5 route auth-url
(`server_management.go:176-180`). **Không có** `gemini-auth-url` hay `aistudio-auth-url`.

→ Muốn dùng Gemini **bằng OAuth, không nhúng API key** (nguyên tắc Plan.md mục 9) thì
`antigravity` là đường duy nhất. Channel `aistudio`/`gemini` cần API key khai trong
`gemini-api-key` của `config.yaml` — chỉ để dành làm dự phòng cho rủi ro R1.

→ **Task 2.3 (Q): đổi mặc định `LLM_MODEL` sang một model có thật trong channel `antigravity`.**
Đề xuất `gemini-3-flash` (bản flash cơ bản, gần "Gemini Flash" mà đề bài yêu cầu nhất).
Chốt con số cuối cùng bằng cách gọi `GET /v0/management/model-definitions/antigravity` sau khi
container chạy ở task 2.1 — danh mục có thể tự cập nhật từ xa nên **không hardcode theo tài liệu này**.

---

## 6. Xác nhận lại: vẫn KHÔNG có endpoint embedding

Kiểm lại ở commit `7fac6b15` — kết luận của Plan.md mục 2.6 **vẫn đúng**:

| Kiểm tra | Kết quả |
|----------|---------|
| Route `/v1/embeddings` kiểu OpenAI | Không tồn tại (`internal/api/server_routes.go`) |
| Route Gemini native | `GET/POST /v1beta/models/*action` (dòng 126-127) |
| Action được xử lý | Chỉ `generateContent`, `streamGenerateContent`, `countTokens` |
| Nhánh `default` của `switch` | **Không có** — `sdk/api/handlers/gemini/gemini_handlers.go:156-163` |

Hệ quả của việc thiếu nhánh `default`: gọi `:embedContent` **không trả 404** mà rơi ra khỏi
`switch`, handler kết thúc không ghi gì → gin trả **HTTP 200 với body rỗng**. Rất dễ tưởng
"gọi được nhưng parse lỗi" và mất nửa ngày debug nhầm hướng.

→ Giữ nguyên quyết định: **embedding do service `embedder` cục bộ đảm nhiệm** (task 3.10, của T).

---

## 7. Các endpoint ta thực sự dùng

Tất cả nằm dưới `/v0/management`, đều cần management key (mục 2) và `allow-remote: true` (mục 3).

| Việc | Endpoint | Trả về |
|------|----------|--------|
| Xin URL đăng nhập OAuth | `GET /antigravity-auth-url` | `{"status":"ok","url":"<URL Google>","state":"<state>"}` |
| Poll trong lúc chờ đồng ý | `GET /get-auth-status?state=<state>` | `{"status":"wait"}` → `{"status":"ok"}` |
| **Kiểm tra đã kết nối chưa** | `GET /auth-files` | Danh sách credential đã lưu |
| Model khả dụng của channel | `GET /model-definitions/antigravity` | Danh mục model |
| Huỷ phiên OAuth đang chờ | `DELETE /oauth-session` | |
| Xoá credential (ngắt kết nối) | `DELETE /auth-files` | |
| Gọi model (vision + text) | `POST /v1beta/models/<model>:generateContent` | Chuẩn Gemini native |
| Gọi model (thay thế) | `POST /v1/chat/completions` | Chuẩn OpenAI |

### Tham số `is_webui`

`GET /antigravity-auth-url?is_webui=1` (nhận `1|true|yes|on`, `auth_files_oauth_callback.go:27-38`)
làm CLIProxy dựng thêm một forwarder ở cổng 51121 chuyển tiếp callback về chính nó.
Dù bật hay không, **cổng 51121 vẫn phải publish** vì trình duyệt luôn được Google chuyển hướng
về `http://localhost:51121/oauth-callback`.

---

## 8. Cấu hình `config.yaml` đề xuất cho dự án

Q dùng ở task 2.1. File này **không commit** (`.gitignore` đã có `cliproxy/config.yaml`).

```yaml
host: ""
port: 8317

remote-management:
  allow-remote: true          # BẮT BUỘC — container api không phải 127.0.0.1 (mục 3)
  secret-key: "<đặt trùng CLIPROXY_MGMT_KEY trong .env>"
  disable-control-panel: false

auth-dir: "/root/.cli-proxy-api"   # mount volume cliproxy_auths vào đây (giữ token qua restart, rủi ro R1)

api-keys: []                  # không dùng — ta gọi qua mạng nội bộ Docker
debug: false
```

---

## 9. Việc còn phải làm khi chạy container thật (D2)

Khảo sát này dừng ở mức đọc mã nguồn. Bốn điều **phải xác nhận bằng tay** ở task 2.1/2.5:

1. Gọi `GET /v0/management/model-definitions/antigravity` → chốt tên model thật cho `LLM_MODEL`.
2. Bấm nút OAuth đầu–cuối một lần: URL Google → đồng ý → callback 51121 → token nằm trong volume.
3. Xác nhận `GET /auth-files` trả đúng nhãn tài khoản để hiển thị trên badge.
4. Thử `POST /v1beta/models/<model>:generateContent` kèm **ảnh** (`inline_data`) — vision là
   trái tim của F1, phải biết chắc nó chạy qua channel `antigravity` trước khi Q viết task 3.4.
