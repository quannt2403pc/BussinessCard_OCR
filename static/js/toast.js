/**
 * Ô thông báo dùng chung cho mọi trang.
 *
 * Chủ sở hữu: Q | Task: 14.8 (D14) | luật ở docs/ui-kit.md mục 5
 *
 * Vì sao gom ra đây: trước D14 có **năm bản copy** của cùng một hàm `message(text, kind)` nằm rải
 * trong `cards/list.html`, `cards/upload.html`, `cards/detail.html`, `cards/batch.html`,
 * `settings.html` — và chúng đã kịp lệch nhau (chỗ nền xanh, chỗ nền lục cho cùng một loại tin).
 * Hai luật mới của `14.1` — *thành công tự tắt sau 4s, lỗi ở lại kèm nút thử lại* — nếu vẫn để
 * năm chỗ thì phải sửa năm lần và chắc chắn sót một.
 *
 *     toastShow(box, "Đã lưu.", "success");
 *     toastShow(box, "Tải ảnh không xong. Thử lại.", "error", () => upload(file));
 *     toastClear(box);
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

  //: "ok" là tên các trang của Q dùng từ D4. Nhận cả hai thay vì đi sửa 12 chỗ gọi — và để
  //: mặc định về `info`, không về `success`: trước D14 mỗi file mặc định một kiểu (list.html
  //: xanh lục, batch.html xanh dương) cho cùng một lời gọi thiếu tham số.
  const ALIAS = { ok: "success" };

  const timers = new WeakMap();

  //: Lớp lề của ô thông báo do chính markup quyết định (`data-toast-class`), mặc định `mt-4`.
  //: Ô trong hộp thoại của /settings dùng `mt-5`; ép cứng ở đây là mỗi lần báo tin hộp thoại lại
  //: nhảy lên 4px.
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
    }

    const delay = AUTO_HIDE[ALIAS[kind] || kind];
    if (delay) timers.set(box, setTimeout(() => toastClear(box), delay));
  }

  global.toastShow = toastShow;
  global.toastClear = toastClear;
})(window);
