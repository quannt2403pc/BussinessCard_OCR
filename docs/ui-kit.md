# ui-kit.md — Token giao diện & luật viết chữ trên màn hình

> Chủ sở hữu: **Q** · Task **14.1** (D14) · Cập nhật 2026-09-23
> Áp dụng cho **toàn bộ** `templates/`. Trang của Q (`14.8`) và của T (`14.9`) đều thi hành file này.

Tài liệu này tồn tại vì một lý do đo được: trước D14, `templates/` có **121 chỗ gọi thẳng `sky-*`**,
**0 `aria-label`**, **28 chuỗi thông báo dài ≥ 45 ký tự**, và mỗi trang tự chọn nhãn nút theo cảm tính.
Không chốt token và luật trước thì hai người quét 17 file sẽ ra hai phong cách.

---

## 1. Màu

Lấy từ chính `static/img/LogoOCRXimi.png`, không phải từ sở thích.

| Token | Mã | Dùng ở đâu |
|-------|-----|-----------|
| `brand-50` | `#EFF6FF` | Nền mục nav đang mở, nền badge nhạt |
| `brand-100` | `#DBEAFE` | Nền dấu trích dẫn `[n]` |
| `brand-600` | `#2563EB` | **Màu chính** — nút chính, liên kết, viền focus. Thay toàn bộ `sky-600` |
| `brand-700` | `#1D4ED8` | Trạng thái hover của nút chính, chữ trên nền `brand-50` |
| `brand-ink` | `#1E293B` | Chữ tiêu đề (khớp navy của wordmark) |
| `brand-accent` | `#0369A1` | **Chỉ** dùng cho trạng thái *đang quét / đang nghĩ* + thanh tiến trình. Không dùng làm màu nút |
| `surface` | `#F8FAFC` | Nền trang |
| `line` | `#E2E8F0` | Viền |
| `muted` | `#475569` | Chữ phụ. **Đây là mức xám nhạt nhất được phép cho chữ** — `slate-400` chỉ đạt 2,56:1 |

> ⚠️ **`brand-accent` không phải là ô cyan `#0EA5E9` lấy nguyên từ logo.** Đo ở task 14.10:
> `#0EA5E9` trên nền trắng chỉ được **2,77:1** — trượt cả ngưỡng 4,5:1 của chữ lẫn ngưỡng 3:1 của
> thành phần giao diện (WCAG 1.4.11), nên nó không dùng được cho *cả* chữ "Đang tra cứu…" *lẫn*
> thanh tiến trình, tức là đúng hai chỗ nó sinh ra để phục vụ. Token giữ sắc cyan ấy nhưng đậm
> xuống `#0369A1` (**5,93:1**). Bảng số đo đầy đủ ở mục 10.

**Màu ngữ nghĩa giữ nguyên `emerald` / `amber` / `rose`.** Đổi chúng theo thương hiệu là làm hỏng nghĩa:
người dùng đọc "xanh lá = xong, đỏ = hỏng" trước khi đọc chữ.

Hai thứ **cố ý không lấy**, ghi ra để sau này không ai tưởng là bỏ sót:

- **Màu nhấn cam `#EA580C`** mà bộ dữ liệu thiết kế gợi ý cho "SaaS tin cậy" — logo này đơn sắc xanh,
  chêm cam vào là hai thương hiệu đánh nhau trên cùng một trang. Nhấn bằng **độ đậm + khoảng trắng**.
- **Glassmorphism** — nền mờ trong suốt đọc rất tệ trên bảng dữ liệu dày, mà `/cards` và `/companies`
  chính là hai bảng dữ liệu dày. Kính mờ **chỉ dùng đúng một chỗ**: panel bong bóng chat nổi trên nội dung.

## 2. Chữ

**Plus Jakarta Sans**, Google Fonts, đúng **3 nét 400/500/600** (không tải 7 nét — mỗi nét là một lượt
tải chặn render). Khai bằng `preconnect` + `display=swap` trong `base.html`.

| Vai trò | Lớp |
|---------|-----|
| Tiêu đề trang | `text-2xl font-semibold text-brand-ink` |
| Tiêu đề khối | `text-sm font-semibold text-brand-ink` |
| Chữ thường | `text-sm text-slate-700` |
| Chữ phụ / chú thích | `text-xs text-muted` |
| **Số liệu** | `tabular-nums` — bắt buộc, để số không nhảy ngang khi đếm |

## 3. Khoảng cách, bo góc, đổ bóng

- **Thang 4/8**: chỉ dùng `1 · 2 · 3 · 4 · 6 · 8 · 12` của Tailwind (4–48px). Không `p-5`, không `gap-7`.
- **Bo góc**: `rounded-md` (6px) cho nút/ô nhập · `rounded-lg` (8px) cho thẻ · `rounded-full` cho chip và
  nút tròn.
- **Đổ bóng**: `shadow-sm` cho thẻ nổi nhẹ · `shadow-lg` cho panel nổi trên nội dung. **Không có mức giữa** —
  ba mức bóng trên một trang thì mắt không đọc ra thứ bậc nào.
- **Vùng bấm tối thiểu 44×44px** kể cả khi icon chỉ 20px (WCAG 2.2 AA, tiêu chí *Target Size*).

## 4. Thang `z-index`

Khai thành lớp tiện ích trong `static/css/app.css`. Bong bóng chat nổi trên **mọi** trang, nên thiếu thang
này là chắc chắn có chỗ bị che — và chỗ bị che bao giờ cũng lộ ra trên máy người khác, không phải máy mình.

| Lớp | `z-index` | Dùng cho |
|-----|-----------|----------|
| *(không khai)* | `0` | Nội dung trang |
| `z-header` | `20` | Header dính |
| `z-bubble` | `40` | Nút tròn bong bóng chat |
| `z-panel` | `50` | Panel chat, hộp thoại, menu người dùng |
| `z-toast` | `60` | Thông báo nổi |

## 5. Luật viết thông báo

1. **Một câu, ≤ 80 ký tự.** Dài hơn thì cắt, đừng xuống dòng.
2. Nói **nguyên nhân + việc cần làm**, không mô tả lại thao tác vừa rồi.
3. **Không lặp lại chữ trên nút.** Người dùng vừa bấm *Xác nhận*, họ biết mình vừa xác nhận.
4. Không có chữ "Lỗi:" ở đầu — màu đỏ và icon đã nói rồi.
5. Ô chứa thông báo **bắt buộc** có `aria-live="polite"` (lỗi: `assertive`). Không có thì trình đọc màn
   hình câm lặng trước đúng thứ người dùng đang chờ.
6. **Toast báo thành công tự tắt sau 4s; toast báo lỗi ở lại** kèm nút thử lại — lỗi biến mất trước khi
   đọc xong là lỗi mất luôn.

| Đừng viết | Viết là |
|-----------|---------|
| "Đã xảy ra lỗi trong quá trình tải ảnh lên máy chủ, vui lòng thử lại sau." | "Tải ảnh không xong. Thử lại." |
| "Bạn chưa kết nối với CLIProxy. Hãy vào trang Cài đặt và bấm nút Kết nối." | "Chưa kết nối AI. Mở Cài đặt để kết nối." |
| "Không tìm thấy dữ liệu liên quan trong Knowledge Base cho câu hỏi này." | "Chưa có dữ liệu cho câu này." |

## 6. Luật nhãn nút

1. **≤ 2 từ.** "Tạo hồ sơ doanh nghiệp" → "Lập hồ sơ".
2. Nút **chỉ có icon bắt buộc có `aria-label`** đọc ra hành động ("Xoá danh thiếp", không phải "Xoá").
3. **Hành động xoá luôn giữ chữ.** Icon thùng rác đứng một mình là lời mời bấm nhầm.
4. Icon đứng cạnh chữ là **trang trí** → `aria-hidden="true"`, đừng để trình đọc màn hình đọc hai lần.
5. Nút chính mỗi màn hình **đúng một cái** (`bg-brand-600`), còn lại là nút viền.

## 7. Icon

Một bộ **duy nhất**: Lucide outline 24px, nét 1.75. Trộn hai bộ icon là thứ nhìn thấy ngay cả khi không
biết vì sao. Dùng qua sprite + macro, không dán SVG thẳng vào template:

```jinja
{% from "_macros.html" import icon, banner %}

<button class="btn-primary">{{ icon("scan") }} Quét</button>          {# icon cạnh chữ #}
<button class="btn-icon" aria-label="Xoá danh thiếp">{{ icon("trash") }}</button>
{{ banner("warn", "Chưa kết nối AI. Mở Cài đặt để kết nối.") }}
```

Sprite ở `static/img/icons.svg`, danh sách tên xem chính file đó. Thêm icon mới → thêm một `<symbol>`,
**không** thêm thư viện.

## 8. Bộ nhận diện

| File | Cỡ | Dùng ở |
|------|-----|--------|
| `static/img/logo-mark-64.webp` · `-128.webp` | 64 · 128 | Header, khối nhỏ |
| `static/img/logo-full.webp` | 480px ngang | Trang đăng nhập (14.9 của T) — **không dùng ở chân trang**: đo ở 14.10, logo thu xuống 16px là một vệt mờ, chân trang để chữ |
| `static/img/favicon.ico` | 16/32/48 | Tab trình duyệt |
| `static/img/apple-touch-icon.png` | 180 | iOS thêm vào màn hình chính (nền trắng — iOS không tôn trọng nền trong suốt) |
| `static/img/og-image.png` | 1200×630 | Thẻ xem trước khi chia sẻ link |

> ⚠️ **Dấu huyền trên chữ `I` là CỐ Ý.** Tên đọc là **"OCR Xì Mi"**, `ocrximi.io.vn` là bản viết không dấu
> của chính nó. Ba hệ quả bắt buộc:
> 1. **Không bao giờ đánh máy lại wordmark bằng font** — gõ "OCRXimi" bằng Plus Jakarta Sans là mất dấu,
>    tức mất luôn cách đọc tên. Wordmark chỉ lấy từ file ảnh.
> 2. `alt` của logo và `og:site_name` viết là **`OCR Xì Mi`** (có dấu, có khoảng trắng) — trình đọc màn hình
>    gặp `OCRXÌMI` sẽ đánh vần từng chữ cái.
> 3. Bản chỉ-biểu-tượng dùng ở header/favicon vì **nét chữ bết ở cỡ nhỏ**, không phải để né lỗi dấu.

## 9. Ba thứ D14 cố ý KHÔNG làm

1. **Chế độ tối** — ngoài phạm vi demo từ `Plan.md` mục 1.3. Bù lại màu chốt bằng **biến CSS** nên thêm sau
   là đổi một bảng biến, không phải sửa lại 17 file.
2. **Toàn bộ giao diện di động** — D14 chỉ lo **375px không tràn ngang** ở nav, trang chủ, bong bóng chat.
   Bảng `/cards`, `/companies` trên điện thoại vẫn cuộn ngang.
3. **Đổi `templates/` sang component/SPA** — không có bước build, và đổi khung ở ngày dự phòng cuối là tự
   chuốc rủi ro.

---

## 10. Số đo tương phản (task 14.10, đo lại khi đổi bất kỳ token nào)

Ngưỡng: **4,5:1** cho chữ thường (WCAG 1.4.3 AA) · **3:1** cho thành phần giao diện và chữ lớn.

| Cặp màu | Tỉ lệ | |
|---------|-------|---|
| `brand-600` trên trắng | 5,17:1 | ✅ |
| trắng trên `brand-600` (nút chính) | 5,17:1 | ✅ |
| `brand-700` trên `brand-50` (nav đang mở) | 6,16:1 | ✅ |
| `brand-700` trên `brand-100` (dấu trích dẫn) | 5,49:1 | ✅ |
| `brand-ink` trên trắng | 14,63:1 | ✅ |
| `muted` trên `surface` | 7,24:1 | ✅ |
| `brand-accent` `#0369A1` trên trắng | 5,93:1 | ✅ |
| amber-900 / emerald-800 / rose-800 trên nền 50 tương ứng | 8,75 / 7,29 / 7,30 | ✅ |
| ~~`#0EA5E9` (cyan nguyên bản của logo) trên trắng~~ | **2,77:1** | ❌ đã thay |
| ~~`slate-400` trên trắng~~ | **2,56:1** | ❌ đã thay bằng `muted` |

Hai dòng cuối là **lỗi thật bắt được lúc nghiệm thu**, không phải giả định: `slate-400` là màu mặc
định của mọi nhãn phụ từ D4 (chân trang, chữ "Nguồn", "Thử hỏi", nhãn ô số liệu) và không ai để ý
vì nó *trông* nhã.
