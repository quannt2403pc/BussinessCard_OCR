# Kết nối OAuth với CLIProxy

> Chủ sở hữu: **Q** · Task **2.5** · Cập nhật: **2026-09-11**
> Đối chiếu khảo sát mã nguồn của T: [`cliproxy-notes.md`](./cliproxy-notes.md) · Thiết kế: `Plan.md` mục 2.4, 2.5

Toàn bộ lời gọi Gemini của dự án đi qua **CLIProxyAPI** bằng **OAuth**, không nhúng API key
(nguyên tắc `Plan.md` mục 9). Tài liệu này mô tả cách bấm nút kết nối, cách kiểm tra, và cách
xử lý khi hỏng.

---

## 1. Chuẩn bị (một lần)

```bash
cp .env.example .env                                   # điền CLIPROXY_MGMT_KEY
cp cliproxy/config.example.yaml cliproxy/config.yaml    # secret-key phải TRÙNG key trên
docker compose up -d
docker compose exec api alembic upgrade head
```

Hai giá trị **bắt buộc trùng nhau**:

| Nơi | Khoá |
|-----|------|
| `.env` | `CLIPROXY_MGMT_KEY=<key>` |
| `cliproxy/config.yaml` | `remote-management.secret-key: "<key>"` |

> CLIProxy **băm `secret-key` lúc khởi động rồi ghi đè lại chính `config.yaml`**. Sau lần `up`
> đầu tiên, giá trị trong file thành chuỗi `$2a$10$…` — đó là bình thường, không phải hỏng.
> Muốn đổi key: điền lại plaintext rồi `docker compose restart cliproxy`.

Bỏ bước `cp` thứ hai thì Docker thấy đường dẫn bind mount không tồn tại và **tạo một thư mục**
tên `config.yaml`, cliproxy khởi động lỗi với thông báo rất khó đoán.

---

## 2. Bấm nút kết nối (thao tác tay, đầu–cuối)

1. Mở <http://localhost:8000/settings>. Badge lúc này là **“Chưa kết nối”** (chấm đỏ).
2. Bấm **“Kết nối CLIProxy (OAuth)”**. Backend gọi
   `GET /v0/management/antigravity-auth-url?is_webui=1`, nhận `url` + `state`, mở tab mới tới
   màn hình đăng nhập Google.
   *Trình duyệt chặn popup thì trang hiện nguyên link để mở tay.*
3. Đăng nhập tài khoản Google rồi bấm **Đồng ý**. Google chuyển hướng về
   `http://localhost:51121/oauth-callback` — cổng này do container `cliproxy` publish, thiếu nó
   thì đến bước này trình duyệt sẽ báo không kết nối được (I-04).
4. Trong lúc chờ, trang `/settings` poll `GET /api/integration/oauth-status?state=…` mỗi **2
   giây**. Trả `wait` thì chờ tiếp, trả `ok` thì trang **gọi lại `/api/integration/status`** để
   vẽ badge.
5. Badge chuyển **“Đã kết nối”** kèm email tài khoản. Token nằm trong volume `cliproxy_auths`
   nên sống qua `docker compose restart`.
6. Bấm **“Kiểm tra kết nối”** — gọi thật một prompt ngắn tới `LLM_MODEL`. Thành công thì trang
   hiện nguyên văn câu trả lời của model kèm thời gian gọi.

Chờ quá **5 phút** thì UI tự huỷ phiên (`DELETE /api/integration/oauth-session?state=…`) và mời
bấm lại — `state` treo vô hạn bên CLIProxy không có lợi gì.

### Ngắt kết nối

Nút **“Ngắt kết nối”** phải bấm **hai lần** (lần đầu là xác nhận). Nó xoá từng credential của
provider trong CLIProxy rồi hạ cờ trong bảng `integration_status`.

---

## 3. Vì sao badge đọc `auth-files` chứ không phải `get-auth-status`

Đây là bẫy nguy hiểm nhất của cả luồng này (I-02) — **đã đo lại trên container thật**:

```
$ curl -H "Authorization: Bearer $KEY" localhost:8317/v0/management/get-auth-status
{"status":"ok"}          ← chưa đăng nhập bao giờ vẫn trả "ok"

$ curl -H "Authorization: Bearer $KEY" localhost:8317/v0/management/auth-files
{"files":[]}             ← đây mới là sự thật
```

Dùng `get-auth-status` cho badge thì badge **xanh vĩnh viễn**, kể cả khi chưa từng đăng nhập.
Vì thế trong mã nguồn:

- `CliProxyClient.oauth_status()` **bắt buộc** tham số `state`, thiếu là ném `ValueError`.
- `GET /api/integration/oauth-status` khai `state` là query bắt buộc → thiếu thì FastAPI trả
  **422**, không có đường nào lọt ra kết quả “ok” giả.
- Badge chỉ lấy dữ liệu từ `GET /api/integration/status`, mà endpoint này đọc `auth-files`.

---

## 4. Hợp đồng endpoint (đo trên `eceasy/cli-proxy-api` v7.2.156, 2026-09-11)

Tất cả nằm dưới `/v0/management`, cần header `Authorization: Bearer <mgmt key>`
(hoặc `X-Management-Key`), và `allow-remote: true` (I-01).

| Gọi | Kết quả thật |
|-----|--------------|
| `GET /auth-files` chưa đăng nhập | `200 {"files":[]}` |
| `GET /auth-files` có credential | `200 {"files":[{"name":"antigravity-<email>.json","provider":"antigravity","label":"<email>","status":"active","disabled":false,…}]}` |
| `GET /antigravity-auth-url?is_webui=1` | `200 {"status":"ok","url":"https://accounts.google.com/…","state":"<32 hex>"}` |
| `GET /get-auth-status?state=<đang chờ>` | `200 {"status":"wait"}` |
| `GET /get-auth-status?state=<lạ>` | `200` + `{"status":"error","error":"unknown or expired state"}` ← **lỗi mà vẫn HTTP 200** |
| `DELETE /oauth-session?state=…` | `200 {"status":"ok","cancelled":true}` |
| `DELETE /auth-files` thiếu `name` | `400 {"error":"invalid name"}` |
| `DELETE /auth-files?name=<tên file>` | `200 {"status":"ok"}` |
| Sai management key | `401 {"error":"invalid management key"}` |
| Thiếu management key | `401 {"error":"missing management key"}` |

Hai hệ quả đã đưa thẳng vào mã:

1. **Không có API “xoá tất cả credential”.** Ngắt kết nối = `auth-files` rồi xoá từng `name`.
2. **Đọc `status` trong body, đừng tin mã HTTP** với `get-auth-status`.

---

## 5. Khi gọi model bị lỗi: bốn tình huống, ba thông báo

Đo thật ở task 2.3 — **hai tình huống đầu trả về câu chữ y hệt nhau nhưng cách sửa trái ngược**:

| Tình huống | CLIProxy trả | UI hiện |
|------------|--------------|---------|
| `LLM_MODEL` sai tên | `400 unknown provider for model …` | “Model … không có trong channel …, sửa `.env`” |
| Model đúng, **chưa từng đăng nhập** | `400 unknown provider for model …` | “Chưa kết nối OAuth, bấm nút kết nối” |
| Model đúng, credential vừa bị xoá | `503 auth_unavailable: no auth available` | “Chưa kết nối OAuth, bấm nút kết nối” |
| Token hỏng / hết hạn | `401 authentication_error` | “Token hỏng hoặc hết hạn, đăng nhập lại” |

`services/llm.py` phân biệt hai dòng đầu bằng cách tra danh mục model của channel
(`GET /v0/management/model-definitions/antigravity`): model **có** trong danh mục ⇒ lỗi là chưa
kết nối; **không có** ⇒ đúng là sai `LLM_MODEL` (I-03). Bỏ bước tra này thì người dùng bị đẩy đi
sửa `.env` trong khi thật ra chỉ cần bấm một nút.

---

## 6. Sự cố thường gặp

| Triệu chứng | Nguyên nhân | Cách xử lý |
|-------------|-------------|------------|
| Badge “Không gọi được CLIProxy”, detail có `Name or service not known` | Container `cliproxy` chưa chạy | `docker compose up -d cliproxy` |
| `403 remote management disabled` | `allow-remote: false` — CLIProxy coi “localhost” đúng nghĩa đen, container `api` gọi từ IP bridge nên bị coi là remote (I-01) | Đặt `allow-remote: true` trong `cliproxy/config.yaml`, restart |
| `401 invalid management key` | `.env` và `config.yaml` lệch key | Sửa cho trùng, `docker compose restart cliproxy` |
| `403 IP banned due to too many failed attempts` | Sai key 5 lần → ban IP 30 phút, cả container `api` chung một IP (I-05) | `docker compose restart cliproxy` (bộ đếm nằm trong RAM) |
| Mọi route `/v0/management` trả **404** | `secret-key` để rỗng = tắt hẳn Management API | Điền `secret-key`, restart |
| Đồng ý trên Google xong, trình duyệt báo không kết nối được | Cổng `51121` chưa publish (I-04) | Kiểm tra khối `cliproxy.ports` trong `docker-compose.yml` |
| Badge xanh nhưng “Kiểm tra kết nối” đỏ | Token còn nhưng `LLM_MODEL` sai tên, hoặc tài khoản hết quota | Đối chiếu danh sách model ngay trên trang `/settings` |
| Badge “Không gọi được CLIProxy” mà vẫn hiện email | Đúng như thiết kế: đó là **cache** lần kiểm tra cuối. CLIProxy chết không làm mất token (token nằm trong volume) | Bật lại cliproxy rồi bấm “Làm mới” |

Xem log gọn: `docker compose logs -f cliproxy`.

---

## 7. Phần đã kiểm chứng bằng máy & phần còn phải thử tay

Kiểm chứng tự động ở task 2.2–2.4 (container thật, không mock):

- `/health` xanh, router `integration` được nạp, `/settings` trả HTTP 200.
- `status` → `connected:false` khi `auth-files` rỗng; **`connected:true` + đúng email** khi thư
  mục `auth-dir` có credential (thử bằng file credential giả nhét vào volume).
- `connect` → trả URL Google + `state`; `oauth-status?state=…` → `wait`; huỷ phiên → lần poll sau
  ra `unknown or expired state`.
- Thiếu `state` khi poll → **422**, không thể lọt ra “ok” giả (I-02).
- Sai management key → đúng **một** request tới CLIProxy, **không retry** (I-05) — đếm bằng log
  của container.
- Tắt `cliproxy`: `/health` và `/settings` vẫn 200, `status` trả `reachable:false` + cache,
  `connect` trả 503 có thông báo rõ.

**Còn phải làm tay một lần (cần tài khoản Google thật, máy không tự làm được):**

1. Bấm nút OAuth đi hết vòng: Google → Đồng ý → callback 51121 → badge chuyển xanh.
2. Bấm “Kiểm tra kết nối” và xem câu trả lời thật của `gemini-3-flash`.
3. Thử `generate_vision()` với ảnh thật (`inline_data`) — **phải xong trước khi viết task 3.4**,
   đây là việc còn lại của I-03.
