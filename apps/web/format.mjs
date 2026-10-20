// Shared presentation helpers: readable IDs, times and labels. Pure functions plus tiny DOM builders; no page state.

function node(tag, className, text) {
  const n = document.createElement(tag);
  if (className) n.className = className;
  if (text !== undefined && text !== null) n.textContent = text; // textContent only: API data is never parsed as HTML
  return n;
}

/** First 8 characters after the run_ prefix. */
export function shortId(id) {
  return String(id ?? "").replace(/^[a-z]+_/, "").slice(0, 8);
}

/** Link to a run in the Runs workspace. */
export function runLink(runId) {
  return "./runs.html?run=" + encodeURIComponent(runId);
}

/** "12m ago" (default) or "12 minutes ago" ({ long: true }); invalid input gives "". */
export function relativeTime(iso, now = Date.now(), { long = false } = {}) {
  const t = Date.parse(iso ?? "");
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 45) return "just now";
  const [n, short, word] = s < 3600 ? [Math.round(s / 60), "m", "minute"] : s < 86400 ? [Math.round(s / 3600), "h", "hour"] : [Math.round(s / 86400), "d", "day"];
  return long ? n + " " + word + (n === 1 ? "" : "s") + " ago" : n + short + " ago";
}

/** Timestamp without microseconds: "17 Oct 2026, 11:20 UTC". Empty for invalid input. */
export function absoluteTime(iso) {
  const t = Date.parse(iso ?? "");
  if (Number.isNaN(t)) return "";
  const d = new Date(t);
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const two = (n) => String(n).padStart(2, "0");
  return d.getUTCDate() + " " + months[d.getUTCMonth()] + " " + d.getUTCFullYear() + ", " + two(d.getUTCHours()) + ":" + two(d.getUTCMinutes()) + " UTC";
}

/** <time> showing a relative label with the exact time on hover. */
export function timeEl(iso, now = Date.now(), options = {}) {
  const t = node("time", "", relativeTime(iso, now, options) || "Unknown");
  if (relativeTime(iso, now)) { t.dateTime = iso; t.title = absoluteTime(iso) + " (" + iso + ")"; }
  return t;
}

/** Readable run-state label: "awaiting_playback" -> "Awaiting playback". Shared by the runs table, drawer and detail views. */
export function stateLabel(state) {
  return humanise(state ?? "unknown");
}

/** Make a horizontally scrolling wrapper keyboard-reachable: focusable named region (focus ring in ops.css). Returns the node. */
export function scrollRegion(wrapper, label) {
  wrapper.tabIndex = 0;
  wrapper.setAttribute("role", "region");
  wrapper.setAttribute("aria-label", label);
  wrapper.classList.add("scroll-region");
  return wrapper;
}

const ACRONYMS = new Set(["asr", "tts", "ms", "id", "ai", "api", "pcm", "p50", "p95", "p99", "ttfa"]);

/** snake_case / kebab-case / dotted name -> "First audio ms"-style label. Known acronyms are upper-cased. */
export function humanise(name) {
  const words = String(name ?? "").trim().split(/[_\-.\s]+/).filter(Boolean);
  if (!words.length) return "Unknown";
  return words.map((w, i) => ACRONYMS.has(w.toLowerCase()) ? (w.toLowerCase() === "ms" ? "ms" : w.toUpperCase()) : i === 0 ? w.charAt(0).toUpperCase() + w.slice(1) : w).join(" ");
}

/** Small "Copy" button that copies `text`; stops propagation so it can sit inside clickable rows. */
export function copyButton(text, label = "Copy ID") {
  const b = node("button", "copy-id", "Copy");
  b.type = "button";
  b.setAttribute("aria-label", label);
  b.addEventListener("click", (event) => {
    event.stopPropagation();
    try { navigator.clipboard.writeText(text).then(() => { b.textContent = "Copied"; setTimeout(() => { b.textContent = "Copy"; }, 1200); }, () => {}); } catch { /* clipboard unavailable */ }
  });
  return b;
}

/** Short run ID (linked when `link`) with its full value on hover, plus a copy button. */
export function idCell(runId, { link = true } = {}) {
  const wrap = node("span", "id-cell");
  const code = link ? node("a", "id-short", shortId(runId)) : node("code", "id-short", shortId(runId));
  if (link) code.href = runLink(runId);
  code.title = runId;
  wrap.append(code, " ", copyButton(runId, "Copy ID " + runId));
  return wrap;
}

/** JSON kept behind a "Raw" disclosure. */
export function rawToggle(value, label = "Raw") {
  const d = node("details", "raw-toggle");
  d.append(node("summary", "", label), node("pre", "event-json", typeof value === "string" ? value : JSON.stringify(value, null, 2)));
  return d;
}

const REPORT_WORDS = { google: "Google", xai: "xAI", openai: "OpenAI", gpt: "GPT", grok: "Grok", gemini: "Gemini", nova: "Nova", sonic: "Sonic", live: "Live", voice: "Voice", aws: "AWS", amazon: "Amazon", mock: "Mock", asr: "ASR", tts: "TTS" };

/** "cap_google_gemini_live_2_5_flash_native_audio_20261018" -> "Google Gemini Live 2.5 flash native audio (18 Oct 2026)". Digit pairs become versions; a trailing YYYYMMDD becomes a date. */
export function humaniseReportId(id) {
  let parts = String(id ?? "").trim().replace(/^cap_/, "").split(/[_\-\s]+/).filter(Boolean);
  if (!parts.length) return "Unknown report";
  let date = "";
  const last = parts[parts.length - 1];
  if (/^20\d{6}$/.test(last)) {
    date = absoluteTime(last.slice(0, 4) + "-" + last.slice(4, 6) + "-" + last.slice(6, 8) + "T00:00:00Z").split(",")[0];
    parts = parts.slice(0, -1);
  }
  const out = [];
  for (let i = 0; i < parts.length; i++) {
    const w = parts[i];
    if (/^\d$/.test(w) && /^\d$/.test(parts[i + 1] ?? "")) { out.push(w + "." + parts[++i]); continue; }
    out.push(REPORT_WORDS[w.toLowerCase()] ?? w);
  }
  let text = out.join(" ");
  if (!REPORT_WORDS[(parts[0] ?? "").toLowerCase()]) text = text.charAt(0).toUpperCase() + text.slice(1);
  return date ? text + " (" + date + ")" : text;
}

/** <span class="report-id" title="raw id"> with the readable label. */
export function reportIdEl(id) {
  const s = node("span", "report-id", humaniseReportId(id));
  if (id) s.title = String(id);
  return s;
}
