# Triển khai `ocrximi.io.vn`

> Chủ sở hữu: **Q** · Task **13.4**, **13.5**, **13.7**, **13.8**, **13.9** · Cập nhật **2026-09-22**
> Chọn máy và số tiền: `docs/vps-options.md` · Kiến trúc: `Plan.md` mục 10

Một máy ảo Google Compute Engine chạy đúng bộ Docker Compose của dự án, Caddy giữ HTTPS,
GitHub Actions tự deploy khi `main` xanh CI.

```
Internet ──443──► Caddy ──► api ──┬── db (pgvector)
                                  ├── embedder
                                  └── cliproxy ──► Gemini
```

> ## ⏰ Ngày hết hạn Free Trial: **2026-12-21**
>
> Tài khoản mở ngày 2026-09-22, trial 90 ngày. Hết hạn là Google **dừng tài nguyên** —
> `ocrximi.io.vn` tắt mà **không ai báo**, không có mail cảnh báo nào từ phía hệ thống này.
> Đây là mốc phải nhớ, **không phải số dư credit**: ở mức ~$39,3/tháng thì 90 ngày mới tiêu
> hết ≈$118 trong $300, tức **tiền vẫn còn hơn nửa lúc máy bị dừng**.
> Trước ngày đó phải quyết: nâng cấp lên tài khoản trả tiền, hay dọn sang phương án dự phòng
> (Oracle Always Free — `docs/vps-options.md` mục 5). ⚠️ Nhớ **xoá hoặc gắn lại static IP**
> khi xoá máy: IP đã reserve mà không gắn vào đâu thì vẫn bị tính tiền.

---

## 0. Việc phải làm bằng tay, không tự động hoá được

Đánh dấu 👤 ở mọi bước cần người thật ngồi bấm. Phần còn lại script lo.

| # | Việc | Ở đâu |
|---|------|-------|
| 👤 1 | Đăng ký Google Cloud Free Trial, kiểm **quota** vCPU/IP | console.cloud.google.com |
| 👤 2 | Tạo VM + reserve static IP + VPC firewall rule | console.cloud.google.com |
| 👤 3 | Trỏ bản ghi DNS `A` về static IP | trang quản trị tên miền |
| 👤 4 | Sinh khoá SSH deploy, dán vào GitHub Secrets | máy của bạn + Settings → Secrets |
| 👤 5 | Tạo GitHub Environment `production` + Required reviewers | Settings → Environments |
| 👤 6 | Kết nối OAuth Google lần đầu trên `/settings` | trình duyệt |
| 👤 7 | Bật branch protection (task 13.10 — cần GitHub Pro) | Settings → Branches |

---

## 1. 👤 Tạo máy ảo

> **Máy đang chạy (dựng 2026-09-22):** project `ocrximi` · instance `bizcard` ·
> `asia-southeast1-a` · `e2-medium` · Debian 12 · pd-balanced 50 GB ·
> **static IP `34.124.140.167`** (đã reserve, đã gắn) · `enable-oslogin=FALSE`.
> Firewall: `tcp:22`, `tcp:80`, `tcp:443`, `icmp` từ `0.0.0.0/0`; `default-allow-rdp` đã xoá.
> DNS do **TenTen** giữ (`ns-b1/b2/b3.tenten.vn`).

Dựng bằng `gcloud` trong Cloud Shell thì nhanh và không lệ thuộc giao diện đổi chỗ:

```bash
gcloud config set compute/region asia-southeast1
gcloud config set compute/zone   asia-southeast1-a

# Reserve static IP TRƯỚC, rồi mới tạo máy — tạo trước thì máy nhận IP ephemeral.
gcloud compute addresses create bizcard-ip --region=asia-southeast1
gcloud compute addresses describe bizcard-ip --region=asia-southeast1     --format='value(address)'

gcloud compute instances create bizcard   --zone=asia-southeast1-a --machine-type=e2-medium   --image-family=debian-12 --image-project=debian-cloud   --boot-disk-size=50GB --boot-disk-type=pd-balanced   --address=<IP vừa reserve>   --tags=http-server,https-server   --metadata=enable-oslogin=FALSE
```

⚠️ **`--tags` chỉ gắn nhãn, KHÔNG mở cổng.** Dự án mới không có sẵn rule
`default-allow-http/https` (đo thật 2026-09-22: mạng `default` chỉ có `icmp`, `internal`,
`rdp`, `ssh`). Phải tạo rule khớp nhãn, nếu không Caddy **không xin nổi chứng chỉ** vì thử
thách ACME HTTP-01 không vào được cổng 80:

```bash
gcloud compute firewall-rules create default-allow-http   --allow=tcp:80 --source-ranges=0.0.0.0/0 --target-tags=http-server
gcloud compute firewall-rules create default-allow-https   --allow=tcp:443 --source-ranges=0.0.0.0/0 --target-tags=https-server
gcloud compute firewall-rules delete default-allow-rdp --quiet
```

*(Giữ `tcp:22` mở ra Internet là **có chủ ý**: CD ở mục 7 gọi SSH từ runner của GitHub,
mà IP runner đổi liên tục nên không siết theo dải được. Bù lại: chỉ đăng nhập bằng khoá,
tắt hẳn mật khẩu, cộng `ufw`. Tiêu chí **A11** cũng viết đúng vậy — "mở đúng 80/443, giữ 22".)*

Hoặc làm tay trên Console — *Compute Engine → VM instances → Create instance*:

| Trường | Giá trị | Ghi chú |
|--------|---------|---------|
| Region / Zone | `asia-southeast1` / `-a` | |
| Machine type | `e2-medium` (2 vCPU, 4 GB) | |
| Boot disk → Type | **Balanced persistent disk** | |
| Boot disk → Size | **50 GB** | ⚠️ **mặc định là 10 GB** — sửa ngay tại đây. Quên là build `embedder` chết giữa chừng vì hết đĩa |
| Boot disk → Image | Debian 12 hoặc Ubuntu 22.04 LTS | **không** dùng Container-Optimized OS |
| Firewall | ✅ Allow HTTP · ✅ Allow HTTPS | tạo sẵn rule `80/443` |

**Reserve static IP — làm ngay, trước khi trỏ DNS:**

*VPC network → IP addresses → Reserve external static address* (region `asia-southeast1`,
type Regional) → rồi *Edit VM → Network interfaces → External IPv4 address* → chọn IP vừa
reserve.

> ⚠️ IP mặc định là **ephemeral**: dừng/khởi động lại máy là đổi IP, và bản ghi A trỏ vào
> hư không **mà không có lỗi nào báo**. Đây là kiểu hỏng chỉ lộ ra vài tuần sau.

**Rà VPC firewall rule** (*VPC network → Firewall*). Mạng `default` có sẵn vài rule rộng tay.
Kết quả mong muốn: chỉ `22`, `80`, `443` mở từ `0.0.0.0/0`. Xoá hoặc thu hẹp
`default-allow-rdp`, `default-allow-icmp` nếu không dùng.

## 2. Cài đặt trên máy

SSH vào máy (nút **SSH** trên console là đủ cho lần đầu), rồi:

```bash
# --- swap: BẮT BUỘC trước lần build đầu ---------------------------------
# `pip install torch` trong image embedder là chỗ ngốn RAM nhất. 4GB không swap
# thì OOM killer cắt ngang giữa lúc build, và thông báo lỗi không nói gì về RAM.
sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h

# --- Docker Engine + compose plugin -------------------------------------
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
sudo docker compose version     # phải >= v2.24 — xem mục "Bẫy" bên dưới
# KHÔNG dùng `newgrp docker` ở đây: nó mở một shell con và nuốt luôn các dòng dán
# tiếp theo. Quyền chạy docker không cần sudo có ở lần đăng nhập sau, mà cũng không
# cần ngay — `deploy` mới là user chạy compose.

# --- người dùng triển khai riêng ----------------------------------------
sudo adduser --disabled-password --gecos "" deploy
sudo usermod -aG docker deploy

# --- ufw: lớp chặn thứ hai (R9) -----------------------------------------
# Debian 12 KHÔNG cài sẵn ufw (đo thật 2026-09-22 trên image debian-12).
sudo apt-get update && sudo apt-get install -y ufw
sudo ufw default deny incoming && sudo ufw default allow outgoing
sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
sudo ufw --force enable && sudo ufw status verbose

# --- tắt đăng nhập bằng mật khẩu ----------------------------------------
sudo sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl restart ssh
```

> ⚠️ **`docker compose version` phải ≥ v2.24.** `docker-compose.prod.yml` dùng hai thẻ YAML
> `!reset` và `!override` để **gỡ** các cổng mà `docker-compose.yml` đã publish. Bản Compose
> cũ hơn **bỏ qua hai thẻ đó mà không báo lỗi** → deploy vẫn xanh, và PostgreSQL cùng CLIProxy
> phơi thẳng ra Internet. `scripts/deploy.sh` kiểm tra điều này và từ chối chạy nếu quá cũ.

## 3. Lấy mã nguồn và dựng `.env`

```bash
sudo mkdir -p /opt/bizcard && sudo chown deploy:deploy /opt/bizcard
sudo -iu deploy
git clone <URL repo> /opt/bizcard && cd /opt/bizcard

cp .env.prod.example .env && chmod 600 .env

# Sinh ba bí mật, mỗi cái một lần:
for k in POSTGRES_PASSWORD SECRET_KEY CLIPROXY_MGMT_KEY; do
    echo "$k=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
done
```

Mở `.env`, dán ba giá trị vừa sinh, sửa `SITE_DOMAIN` và `ACME_EMAIL`, và **sửa
`DATABASE_URL` cho khớp `POSTGRES_PASSWORD`**.

> ⚠️ **Bước dễ quên nhất — `cliproxy/config.yaml`.** Service `cliproxy-init` chép
> `config.example.yaml` sang `config.yaml` ở lần chạy đầu, và bản mẫu ghi
> `secret-key: "change-me"`. Nó **không biết gì về `.env`**. Không sửa thì mọi lời gọi quản trị
> trả `401`, và sai key 5 lần là CLIProxy **ban IP 30 phút** (I-05) — mà cả container `api`
> chung một IP.

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d cliproxy-init
sed -i "s|^  secret-key:.*|  secret-key: \"$(grep '^CLIPROXY_MGMT_KEY=' .env | cut -d= -f2-)\"|" \
    cliproxy/config.yaml
grep -n 'secret-key' cliproxy/config.yaml     # xác nhận không còn "change-me"
```

*(CLIProxy băm lại `secret-key` rồi ghi đè chính file này ở lần khởi động đầu — thấy chuỗi
hash chứ không phải giá trị vừa dán là **đúng**, không phải hỏng.)*

## 4. 👤 Trỏ DNS

Ở trang quản trị tên miền, tạo hai bản ghi trỏ về **static IP** (không phải IP ephemeral):

| Loại | Tên | Giá trị | TTL |
|------|-----|---------|-----|
| A | `@` | `<static IP>` | 300 (thấp lúc đầu, nâng lên sau khi ổn) |
| A | `www` | `<static IP>` | 300 |

Xác nhận đã lan ra, **từ một máy khác**, trước khi bật Caddy:

```bash
dig +short ocrximi.io.vn      # phải ra đúng static IP
dig +short www.ocrximi.io.vn
```

> Bật Caddy khi DNS chưa đúng thì Let's Encrypt từ chối cấp chứng chỉ, và **giới hạn 5 lần
> thất bại/giờ cho mỗi tên miền** — thử vài lần là phải ngồi chờ.

## 5. Lần deploy đầu tiên

```bash
cd /opt/bizcard && ./scripts/deploy.sh
```

Script tự làm: kiểm tiền đề (Compose ≥ 2.24, `.env` không còn giá trị mẫu) → `pg_dump` sao lưu
→ lấy mã nguồn → `up -d --build` (entrypoint tự `alembic upgrade head`) → smoke test
`https://ocrximi.io.vn/health` → **hỏng thì tự quay về bản trước**.

Lần đầu mất vài phút vì phải build `embedder` (kéo torch). Các lần sau chỉ build lại `api`.

Xong thì kiểm bằng mắt:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
curl -sS https://ocrximi.io.vn/health
curl -sSI http://ocrximi.io.vn/ | head -3      # phải là 308 -> https
```

---

## 6. Kết nối OAuth trên tên miền thật (task 13.7 — gỡ I-29)

**Kết quả điều tra, đo ngày 2026-09-22 trên container thật:**

```
GET /v0/management/antigravity-auth-url?is_webui=1
→ redirect_uri = http://localhost:51121/oauth-callback
```

Giá trị này **ghi cứng trong mã nguồn CLIProxy** (`auth_files_provider_oauth.go:359`, dựng từ
hằng số `antigravity.CallbackPort = 51121`) — **không có khoá cấu hình nào đổi được**. Cờ
`-oauth-callback-port` chỉ tác động tới lệnh login trên dòng lệnh, không tới Management API.

Hệ quả:

- **Trên localhost** luồng thông một cách **tình cờ**: máy chạy trình duyệt cũng là máy chạy
  CLIProxy, nên `localhost:51121` đúng là forwarder mà CLIProxy vừa dựng.
- **Trên `ocrximi.io.vn`** thì `localhost` là máy của **người dùng**. Trình duyệt đâm vào
  khoảng không, trang báo "không kết nối được", và `get-auth-status` **`wait` mãi cho tới lúc
  hết giờ**.

**Phương án dự phòng ghi trong kế hoạch — mở `51121` qua Caddy dưới `oauth.ocrximi.io.vn` rồi
chỉnh URL callback — KHÔNG dùng được.** `redirect_uri` phải khớp đúng cái đã đăng ký cho
client OAuth của Antigravity; client đó là của Google, ta không sửa được danh sách của nó.
Vì vậy `docker-compose.prod.yml` **không publish `51121`**: mở ra cũng vô ích.

**Lối đi được** là chính cái CLIProxy tự chừa cho TUI của nó
(`internal/tui/oauth_tab.go:371`): `POST /v0/management/oauth-callback` nhận trường
`redirect_url` và **tự bóc `code` + `state`** ra khỏi đó (`oauth_callback.go:55-74`). Phần đổi
mã lấy token vẫn chạy trên máy chủ. Trang `/settings` nay có sẵn ô dán URL:

> 👤 **Các bước người dùng thấy trên `ocrximi.io.vn`:**
> 1. Vào **Cài đặt** → bấm **Kết nối CLIProxy (OAuth)** → tab Google mở ra.
> 2. Đồng ý → trình duyệt nhảy sang một trang **báo lỗi không kết nối được localhost**.
>    **Đây là chuyện bình thường, đừng đóng tab.**
> 3. Chép **nguyên** địa chỉ trên thanh địa chỉ của trang đó
>    (`http://localhost:51121/oauth-callback?state=…&code=…`).
> 4. Quay lại tab Cài đặt, dán vào ô *"Sau khi đồng ý, trang báo không kết nối được?"* → bấm
>    **Hoàn tất kết nối**. Badge chuyển xanh.

Phiên OAuth chỉ sống **5 phút** — dán muộn hơn thì bấm kết nối lại từ đầu.

Trên máy dev không cần bước 3–4: callback vẫn tự về như cũ. Ô dán chỉ là lối thoát, không thay
thế gì.

---

## 7. 👤 Bật CD

**a. Khoá SSH** — sinh trên máy của bạn, khoá riêng chỉ dùng cho GitHub Actions:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/bizcard_deploy -C "github-actions-cd" -N ""
ssh-copy-id -i ~/.ssh/bizcard_deploy.pub deploy@<static IP>
ssh -i ~/.ssh/bizcard_deploy deploy@<static IP> 'cd /opt/bizcard && git log -1 --oneline'
ssh-keyscan -H <static IP>          # dán kết quả vào DEPLOY_KNOWN_HOSTS
```

> ⚠️ **Kiểm xem VM có bật OS Login không** (*Compute Engine → Metadata*, khoá `enable-oslogin`).
> Bật thì khoá SSH thường trong metadata **không dùng được** và CD chết với
> `Permission denied (publickey)` — kiểu hỏng chỉ lòi ra lúc CD chạy thật lần đầu. Tắt OS Login
> cho riêng VM này (*Edit VM → Metadata → `enable-oslogin: FALSE`*), hoặc đi đường service
> account. Cách trên dùng `~/.ssh/authorized_keys` của user `deploy` nên không vướng.

**b. Secrets & variables** — *Settings → Secrets and variables → Actions*:

| Loại | Tên | Giá trị |
|------|-----|---------|
| Secret | `DEPLOY_SSH_KEY` | nội dung `~/.ssh/bizcard_deploy` (khoá **riêng**, cả dòng `-----BEGIN`/`-----END`) |
| Secret | `DEPLOY_HOST` | static IP |
| Secret | `DEPLOY_USER` | `deploy` |
| Secret | `DEPLOY_KNOWN_HOSTS` | kết quả `ssh-keyscan -H <static IP>` |
| Variable | `SITE_DOMAIN` | `ocrximi.io.vn` |

**c. Environment `production`** — *Settings → Environments → New environment* → tên
`production` → bật **Required reviewers** và chọn chính mình.

Bước duyệt tay này **không phải thủ tục**: `main` hiện chưa có branch protection (**I-14**, chờ
GitHub Pro ở task 13.10), nên một commit đẩy thẳng vào `main` mà CI tình cờ xanh sẽ lên máy chủ
thật. Cho tới khi 13.10 xong, đây là lớp chặn duy nhất.

**d. Kiểm đủ vòng:** merge một PR nhỏ vào `main` → CI xanh → workflow **CD** chờ duyệt → bấm
duyệt → xem log SSH → smoke test từ ngoài xanh.

Deploy lại một bản cũ mà không cần commit: *Actions → CD → Run workflow*, điền commit vào ô
`ref`.

---

## 8. Vận hành

```bash
cd /opt/bizcard
P="-f docker-compose.yml -f docker-compose.prod.yml"

docker compose $P ps                       # trạng thái
docker compose $P logs -f --tail=100 api   # log ứng dụng
docker compose $P logs --tail=50 caddy     # log chứng chỉ / reverse proxy
docker stats --no-stream                   # RAM còn bao nhiêu (máy chỉ có 4GB)

./scripts/deploy.sh                        # deploy tay khi Actions hỏng
./scripts/deploy.sh --rollback             # quay về bản trước
```

**Sao lưu.** `scripts/deploy.sh` tự `pg_dump` trước mỗi lần deploy vào `./backups/`, giữ 7 bản
gần nhất. Chạy tay ngoài lịch deploy:

```bash
docker compose $P exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    | gzip > "backups/thu-cong-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
```

Tải về máy mình: `scp deploy@<ip>:/opt/bizcard/backups/*.sql.gz .`
Ảnh danh thiếp nằm ở volume `uploads`, **không** nằm trong bản dump — sao lưu riêng nếu cần:
`docker run --rm -v businesscard-ocr_uploads:/d -v "$PWD":/out alpine tar czf /out/uploads.tgz -C /d .`

**Khôi phục.**

```bash
docker compose $P stop api
gunzip -c backups/<ban-can>.sql.gz | docker compose $P exec -T db \
    psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
docker compose $P start api
```

> ⚠️ `--rollback` chỉ lui **mã nguồn**, không lui **schema**. Migration đã chạy thì vẫn còn đó.
> Alembic có `downgrade` nhưng dự án này chưa bao giờ chạy thử nó, nên tự động hoá một bước
> chưa ai kiểm là cách nhanh nhất để biến một bản deploy hỏng thành mất dữ liệu. Cần lui schema
> thì khôi phục từ dump ở trên, bằng tay.

**Chứng chỉ.** Caddy tự gia hạn ~30 ngày trước hạn, không phải làm gì. Nghi ngờ thì:

```bash
echo | openssl s_client -connect ocrximi.io.vn:443 -servername ocrximi.io.vn 2>/dev/null \
    | openssl x509 -noout -dates
docker compose $P logs caddy | grep -i "certificate\|acme\|error"
```

Hỏng thật thì gần như luôn là một trong ba: DNS không còn trỏ đúng IP; cổng 80 bị chặn (ACME
HTTP-01 cần nó); hoặc **volume `caddy_data` bị xoá** — mất volume đó là phải xin lại chứng chỉ,
mà Let's Encrypt giới hạn **5 lần/tuần** cho mỗi bộ tên miền.

---

## 9. Nghiệm thu (task 13.9)

| | Kiểm gì | Cách đo | Kết quả |
|---|---------|---------|---------|
| **A11** | Mở được từ ngoài, chứng chỉ hợp lệ | trình duyệt ở mạng khác | ⬜ |
| **A11** | HTTP tự chuyển HTTPS | `curl -sSI http://ocrximi.io.vn/` → `308` | ⬜ |
| **A11** | **Không cổng nào khác mở** | `nmap -Pn -p- <static IP>` **và** `nmap -Pn -p- ocrximi.io.vn` từ máy ngoài | ⬜ |
| **A11** | Lớp chặn thứ hai khớp | đối chiếu VPC firewall rule trên console với `sudo ufw status` | ⬜ |
| **A10** | Hai người tự kết nối OAuth | 2 trình duyệt, 2 tài khoản Google, theo mục 6 | ⬜ |
| **A10** | A ngắt kết nối **không** ảnh hưởng B | A bấm *Ngắt kết nối* → B quét thẻ vẫn chạy | ⬜ |
| **A9** | Không ai thấy dữ liệu của ai | mỗi bên upload thẻ + tạo hồ sơ + hỏi trợ lý, kiểm chéo | ⬜ |
| **A12** | Merge PR → tự deploy | merge một thay đổi nhỏ, xem CD chạy đủ vòng | ⬜ |

> Quét cổng phải làm **cả IP trần lẫn tên miền**, và đối chiếu **cả hai lớp** chặn. Một lớp hở
> là đủ phơi DB ra Internet — đó là toàn bộ nội dung rủi ro **R9**.
