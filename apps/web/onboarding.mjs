import { el } from "./runs.mjs?v=ux-final-20261023";

export const TOKEN_COMMAND = "make token";
export const TOKEN_FALLBACK = "docker compose exec -T api cat /data/operator-token";

const FIXES = {
  NO_ASR_BACKEND_CONFIGURED: "Set VOICE_HARNESS_LIVE_ASR=openai and OPENAI_API_KEY on the server. Run make serve-live on the host to load them from .env.",
  NO_CALLER_VOICE_SYNTH: "This server has no caller voice (a container has no macOS speech tools). Run make serve-live on a Mac, or set VOICE_HARNESS_CALLER_VOICE=openai_tts.",
  CALLER_VOICE_TTS_NOT_CONFIGURED: "openai_tts needs VOICE_HARNESS_LIVE_ASR=openai and OPENAI_API_KEY on the server.",
  CALLER_VOICE_UNKNOWN_BACKEND: "VOICE_HARNESS_CALLER_VOICE must be macos or openai_tts.",
  NO_AWS_SESSION: "Start an AWS session on the server host: ./scripts/aws-session.sh <MFA code>.",
  NO_XAI_API_KEY: "Set xAI_API_KEY in the server's .env, then restart the server.",
  NO_GOOGLE_ADC: "Sign in on the server host: gcloud auth application-default login.",
  NO_GOOGLE_PROJECT: "Set GOOGLE_CLOUD_PROJECT on the server, or use ADC that carries a quota project.",
};

const fixFor = (code) => FIXES[code] ?? "Unavailable: " + code;

/** Pure: what every live provider is waiting on, then what each still needs on top of that.
 * `shared` holds reason codes missing from every provider; they are shown once, not four times. */
export function readinessItems(payload) {
  const rows = (payload?.items ?? []).map((p) => ({
    id: p.provider_config_id,
    ready: Boolean(p.available),
    codes: p.available ? [] : (Array.isArray(p.reasons) && p.reasons.length ? p.reasons : [p.reason]),
  }));
  const waiting = rows.filter((r) => !r.ready);
  const shared = waiting.length === rows.length && rows.length
    ? waiting[0].codes.filter((code) => waiting.every((r) => r.codes.includes(code))) : [];
  return {
    shared: shared.map((code) => ({ code, fix: fixFor(code) })),
    providers: rows.map((r) => ({ id: r.id, ready: r.ready,
      fixes: r.codes.filter((code) => !shared.includes(code)).map(fixFor) })),
  };
}

/** Pure: a plain-language message for a failed request; the raw code is kept so nothing is hidden. */
export function friendlyError(error) {
  const code = String(error?.message ?? error ?? "");
  if (code === "AUTH_REQUIRED" || code === "HTTP 401") {
    return "That token was not accepted. Copy it again with " + TOKEN_COMMAND + ". It changes only if the server's data volume is deleted.";
  }
  if (code === "HTTP 403" || code === "FORBIDDEN") return "That token is valid but its role is not allowed to do this. Use the operator token.";
  if (/failed to fetch|networkerror|load failed/i.test(code)) {
    return "Could not reach the server. Check it is running (make up) and that you opened this page from its address.";
  }
  const actions = {
    REVIEW_CLAIMED_BY_OTHER: "This finding is claimed by another reviewer. Refresh the queue to see its current owner.",
    INVALID_REVIEW_TRANSITION: "This review changed since it was loaded. Reopen the finding before continuing.",
    SCENARIO_VERSION_CONFLICT: "This scenario version already exists with different content. Use a new version.",
    RUN_NOT_FINISHED: "Wait for the run to finish before assessing it.",
    LIVE_JUDGE_DISABLED: "This judge is not enabled on the server. Choose deterministic checks or configure the judge first.",
    CONCURRENCY_LIMIT: "The server has reached its concurrent-run limit. Wait for active runs to finish, then retry.",
  };
  return actions[code] ? actions[code] + " (" + code + ")" : code;
}

function step(parts) {
  const li = el("li");
  for (const part of parts) li.append(typeof part === "string" ? document.createTextNode(part) : part);
  return li;
}

/** The first-run guide. `open` expands it until the user has connected once. */
export function renderGetStarted(root, open) {
  const details = el("details", "panel help");
  details.open = Boolean(open);
  details.append(el("summary", "", "Get started: what the operator token is and how to get it"));
  details.append(el("p", "", "The operator token is this harness's own password for its API. It is not an OpenAI, xAI, AWS or Google key, and provider keys never reach this page."));
  const list = el("ol");
  list.append(
    step(["In a terminal in the project folder, start the harness: ", el("code", "", "make up"), " for scripted fixtures, or ", el("code", "", "make serve-live"), " on a Mac for live providers."]),
    step(["Copy the token to your clipboard without printing it: ", el("code", "", TOKEN_COMMAND), " (", el("code", "", "make token-host"), " for the live server)."]),
    step(["Paste it into the token box above and choose Connect. It is kept for this browser tab only."]),
  );
  details.append(list);
  details.append(el("p", "muted", "No clipboard tool? Run: " + TOKEN_FALLBACK + " and copy the output. Scripted fixtures need no provider setup. Live providers need keys or sessions on the server, and the checklist below shows what is missing once you connect."));
  root.replaceChildren(details);
}

/** Readiness checklist for live providers; text only. */
export function renderReadiness(root, payload) {
  const { shared, providers } = readinessItems(payload);
  const parts = [];
  if (shared.length) {
    const box = el("div", "shared");
    box.append(el("strong", "", "This server needs first (applies to every provider)"));
    const ul = el("ul");
    for (const s of shared) ul.append(el("li", "", s.fix));
    box.append(ul);
    parts.push(box);
  }
  const list = el("ul", "checklist");
  for (const p of providers) {
    const li = el("li", p.ready ? "ready" : "missing");
    li.append(el("strong", "", (p.ready ? "Ready · " : "Needs setup · ") + p.id));
    for (const fix of p.fixes) li.append(el("span", "muted", fix));
    if (!p.ready && !p.fixes.length) li.append(el("span", "muted", "Nothing further beyond the server requirements above."));
    list.append(li);
  }
  parts.push(providers.length ? list : el("p", "muted", "No live providers are registered on this server."));
  root.replaceChildren(...parts);
}
