/**
 * Trợ lý AI — logic hỏi đáp của bong bóng chat.
 *
 * Chủ sở hữu: Q | Task: 14.4, 14.5, EX-10
 *
 * Mọi phần tử tìm theo `data-a=…` **bên trong `root`**, nên nhiều thể hiện sống chung một trang
 * được — đó là lý do logic này tách khỏi template thay vì bám cứng vào `id` của một trang.
 *
 * Hợp đồng với template (mọi thuộc tính đều nằm trong `root`):
 *   [data-a="form"]      <form> gửi câu hỏi            (bắt buộc)
 *   [data-a="question"]  <textarea>                    (bắt buộc)
 *   [data-a="send"]      nút gửi                       (bắt buộc)
 *   [data-a="thread"]    khung hội thoại               (bắt buộc)
 *   [data-a="message"]   ô thông báo, nên có aria-live (tuỳ)
 *   [data-a="samples"]   khối câu hỏi gợi ý            (tuỳ)
 *   [data-a="scope"]     <select> lọc phạm vi          (tuỳ)
 *   [data-a="new"]       nút hội thoại mới             (tuỳ)
 *   [data-sample]        nút câu hỏi gợi ý             (tuỳ, nhiều cái)
 *
 * Tuỳ chọn:
 *   sessionId    id phiên nạp lại lúc khởi động.
 *   storageKey   khoá `sessionStorage` để nhớ phiên qua các lần chuyển trang.
 */
(function (global) {
  "use strict";

  let instances = 0;

  function initAssistant(root, options) {
    const opts = options || {};
    const scopeId = "a" + ++instances;

    const pick = (name) => root.querySelector('[data-a="' + name + '"]');
    const form = pick("form");
    const input = pick("question");
    const send = pick("send");
    const thread = pick("thread");
    const messageBox = pick("message");
    const samples = pick("samples");
    const scope = pick("scope");
    const newChat = pick("new");

    if (!form || !input || !send || !thread) {
      throw new Error("initAssistant: thiếu [data-a=form|question|send|thread] trong root");
    }

    let sessionId = opts.sessionId || null;
    let answerCount = 0;
    let busy = false;
    let messageTimer = null;

    // --- tiện ích ------------------------------------------------------------

    function el(tag, className, text) {
      const node = document.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined) node.textContent = text;
      return node;
    }

    function remember(id) {
      sessionId = id;
      if (opts.storageKey) {
        try {
          if (id) sessionStorage.setItem(opts.storageKey, id);
          else sessionStorage.removeItem(opts.storageKey);
        } catch (_) {
          /* chế độ riêng tư chặn sessionStorage — mất trí nhớ giữa các trang, không phải lỗi */
        }
      }
    }

    /**
     * Thông báo một dòng. Luật ở docs/ui-kit.md mục 5: lỗi thì **ở lại** kèm nút thử lại, còn
     * cảnh báo tự tắt sau 4s — thông báo biến mất trước khi đọc xong là thông báo mất luôn.
     */
    function message(text, tone, retry) {
      if (!messageBox) return;
      clearTimeout(messageTimer);
      messageBox.replaceChildren();
      if (!text) {
        messageBox.className = "hidden";
        return;
      }
      const colours =
        tone === "warn" ? "bg-amber-50 text-amber-900" : "bg-rose-50 text-rose-800";
      messageBox.className =
        "mt-4 flex items-center gap-3 rounded-md px-4 py-3 text-sm " + colours;
      messageBox.appendChild(el("span", "min-w-0 flex-1", text));
      if (typeof retry === "function") {
        const again = el("button", "btn shrink-0", "Thử lại");
        again.type = "button";
        again.addEventListener("click", () => {
          message("");
          retry();
        });
        messageBox.appendChild(again);
      }
      if (tone === "warn") messageTimer = setTimeout(() => message(""), 4000);
    }

    async function readError(res) {
      try {
        const body = await res.json();
        if (typeof body.detail === "string") return body.detail;
        // 422 của FastAPI: detail là mảng lỗi từng trường.
        if (Array.isArray(body.detail) && body.detail.length) {
          return body.detail[0].msg || "Lỗi HTTP " + res.status;
        }
      } catch (_) {
        /* body không phải JSON */
      }
      return "Lỗi HTTP " + res.status;
    }

    function hostOf(url) {
      try {
        return new URL(url).host;
      } catch (_) {
        return url;
      }
    }

    // --- vẽ hội thoại --------------------------------------------------------

    function addQuestion(text) {
      const row = el("div", "flex justify-end");
      row.appendChild(
        el(
          "div",
          "max-w-2xl whitespace-pre-wrap rounded-lg bg-brand-600 px-4 py-2.5 text-sm text-white",
          text,
        ),
      );
      thread.appendChild(row);
      return row;
    }

    /**
     * Biến các dấu [n] trong câu trả lời thành nút bấm cuộn tới đúng thẻ trích dẫn.
     *
     * Dựng bằng createElement + textContent chứ không innerHTML: nội dung này do model sinh ra
     * từ dữ liệu người dùng nhập (tên công ty, ghi chú trên danh thiếp). Ghép chuỗi HTML ở đây là
     * mở đúng một đường XSS đi từ ảnh danh thiếp tới trình duyệt.
     */
    function renderAnswer(box, text, citations, key) {
      const pattern = /\[(\d+)\]/g;
      let last = 0;
      let match;
      while ((match = pattern.exec(text)) !== null) {
        if (match.index > last) {
          box.appendChild(document.createTextNode(text.slice(last, match.index)));
        }
        const index = Number(match[1]);
        if (index >= 1 && index <= citations.length) {
          const mark = el(
            "a",
            "mx-0.5 rounded bg-brand-100 px-1 text-xs font-medium text-brand-700 no-underline hover:bg-brand-600 hover:text-white",
            "[" + index + "]",
          );
          mark.href = "#" + key + "-" + index;
          box.appendChild(mark);
        } else {
          box.appendChild(document.createTextNode(match[0]));
        }
        last = pattern.lastIndex;
      }
      if (last < text.length) box.appendChild(document.createTextNode(text.slice(last)));
    }

    function renderCitations(citations, key) {
      const wrap = el("div", "mt-3 space-y-2");
      wrap.appendChild(el("div", "text-xs uppercase tracking-wide text-muted", "Nguồn"));

      citations.forEach((citation, position) => {
        const number = position + 1;
        const card = el(
          "a",
          "link block rounded-md border border-slate-200 bg-slate-50 px-3 py-2 transition hover:border-brand-600 hover:bg-white",
        );
        // `id` mang tiền tố của thể hiện: hai bản trợ lý trên cùng một trang thì dấu [n] của bản
        // này không được nhảy sang thẻ nguồn của bản kia.
        card.id = key + "-" + number;
        card.href = citation.url;

        const head = el("div", "flex items-baseline gap-2");
        head.appendChild(el("span", "text-xs font-semibold text-brand-700", "[" + number + "]"));
        head.appendChild(el("span", "text-sm font-medium text-slate-800", citation.title));
        if (typeof citation.score === "number") {
          head.appendChild(el("span", "ml-auto text-xs text-muted", citation.score.toFixed(2)));
        }
        card.appendChild(head);
        card.appendChild(el("div", "mt-1 text-xs text-slate-600", citation.snippet));
        wrap.appendChild(card);

        if (citation.source_urls && citation.source_urls.length) {
          const refs = el("div", "flex flex-wrap gap-2 pl-3 text-xs");
          refs.appendChild(el("span", "text-muted", "Nguồn tham khảo:"));
          citation.source_urls.forEach((url) => {
            const link = el("a", "link text-slate-500 underline hover:text-brand-700", hostOf(url));
            link.href = url;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            refs.appendChild(link);
          });
          wrap.appendChild(refs);
        }
      });

      return wrap;
    }

    function addAnswer(text, citations) {
      const key = scopeId + "c" + ++answerCount;
      const row = el("div", "flex justify-start");
      const bubble = el(
        "div",
        "max-w-3xl rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-sm",
      );

      const body = el("div", "whitespace-pre-wrap text-sm text-slate-800");
      renderAnswer(body, text, citations || [], key);
      bubble.appendChild(body);

      if (citations && citations.length) {
        bubble.appendChild(renderCitations(citations, key));
      } else {
        bubble.appendChild(
          el("div", "mt-2 text-xs text-muted", "Không có nguồn nào được trích dẫn."),
        );
      }

      row.appendChild(bubble);
      thread.appendChild(row);
      return row;
    }

    function addPending() {
      const row = el("div", "flex justify-start");
      row.appendChild(
        el(
          "div",
          "rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm text-brand-accent",
          "Đang tra cứu…",
        ),
      );
      thread.appendChild(row);
      scrollDown();
      return row;
    }

    /** Panel nổi cuộn trong lòng khung hội thoại — cuộn cả cửa sổ là việc của trang, mà từ
     *  `EX-09` thì trợ lý không còn là một trang nữa. */
    function scrollDown() {
      thread.scrollTop = thread.scrollHeight;
    }

    // --- gửi câu hỏi ---------------------------------------------------------

    async function ask(text) {
      if (busy || !text.trim()) return;
      busy = true;
      send.disabled = true;
      message("");
      if (samples) samples.classList.add("hidden");

      addQuestion(text);
      const pending = addPending();

      try {
        const res = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            question: text,
            session_id: sessionId,
            filters: { source_type: (scope && scope.value) || null, company_id: null },
          }),
        });
        if (!res.ok) throw new Error(await readError(res));

        const data = await res.json();
        remember(data.session_id);
        pending.remove();
        addAnswer(data.answer, data.citations);

        if (data.context_chunks === 0) {
          message("Chưa có dữ liệu cho câu này.", "warn");
        }
      } catch (err) {
        pending.remove();
        message("Không hỏi được: " + err.message, "error", () => ask(text));
      } finally {
        busy = false;
        send.disabled = false;
        scrollDown();
      }
    }

    // --- nạp lại một phiên đã có --------------------------------------------

    async function loadSession(id) {
      try {
        const res = await fetch("/api/chat/" + id);
        if (!res.ok) throw new Error(await readError(res));
        const data = await res.json();
        data.messages.forEach((item) => {
          if (item.role === "user") addQuestion(item.content);
          else addAnswer(item.content, item.citations);
        });
        if (data.messages.length && samples) samples.classList.add("hidden");
        scrollDown();
      } catch (err) {
        remember(null);
        message("Không mở lại được hội thoại: " + err.message, "warn");
      }
    }

    function reset() {
      remember(null);
      thread.replaceChildren();
      answerCount = 0;
      message("");
      if (samples) samples.classList.remove("hidden");
    }

    // --- gắn sự kiện ---------------------------------------------------------

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const text = input.value;
      input.value = "";
      input.style.height = "auto";
      ask(text);
    });

    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        form.requestSubmit();
      }
    });

    input.addEventListener("input", (event) => {
      event.target.style.height = "auto";
      event.target.style.height = event.target.scrollHeight + "px";
    });

    root.querySelectorAll("[data-sample]").forEach((button) => {
      button.addEventListener("click", () => ask(button.dataset.sample));
    });

    if (newChat) newChat.addEventListener("click", reset);

    if (sessionId) loadSession(sessionId);

    return {
      ask,
      reset,
      focus: () => input.focus(),
      get sessionId() {
        return sessionId;
      },
    };
  }

  global.initAssistant = initAssistant;
})(window);
