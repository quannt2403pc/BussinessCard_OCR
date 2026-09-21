# ADR — Định tuyến OAuth theo từng người dùng qua CLIProxy

> Chủ sở hữu: **Q** · Task **12.1** · Rủi ro **R8** · Tiêu chí **A10**
> Mở: 2026-09-21 · **Chốt: 2026-09-21** · Trạng thái: **✅ ĐÃ CHỐT — phương án (a)**
> Đo trên **2 tài khoản Google thật** (`quanpyke@gmail.com`, `quanpyke24@gmail.com`)
> Script: [`scripts/spike_multiuser_oauth.py`](../scripts/spike_multiuser_oauth.py)

---

## 1. Vấn đề

Từ **D13**, mỗi người dùng tự bấm nút OAuth bằng Gmail của mình, nên CLIProxy sẽ giữ **nhiều
credential cùng lúc**. Câu hỏi chặn cả ngày D13:

> Khi có 2 credential, CLIProxy quyết định dùng cái nào cho một lời gọi `generateContent`?
> Ta có chỉ định được không?

Nếu không chỉ định được thì lời gọi của người A có thể đi bằng tài khoản Google của người B.
Đây là kiểu hỏng **âm thầm**: không có lỗi nào, không có gì trong response nói nó đi bằng tài
khoản nào — người bị mượn quota chỉ thấy hoá đơn hoặc `429` của mình cạn nhanh bất thường.
Tiêu chí **A10** trượt mà không ai biết.

`docs/cliproxy-notes.md` (khảo sát 1.9) **không có một dòng nào** về chuyện này — đúng như R8 ghi.

## 2. Đã đo được gì (2026-09-21, CLIProxyAPI v7.2.156)

Tất cả đều **chỉ đọc**, không đụng vào file token.

| # | Đo cái gì | Kết quả | Nguồn |
|---|-----------|---------|-------|
| Đ1 | Chiến lược định tuyến mặc định | **`round-robin`** | `routing.strategy` trong `config.example.yaml` 878 dòng của upstream (nằm trong image tại `/CLIProxyAPI/config.example.yaml`, khác hẳn bản 1,7KB ta đang dùng) |
| Đ2 | Cấu hình đang chạy của ta | `routing = {}`, `force-model-prefix = false` | `GET /v0/management/config` |
| Đ3 | Có cơ chế chọn credential không | **Có — `prefix`**: *"optional: require calls like `test/gemini-3-pro-preview` to target this credential"* | upstream config, 7 chỗ (dòng 342, 384, 414, 456, 491, 636, 688) |
| Đ4 | `prefix` có gắn được cho credential OAuth không | **CÓ — đã đo trên 2 tài khoản thật, xem mục 5.** (Lúc chỉ có 1 tài khoản thì đây mới là suy luận: binary chứa struct tag `json:"prefix"` **cùng bộ với** `json:"weight"`, mà tài liệu nói thẳng *"For OAuth/file credentials, add a top-level numeric `weight` field to the auth JSON"*. Suy luận đúng, nhưng giữ lại đây để thấy nó **chỉ là suy luận cho tới khi có phép đo**) | `grep -a` trên binary, rồi đo thật |
| Đ5 | Tiền tố lạ bị xử lý ra sao | `400 unknown provider for model <prefix>/<model>` → CLIProxy **có phân giải** `prefix/model`, chỉ là chưa credential nào nhận tiền tố đó | gọi thật qua container `api` |
| Đ6 | Đóng được đường rò không | **Có — `force-model-prefix: true`**: *"unprefixed model requests only use credentials without a prefix"* | upstream config dòng 137 |
| Đ7 | Quy được lời gọi về đúng credential không | **Được**: mỗi bản ghi `auth-files` có bộ đếm `success`/`failed` riêng; gọi 1 lượt → `success` nhảy **12 → 13** đúng ở credential đã phục vụ | `GET /v0/management/auth-files` trước/sau |
| Đ8 | Có route quản trị sửa trường của credential không | **Có**: `PATCH /v0/management/auth-files/fields` với `{"name", "prefix"}` → `200 {"status":"ok"}`. Hợp đồng đầy đủ ở mục 5 — dò bằng thực nghiệm, **không có trong tài liệu nào** | liệt kê route từ binary rồi thử từng method |

### Hai phát hiện phụ, đáng ghi vì dễ mất giờ

- **`400 unknown provider for model …` nay có BA nguyên nhân**, không phải hai như I-03 ghi:
  (1) chưa kết nối OAuth, (2) sai tên model, (3) **tiền tố không credential nào nhận**. Câu chữ
  y hệt nhau ở cả ba.
- **Ban IP khi sai management key (I-05) tính theo IP nguồn.** Đo được hôm nay: host
  (`172.19.0.1`) bị 403 **tức thì, 0 ms** trong khi container `api` (`172.19.0.6`) vẫn 200 bình
  thường. Nên mọi script spike phải chạy **trong container**, và một lần lỡ tay ở máy thật
  *không* làm chết hệ thống đang chạy.

## 3. `session-affinity` KHÔNG giải quyết được việc này

CLIProxy có `routing.session-affinity` nghe rất giống thứ ta cần, nhưng đọc kỹ thì không phải:

- Nó gắn **một phiên** vào một credential, và phiên được nhận dạng bằng *session header của
  Claude Code/Codex/OpenCode, `prompt_cache_key`, conversation ID, hoặc băm tin nhắn đầu tiên* —
  **không phải danh tính người dùng của ứng dụng ta**.
- Credential cho lần gắn đầu do **"credential priority"** quyết, tức **ta không chọn**.
- Tài liệu ghi rõ: *"An established binding outranks credential priority"* — gắn nhầm rồi thì nó
  **giữ luôn cái nhầm đó**.

Nói cách khác `session-affinity` cho **tính dính**, không cho **quyền chọn**. Người A hoàn toàn có
thể bị gắn dính vào credential của người B. Không đạt A10.

## 4. Quyết định

**Chốt phương án (a) của R8: dùng cơ chế `prefix` của CLIProxy.** Mỗi credential OAuth mang một
tiền tố riêng; backend gọi model bằng tên có tiền tố của đúng người đang đăng nhập.

Cách làm ở **13.1 / 13.2**:

1. Người dùng bấm kết nối → sau khi `get-auth-status` trả `ok`, đối chiếu `auth-files` để biết tên
   file credential vừa sinh, ghi vào `users.cliproxy_auth_file` (đã có trong thiết kế 13.1).
2. **Gắn tiền tố cho credential đó**, tiền tố sinh từ id người dùng (ví dụ `u<8 ký tự đầu của
   uuid>`) — đặt qua `/v0/management/auth-files/fields`, **không sửa tay file token**.
3. `services/llm.py` (13.2) gọi model bằng `f"{prefix}/{settings.llm_model}"` theo người dùng
   đang đăng nhập.
4. **Bật `force-model-prefix: true`** trong `cliproxy/config.yaml`. Đây là bước chống rò: thiếu nó
   thì một lời gọi lỡ quên tiền tố sẽ rơi vào round-robin và mượn nhầm tài khoản người khác —
   đúng kịch bản R8. Bật rồi thì lời gọi không tiền tố chỉ dùng credential **không có** tiền tố,
   tức là không dùng của ai cả.
5. **Đặt `routing.strategy` rõ ràng trong config** thay vì để trống. Để trống là ăn mặc định
   `round-robin` — thứ ta vừa xác định là nguy hiểm trong bối cảnh nhiều người dùng.

### Vì sao không chọn (b) hay (c)

- **(b) mỗi người một container CLIProxy**: đắt (mỗi container ~16MB RAM lúc nhàn rỗi nhưng cần
  một cổng OAuth callback riêng), và phải sinh container động trên VPS 4GB — thêm một lớp hạ tầng
  mới ngay ngày cuối. Chỉ quay lại phương án này nếu phép đo ở mục 5 trượt.
- **(c) hạ yêu cầu xuống một kết nối dùng chung**: chưa cần tới. Nếu phải dùng thì **báo chủ dự án
  ngay trong ngày D12**, không để tới D13.

## 5. Phép đo quyết định (2026-09-21, 2 tài khoản thật)

Chạy `docker compose exec api python -m scripts.spike_multiuser_oauth --calls 6 --prefix
quanpyke@gmail.com=ua --prefix quanpyke24@gmail.com=ub`.

**Chiều phủ định — không tiền tố thì xoay vòng.** Hai lượt đo, 8 lượt rồi 6 lượt, **luân phiên
hoàn hảo**:

| Lượt | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|------|---|---|---|---|---|---|---|---|
| Đo lần 1 (8 lượt) | B | A | B | A | B | A | B | A |
| Đo lần 2 (6 lượt) | A | B | A | B | A | B | — | — |

Tỉ lệ 4/4 rồi 3/3. **R8 không còn là suy đoán từ tài liệu mà là sự thật đo được**: để nguyên như
hiện nay, lời gọi của người A đi bằng tài khoản Google của người B **một nửa số lần**.

**Chiều khẳng định — có tiền tố thì trúng đích 100%.**

| Gọi | Kết quả |
|-----|---------|
| `ua/gemini-3-flash` ×3 | **luôn** `quanpyke@gmail.com` |
| `ub/gemini-3-flash` ×3 | **luôn** `quanpyke24@gmail.com` |
| `khong-credential-nao-nhan/gemini-3-flash` | `400 unknown provider for model …` |

**Cách gắn tiền tố — hợp đồng đã dò ra bằng thực nghiệm** (không có trong tài liệu nào):

```
PATCH /v0/management/auth-files/fields
{"name": "antigravity-<email>.json", "prefix": "ua"}      → 200 {"status":"ok"}
```

- Method **PATCH**; `POST`/`PUT` trả 404. Thiếu `name` → `400 name is required`; chỉ có `name` →
  `400 no fields to update`.
- Gỡ tiền tố: gửi lại `"prefix": ""`. Đã kiểm: gỡ xong `ua/…` trả về 400 ngay, gọi thường vẫn 200.
- **Không phải mở file token**, nên không vướng quyền đọc credential.

⚠️ **`GET /v0/management/auth-files` KHÔNG trả trường `prefix`** (bản ghi có 26 khoá, không khoá
nào là nó). Gắn xong đọc lại vẫn thấy `null` — **đừng tưởng là thất bại**. Chỉ kiểm được bằng
hành vi: gọi thử rồi xem bộ đếm `success` của credential nào tăng. Script làm đúng như vậy, và vì
thế tiền tố phải truyền vào bằng `--prefix EMAIL=TIEN_TO` chứ không tự đọc được.

⚠️ **Gắn tiền tố KHÔNG tự bịt đường rò.** Đo ngay sau khi gắn cả hai: lời gọi *không* tiền tố vẫn
chạy 200 và vẫn **xoay vòng** như cũ — vì `force-model-prefix` đang là `false`. Tin tốt cho hôm
nay (gắn tiền tố lên hệ thống đang chạy **không làm hỏng gì**: trợ lý AI vẫn trả lời đúng kèm
trích dẫn ngay sau đó), nhưng nghĩa là **bịt rò là một bước riêng ở 13.2**, không tự đến.

## 6. Hệ quả

- **13.1** thêm một bước: gắn tiền tố cho credential, và **gỡ tiền tố khi ngắt kết nối**.
- **13.2** đổi chỗ dựng tên model: lấy theo người dùng thay vì đọc thẳng `settings.llm_model`.
- **I-28 nặng thêm một lý do phải sửa**: `disconnect()` hiện xoá **mọi** credential. Với tiền tố
  thì xoá nhầm còn kéo theo mất luôn tiền tố của người khác.
- `cliproxy/config.yaml` phải khai `force-model-prefix: true` và `routing.strategy` — nhớ **sửa cả
  `config.example.yaml`**, nếu không máy sạch dựng lên lại chạy mặc định round-robin.
