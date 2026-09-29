/** Nhãn ngôn ngữ dùng chung: mã ISO 639-1 -> cờ + tên tiếng Việt. Chủ sở hữu: Q | Task: I-47 */
(function (global) {
  "use strict";

  const LANGUAGES = {
    vi: ["vn", "Tiếng Việt"],
    en: ["gb", "Tiếng Anh"],
    ja: ["jp", "Tiếng Nhật"],
    ko: ["kr", "Tiếng Hàn"],
    zh: ["cn", "Tiếng Trung"],
    ar: ["sa", "Tiếng Ả Rập"],
    th: ["th", "Tiếng Thái"],
    km: ["kh", "Tiếng Khmer"],
    lo: ["la", "Tiếng Lào"],
    my: ["mm", "Tiếng Miến Điện"],
    id: ["id", "Tiếng Indonesia"],
    ms: ["my", "Tiếng Mã Lai"],
    tl: ["ph", "Tiếng Philippines"],
    hi: ["in", "Tiếng Hindi"],
    ta: ["in", "Tiếng Tamil"],
    bn: ["bd", "Tiếng Bengal"],
    ur: ["pk", "Tiếng Urdu"],
    fa: ["ir", "Tiếng Ba Tư"],
    he: ["il", "Tiếng Do Thái"],
    tr: ["tr", "Tiếng Thổ Nhĩ Kỳ"],
    ru: ["ru", "Tiếng Nga"],
    uk: ["ua", "Tiếng Ukraina"],
    fr: ["fr", "Tiếng Pháp"],
    de: ["de", "Tiếng Đức"],
    es: ["es", "Tiếng Tây Ban Nha"],
    pt: ["pt", "Tiếng Bồ Đào Nha"],
    it: ["it", "Tiếng Ý"],
    nl: ["nl", "Tiếng Hà Lan"],
    pl: ["pl", "Tiếng Ba Lan"],
    cs: ["cz", "Tiếng Séc"],
    hu: ["hu", "Tiếng Hungary"],
    ro: ["ro", "Tiếng Rumani"],
    el: ["gr", "Tiếng Hy Lạp"],
    sv: ["se", "Tiếng Thuỵ Điển"],
    da: ["dk", "Tiếng Đan Mạch"],
    no: ["no", "Tiếng Na Uy"],
    fi: ["fi", "Tiếng Phần Lan"],
  };

  const REGION_COUNTRIES = {
    "zh-tw": "tw",
    "zh-hant": "tw",
    "zh-hk": "hk",
    "en-us": "us",
    "en-au": "au",
    "pt-br": "br",
    "es-mx": "mx",
    "fr-ca": "ca",
  };

  const PRIMARY = ["vi", "en", "ja", "ko", "zh", "ar", "th", "fr", "de", "es", "ru"];

  function describeLanguage(code) {
    const raw = String(code || "").trim();
    if (!raw) return null;
    const key = raw.toLowerCase().replace(/_/g, "-");
    const entry = LANGUAGES[key.split("-")[0]];
    if (!entry) return { country: null, name: raw.toUpperCase(), code: raw, known: false };
    return { country: REGION_COUNTRIES[key] || entry[0], name: entry[1], code: raw, known: true };
  }

  function languageLabel(code) {
    const info = describeLanguage(code);
    return info ? info.name : "";
  }

  function languageOptions() {
    return PRIMARY.map((code) => ({ code, label: languageLabel(code) }));
  }

  /** Cờ vẽ bằng ảnh, không bằng emoji: Windows không có glyph cờ nên 🇸🇦 hiện ra thành chữ "SA". */
  function languageElement(code) {
    const span = document.createElement("span");
    span.className = "inline-flex items-center gap-1.5";
    const info = describeLanguage(code);
    if (!info) {
      span.textContent = "—";
      return span;
    }
    if (info.country) {
      const flag = document.createElement("img");
      flag.src = `https://flagcdn.com/${info.country}.svg`;
      flag.alt = "";
      flag.loading = "lazy";
      flag.className = "h-3.5 w-5 shrink-0 rounded-sm object-cover ring-1 ring-slate-200";
      span.append(flag);
    }
    span.append(document.createTextNode(info.name));
    return span;
  }

  global.describeLanguage = describeLanguage;
  global.languageLabel = languageLabel;
  global.languageOptions = languageOptions;
  global.languageElement = languageElement;
})(window);
