/**
 * Ô thông báo dùng chung cho mọi trang.
 *
 * Chủ sở hữu: Q | Task: 14.8 | luật ở docs/ui-kit.md mục 5
 *
 * Gom ra đây vì trước D14 có **năm bản copy** của cùng một hàm `message(text, kind)`, và chúng đã
 * kịp lệch nhau về màu nền cho cùng một loại tin.
 *
 *     toastShow(box, "Đã lưu.", "success");
 *     toastShow(box, "Tải ảnh không xong. Thử lại.", "error", () => upload(file));
 *     toastShow(box, "Chưa xác thực.", "error", {href: "https://…", label: "Xác thực ngay"});
 *     toastClear(box);
 *
 * Tham số cuối nhận **hàm** (dựng nút *Thử lại*) hoặc **`{href, label}`** (dựng một liên kết —
 * dùng khi việc cần làm nằm ngoài ứng dụng, I-47).
 *
 * `box` phải có `aria-live` trong markup (`polite` cho tin thường, `assertive` cho lỗi). Không có
 * thì trình đọc màn hình im lặng trước đúng thứ người dùng đang chờ.
 */
(function (global) {
  "use strict";

  const TONES = {
    success: "bg-emerald-50 text-emerald-800",
    info: "bg-brand-50 text-brand-700",
    warn: "bg-amber-50 text-amber-900",
    error: "bg-rose-50 text-rose-800",
  };

  //: Tin vui tự tắt; tin xấu ở lại. Thông báo lỗi biến mất trước khi đọc xong là lỗi mất luôn.
  const AUTO_HIDE = { success: 4000, info: 4000 };

  //: Nhận cả "ok" (tên các trang dùng từ D4) thay vì đi sửa 12 chỗ gọi. Mặc định về `info`,
  //: không về `success`.
  const ALIAS = { ok: "success" };

  const timers = new WeakMap();

  //: Lớp lề do chính markup quyết định (`data-toast-class`), mặc định `mt-4`: ép cứng ở đây thì
  //: ô trong hộp thoại của /settings nhảy lên 4px mỗi lần báo tin.
  function baseClass(box) {
    return (box.dataset.toastClass || "mt-4") + " rounded-md px-4 py-3 text-sm";
  }

  function toastClear(box) {
    if (!box) return;
    clearTimeout(timers.get(box));
    box.replaceChildren();
    box.className = baseClass(box) + " hidden";
  }

  function toastShow(box, text, kind, retry) {
    if (!box) return;
    clearTimeout(timers.get(box));
    box.replaceChildren();

    const tone = TONES[ALIAS[kind] || kind] || TONES.info;
    box.className = baseClass(box) + " flex items-center gap-3 " + tone;

    const label = document.createElement("span");
    label.className = "min-w-0 flex-1";
    label.textContent = text;
    box.appendChild(label);

    if (typeof retry === "function") {
      const again = document.createElement("button");
      again.type = "button";
      again.className = "btn shrink-0";
      again.textContent = "Thử lại";
      again.addEventListener("click", () => {
        toastClear(box);
        retry();
      });
      box.appendChild(again);
    } else if (retry && retry.href) {
      // Việc cần làm nằm **ngoài** ứng dụng — hiện tại chỉ có một: mở trang xác thực tài khoản
      // của Google (I-47). Nút "Thử lại" vô nghĩa ở đây: bấm bao nhiêu lần cũng hỏng cho tới khi
      // người dùng làm xong việc bên kia.
      //
      // Chỉ nhận `https:`. URL đi qua ba lớp (Google → CLIProxy → backend) mới tới đây, và một
      // `javascript:` lọt vào là chạy được ngay trên trang đang giữ token OAuth.
      let safe = null;
      try {
        if (new URL(retry.href, location.origin).protocol === "https:") safe = retry.href;
      } catch (err) { /* không parse được thì bỏ, chữ vẫn hiện */ }

      if (safe) {
        const link = document.createElement("a");
        link.href = safe;
        link.target = "_blank";
        link.rel = "noopener noreferrer";  // thiếu `noopener` thì tab mới nắm được `window.opener`
        link.className = "btn shrink-0";
        link.textContent = retry.label || "Mở";
        box.appendChild(link);
      }
    }

    const delay = AUTO_HIDE[ALIAS[kind] || kind];
    if (delay) timers.set(box, setTimeout(() => toastClear(box), delay));
  }

  global.toastShow = toastShow;
  global.toastClear = toastClear;
})(window);
