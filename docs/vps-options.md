# Chọn máy chủ cho `ocrximi.io.vn`

> Chủ sở hữu: **Q** · Task **13.4b** · Cập nhật **2026-09-22**
> Quyết định kiến trúc kèm lý do: `Plan.md` mục 10. File này giữ **số đo** và **số tiền**.

**Chốt: Google Cloud — Compute Engine, một máy ảo `e2-medium`, trả bằng Free Trial $300 / 90 ngày.**

---

## 1. Hệ thống cần máy to bằng nào — đo, không đoán

Đo trên hệ đang chạy ở máy dev ngày 2026-09-21, 5 container `Up`, không ai thao tác:

| Đo được | Số | Suy ra |
|---------|-----|--------|
| RAM lúc **nhàn rỗi** | **956 MB** — `embedder` **760 MB** (đã nạp model), `api` 113 MB, `db` 58 MB, `cliproxy` 16 MB, `adminer` 9 MB | + OS & Docker daemon ~400 MB + Caddy ~20 MB ⇒ **~1,4 GB chỉ để đứng yên**; chạy thật ước **2–2,5 GB** ⇒ **4 GB là mức tối thiểu an toàn, 2 GB quá sát** |
| Tổng dung lượng image | **4,12 GB** — `embedder` 2,79 GB (kèm torch), `db` 621 MB, `api` 437 MB, `cliproxy` 286 MB, `adminer` 173 MB | bỏ `adminer` còn ~3,9 GB; **lúc build `embedder` cần thêm ~8 GB trống** ⇒ đĩa **≥ 40 GB** |
| Kiến trúc CPU | `cli-proxy-api` và `pgvector/pgvector:pg16` đều có bản **arm64**; `torch==2.14.0` có bánh xe `manylinux_2_28_aarch64` ngay trên index `download.pytorch.org/whl/cpu` mà `embedder/Dockerfile` đang dùng | **chạy được trên ARM mà không sửa một dòng Dockerfile nào** — mở đường cho phương án Oracle ở mục 5 |

Ba con số này **không phụ thuộc nhà cung cấp nào** — chúng đo hệ thống của mình. Đổi nhà cung
cấp thì yêu cầu vẫn nguyên: **≥ 4 GB RAM, ≥ 40 GB đĩa, x86 hay ARM đều được.**

---

## 2. Cấu hình đã chọn

| Hạng mục | Chọn | Vì sao |
|----------|------|--------|
| Dịch vụ | **Compute Engine** (VM) | mục 3 |
| Máy | **`e2-medium`** — 2 vCPU chia sẻ, **4 GB RAM** | đúng mức tối thiểu đo được. Chật thì lên `e2-standard-2` (8 GB) |
| Đĩa | **pd-balanced 50 GB** | yêu cầu ≥ 40 GB. ⚠️ Boot disk **mặc định chỉ 10 GB — phải sửa lúc tạo máy**, đây là chỗ dễ quên nhất |
| Region | **`asia-southeast1`** (Singapore) | ~30–50 ms từ Việt Nam |
| OS | **Debian 12** hoặc **Ubuntu 22.04 LTS** + Docker Engine | **Không dùng Container-Optimized OS**: COS cố tình không cho cài thêm gói, mà ta cần `docker compose` + build `embedder` ngay trên máy |
| IP | **Static external IP (reserved)** | mục 4 |
| Swap | **≥ 4 GB, bật TRƯỚC lần build đầu** | `pip install torch` là chỗ ngốn RAM nhất; 4 GB không swap dễ bị OOM killer cắt ngang giữa lúc build |
| Tiền | **Free Trial $300 / 90 ngày** | mục 4 |

---

## 3. Vì sao Compute Engine chứ không phải Cloud Run

Đây là chỗ dễ chọn sai nhất khi nghe "đổi sang Google Cloud", nên ghi thẳng ra để sau không ai
cân lại từ đầu: **Cloud Run không chạy được hệ thống này nếu không thiết kế lại gần như toàn bộ.**

| Thứ hệ thống đang cần | Cloud Run cho không? |
|----------------------|----------------------|
| `pgvector` giữ dữ liệu qua các lần khởi động | **Không** — container không trạng thái; phải đổi sang Cloud SQL (tính tiền riêng, và bản Postgres có pgvector là một biến nữa phải kiểm) |
| Volume `uploads` giữ ảnh danh thiếp | **Không** — phải chuyển sang Cloud Storage, tức sửa `_store()` và `_resolve_image()` của F1 |
| Volume `cliproxy_auths` giữ token OAuth | **Không** — mà mất token là mất luôn tiêu chí **A2/A10** |
| Cổng `51121` cho callback OAuth (I-04) | **Không** — Cloud Run chỉ phục vụ một cổng HTTP |
| Năm service nói chuyện trong một mạng Compose | **Không** — thành năm service riêng, mỗi cái một URL |

Cloud Run tốt cho ứng dụng không trạng thái. Hệ thống này có **ba volume và một cổng phi-HTTP**.
Compute Engine chạy **đúng bộ `docker-compose.prod.yml`** viết ở 13.6, không sửa một dòng mã ứng
dụng nào. GKE thì thừa: một máy, một bản demo, không có nhu cầu điều phối.

---

## 4. Tiền: $300 nuôi được bao lâu

Giá niêm yết tra ngày **2026-09-22** cho region **`asia-southeast1`**:

| Khoản | Đơn giá | Tháng (730 h) |
|-------|---------|---------------|
| `e2-medium` on-demand | $0,0413 / giờ | **$30,15** |
| pd-balanced 50 GB | $0,11 / GB / tháng | **$5,50** |
| Static external IP **đang gắn vào VM** | $0,005 / giờ | **$3,65** |
| Egress ra Internet | ~$0,12–0,19 / GB | vài xu — demo không đẩy dữ liệu đi đâu |
| | | **≈ $39,3 / tháng** |

**Kết luận quan trọng hơn con số: tiền không phải thứ hết trước — thời gian mới là.**
Ở mức ~$39/tháng, 90 ngày tiêu hết khoảng **$118 trong $300**. Nghĩa là **Free Trial hết hạn
theo ngày trong khi credit vẫn còn hơn một nửa.** Đừng lập kế hoạch dựa trên "còn bao nhiêu
tiền"; mốc phải nhớ là **ngày hết hạn trial**, ghi trong `docs/deploy.md`.

> ⚠️ **Hai thứ vẫn phải tự kiểm trên tài khoản thật, đừng tin bảng trên.**
> 1. **Giá**: bảng này là giá niêm yết công khai; đối chiếu lại trên Google Cloud Pricing
>    Calculator của chính tài khoản mình (khuyến mãi, cam kết, sustained-use có thể khác).
>    E2 **không** hưởng sustained use discount như N1/N2 — đừng trừ hao khoản đó.
> 2. **Quota**: tài khoản Free Trial mới hay bị giới hạn **số vCPU theo region** và **số IP
>    ngoài**. Kiểm ở *IAM & Admin → Quotas* trước khi tạo máy — hết quota thì nút "Tạo" báo
>    lỗi ngay và phải mở yêu cầu tăng, chờ thêm. Ghi số thật vào đây khi đã xem.

**Đã kiểm trên tài khoản thật, 2026-09-22:**

| Mục | Kết quả |
|-----|---------|
| **Ngày hết hạn Free Trial** | **2026-12-21** — đúng 90 ngày kể từ 2026-09-22 |
| Quota `asia-southeast1` (vCPU / IP ngoài / đĩa) | **tài khoản mới, quota còn nguyên mặc định** — thừa sức cho 2 vCPU + 1 static IP + 50 GB. Không phải xin tăng |
| Giá theo Pricing Calculator | *chưa đối chiếu* — vẫn dùng ước tính ≈$39,3/tháng từ giá niêm yết ở bảng trên |

Ở mức ~$39,3/tháng thì 90 ngày tiêu hết **≈$118 trong $300**: xác nhận đúng kết luận ở trên —
**ngày hết hạn mới là thứ chặn, không phải số dư credit.** Mốc phải nhớ: **2026-12-21**.

### Bốn điều phải nhớ với Google Cloud

- ⚠️ **IP ngoài mặc định là *ephemeral* — dừng/khởi động lại máy là đổi IP.** Bản ghi A của
  `ocrximi.io.vn` trỏ vào IP đó, nên đổi IP nghĩa là **tên miền chết mà không có lỗi nào báo**.
  Bắt buộc **reserve static external IP rồi gắn vào VM** (13.4) **trước khi** làm 13.5.
  Chiều ngược lại: IP đã reserve mà **không gắn vào máy nào thì bị tính tiền cao hơn** — xoá
  máy thì nhớ xoá hoặc gắn lại IP.
- **Tường lửa hai lớp** (rủi ro R9): **VPC firewall rule** của Google (mở đúng `80/443`, giữ
  `22`) **cộng với** `ufw` ngay trên máy. Mạng `default` của GCP có sẵn vài rule rộng tay
  (`default-allow-internal`, đôi khi cả `default-allow-rdp`) — phải rà, đừng mặc định là kín.
- **Hết Free Trial thì Google *dừng* tài nguyên chứ không tự trừ thẻ.** An toàn hơn về tiền so
  với DigitalOcean (hết credit là tính tiền thật), nhưng **nguy hiểm hơn về tính sẵn sàng**:
  tới hạn mà quên gia hạn là `ocrximi.io.vn` tắt.
- **Không bật snapshot tự động** cho ổ đĩa — tính tiền theo dung lượng ổ. Thay bằng `pg_dump`
  định kỳ ngay trên máy (`scripts/deploy.sh` tự dump trước mỗi lần deploy) rồi tải về.

---

## 5. Các phương án đã cân nhắc rồi bỏ

| Phương án | Cấu hình / giá | Vì sao bỏ |
|-----------|----------------|-----------|
| **DigitalOcean qua GitHub Student Pack** | Basic Droplet 4 GB / 2 vCPU / 80 GB, `sgp1`, ~$24/tháng, credit **$200 hạn 1 năm** | Chốt sáng 2026-09-21, **bỏ chiều cùng ngày theo quyết định của chủ dự án**. Về kỹ thuật vẫn dùng được nguyên vẹn. Mất theo nó: credit nuôi ~8 tháng thay vì 90 ngày. Được lại: **không phải chờ 72 h Student Pack** (I-30) |
| **Oracle Cloud Always Free A1** | ARM 4 OCPU / 24 GB, **miễn phí vĩnh viễn** | Đã kiểm là **chạy được** (ba mắt xích ARM đều có bản aarch64 — mục 1). Bỏ vì hay *out of capacity* khi tạo instance, và tài khoản Free thuần bị thu hồi instance nhàn rỗi. → **Giữ làm dự phòng số 1 khi credit cạn** |
| Hetzner CX22 | €3,79/tháng | Máy ở châu Âu, ~250–300 ms từ Việt Nam |
| VPS Việt Nam | ~150–200k VND/tháng | Rẻ và gần, nhưng phải trả tiền thật ngay |
| **Các gói *always free* của AWS/Azure/Google** | gồm cả `e2-micro` 1 GB của chính Google | **Loại thẳng**: không đủ cho riêng `embedder` (760 MB lúc nhàn rỗi). ⚠️ Đừng lẫn *always free* với **Free Trial $300** đang dùng — hai thứ khác hẳn nhau |

---

## 6. Build image ở đâu

**Quyết: build ngay trên máy ảo, KHÔNG đẩy lên registry.**

Máy x86 nên về lý thuyết build trong CI rồi đẩy image lên registry là đẹp nhất. Nhưng hạn mức
chặn: **GitHub Packages cho repo private ở gói Free chỉ có 500 MB lưu trữ + 1 GB truyền/tháng**
(Pro: 2 GB + 10 GB) — trong khi riêng image `embedder` đã **2,79 GB**. Đẩy lên GHCR là vượt hạn
mức và **bắt đầu bị tính tiền**.

Build tại chỗ rẻ và không rủi ro hạn mức, dựa trên một tính chất thật của dự án: **`embedder`
gần như không bao giờ đổi** (T dựng xong ở 3.10, từ đó không sửa). Nó chỉ build **một lần lúc
dựng máy**; các lần deploy sau Docker dùng lại layer cache và chỉ build lại `api` (437 MB, toàn
gói thuần Python). Bắt buộc **bật swap ≥ 4 GB trước lần build đầu**.

*Nếu sau này thật sự cần registry*, có hai lối:
1. **Artifact Registry của chính Google** — cùng dự án, cùng region với VM nên kéo image nhanh
   và không đi qua Internet công cộng, repo vẫn riêng tư. Tính tiền theo GB, ~4 GB thì nhỏ,
   **nhưng vẫn trừ vào $300**. Nếu phải chọn thì chọn lối này.
2. **Package công khai trên GHCR** (public thì miễn phí không giới hạn) — `embedder/` chỉ có
   wrapper FastAPI mỏng và model nguồn mở. Đây là **lựa chọn phải hỏi chủ dự án**, vì nó công
   khai một phần mã nguồn của repo private.

---

## 7. Nguồn

- [e2-medium — giá theo region](https://gcloud-compute.com/e2-medium.html) *(số liệu cập nhật 2026-09-21)*
- [pd-balanced — giá theo region](https://gcloud-compute.com/pd-balanced.html)
- [Google Cloud — Disk and image pricing](https://cloud.google.com/compute/disks-image-pricing)
- [Google Cloud — VPC network pricing (IP ngoài)](https://cloud.google.com/vpc/network-pricing)
