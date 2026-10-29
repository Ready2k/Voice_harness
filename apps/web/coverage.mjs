// Provider coverage panel (AC-20). Pure view-model + DOM rendering; no ranking, no scores.
import { providerName } from "./providers.mjs?v=ux-final-20261025";
import { copyButton, reportIdEl, scrollRegion } from "./format.mjs?v=ux-final-20261025";
export const STATE_LABEL = {
  implemented_and_verified: "Implemented and verified",
  implemented_but_unverified: "Implemented, not verified",
  unavailable: "Unavailable",
  not_implemented: "Not implemented",
};

// Provider family (coverage API) -> live provider configuration ID (/api/v1/live-providers).
export const FAMILY_CONFIG = {
  openai: "openai-realtime-live",
  openai_live: "openai-live",
  aws_nova_sonic: "nova-sonic-live",
  xai_grok_voice: "xai-voice-live",
  google_gemini_live: "gemini-live",
  elevenlabs_agents: "elevenlabs-live",
};

// Capability reports last 7 days (capability_report.DEFAULT_EXPIRY); the API exposes checked_at, not expires_at.
export const REPORT_LIFETIME_DAYS = 7;
export const EXPIRY_WARN_DAYS = 2;
const DAY = 86400000;

export const REVERIFY_GUIDANCE = "Re-verifying is run from a terminal on the server host, not from this page: it runs the provider probe (scripts/probe), then builds the capability report from its observations. Reload this page afterwards.";
export const REVERIFY_COST_WARNING = "Cost: the probe makes billed provider API calls. Run it only when you intend to spend.";

const BUILD = "PYTHONPATH=src .venv/bin/python -m voice_harness.probe ";
/** Pure: the real probe invocation for a provider family (from scripts/probe headers and the Makefile), or null.
 *  { needs: what must be set up first, probe: command, build: command (replace the <> placeholders) }. */
export function reverifyPlan(family) {
  const plans = {
    aws_nova_sonic: { needs: "An unexpired AWS session: ./scripts/aws-session.sh <MFA_CODE> writes .local/aws-session.env.",
      probe: "set -a; . .local/aws-session.env; set +a; node scripts/probe/nova_probe.mjs --region us-east-1 --model amazon.nova-2-5-sonic",
      build: BUILD + "from-nova-observations artifacts/phase0/nova-observations-<timestamp>.json --report-id <report-id>" },
    openai: { needs: "OPENAI_API_KEY in the repo-root .env file.",
      probe: "cd scripts/probe && node --env-file=../../.env openai_probe.mjs --model gpt-realtime-2.1",
      build: BUILD + "from-openai-observations artifacts/phase0/openai-observations-<timestamp>.json --report-id <report-id>" },
    xai_grok_voice: { needs: "xAI_API_KEY set in the environment.",
      probe: "PYTHONPATH=src .venv/bin/python scripts/probe/xai_probe.py --model grok-voice-3.0",
      build: BUILD + "from-xai-observations artifacts/phase0/xai-observations-<timestamp>.json --report-id <report-id>" },
    google_gemini_live: { needs: "Google Application Default Credentials (for example gcloud auth application-default login).",
      probe: "PYTHONPATH=src .venv/bin/python scripts/probe/google_gemini_probe.py --region us-central1",
      build: BUILD + "from-google-observations artifacts/phase0/google_gemini-observations-<timestamp>.json --report-id <report-id>" },
    openai_live: { needs: "OPENAI_API_KEY set in the environment.",
      probe: "PYTHONPATH=src .venv/bin/python scripts/probe/openai_live_probe.py",
      build: BUILD + "from-openai-live-observations artifacts/phase0/openai_live-observations-<timestamp>.json --report-id <report-id>" },
    elevenlabs_agents: { needs: "ELEVENLABS_API_KEY set in the environment and a stored agent (python -m voice_harness.adapters.elevenlabs_agent setup).",
      probe: "PYTHONPATH=src .venv/bin/python scripts/probe/elevenlabs_probe.py",
      build: BUILD + "from-elevenlabs-observations artifacts/phase0/elevenlabs-observations-<timestamp>.json --report-id <report-id>" },
  };
  return plans[family] ?? null;
}

/** Pure: a row offers the Re-verify action when it has a report that is near expiry or expired (not the server-skew case). */
export function needsReverify(item, now = Date.now()) {
  const x = expiryOf(item.checkedAt, now, item.expiresAt);
  return x.state === "soon" || (x.state === "expired" && !item.counted);
}

function commandBlock(label, command) {
  const row = el("div", "reverify-cmd");
  const code = el("code", "", command);
  row.append(el("span", "muted", label), code, copyButton(command, "Copy " + label.toLowerCase() + " command"));
  return row;
}

export function reverifyDisclosure(family) {
  const det = el("details", "reverify");
  det.append(el("summary", "", "How to re-verify"));
  const plan = reverifyPlan(family);
  det.append(el("p", "reverify-cost", REVERIFY_COST_WARNING), el("p", "muted", REVERIFY_GUIDANCE));
  if (plan) {
    det.append(el("p", "muted", "Needs: " + plan.needs), commandBlock("1. Probe", plan.probe), commandBlock("2. Build report", plan.build));
  } else det.append(el("p", "muted", "No probe exists for this family."));
  return det;
}

/** Pure: when a report expires and how close that is. `now` is injectable. state: none | ok | soon | expired.
 *  Prefers the server's expires_at; falls back to checked_at + 7 days (`source` says which). */
export function expiryOf(checkedAt, now = Date.now(), expiresAt = null) {
  const t = Date.parse(checkedAt ?? "");
  const given = Date.parse(expiresAt ?? "");
  if (Number.isNaN(t) && Number.isNaN(given)) return { state: "none", label: "No report", at: null, msLeft: null };
  const source = Number.isNaN(given) ? "checked_at" : "expires_at";
  const at = Number.isNaN(given) ? t + REPORT_LIFETIME_DAYS * DAY : given;
  const msLeft = at - now;
  const abs = Math.abs(msLeft);
  const days = Math.floor(abs / DAY);
  const hours = Math.max(1, Math.floor(abs / 3600000));
  const span = days >= 1 ? days + (days === 1 ? " day" : " days") : hours + (hours === 1 ? " hour" : " hours");
  if (msLeft <= 0) return { state: "expired", label: "expired " + span + " ago", at: new Date(at).toISOString(), msLeft, source };
  return { state: msLeft <= EXPIRY_WARN_DAYS * DAY ? "soon" : "ok", label: "expires in " + span, at: new Date(at).toISOString(), msLeft, source };
}

export const REASON_TEXT = {
  NO_ADAPTER: "No adapter has been built for this family.",
  REPORT_WITHOUT_ADAPTER: "A capability report exists but there is no adapter.",
  NO_CAPABILITY_REPORT: "No capability report has been produced.",
  REPORT_EXPIRED: "The capability report has expired (reports last 7 days).",
  REPORT_FAILED_CLOSED: "A required primitive is not verified or observed.",
  ADAPTER_RUNTIME_UNAVAILABLE: "The adapter cannot run in this environment (its runtime dependencies are missing), so it cannot be verified here.",
  AC09_NOT_PASSED: "The AC-09 adapter conformance run has not passed for this report.",
};

/** Pure: one API item -> display model. Only implemented_and_verified counts as support. */
export function describe(item) {
  return {
    family: item.provider_family,
    title: providerName(item.provider_family),
    configId: FAMILY_CONFIG[item.provider_family] ?? null,
    surface: item.api_surface ?? null,
    evidenceIds: item.evidence_ids ?? [],
    state: item.coverage_state,
    stateLabel: STATE_LABEL[item.coverage_state] ?? item.coverage_state,
    counted: item.coverage_state === "implemented_and_verified",
    model: item.model_id ?? "none",
    region: item.region ?? null,
    checkedAt: item.checked_at ?? null,
    expiresAt: item.expires_at ?? null,
    reportId: item.capability_report_id ?? null,
    reasons: (item.reason_codes ?? []).map((c) => REASON_TEXT[c] ?? c),
    evidenceCount: (item.evidence_ids ?? []).length,
  };
}

/** Pure: API summary -> view model. Items keep the server's fixed order; never sorted by status. */
export function view(summary) {
  return {
    statement: summary.statement,
    verified: summary.verified_count,
    requested: summary.requested_count,
    items: summary.items.map(describe),
  };
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text; // textContent only: API data is never parsed as HTML
  return node;
}

/** Pure: readiness for one row from onboarding.readinessItems() output. null input = not checked. */
export function readinessFor(configId, readiness) {
  if (!readiness) return { status: "unknown", fixes: [] };
  const p = readiness.providers.find((x) => x.id === configId);
  if (!p) return { status: "none", fixes: [] };
  return { status: p.ready ? "ready" : "setup", fixes: p.fixes };
}

/** Pure: summary figures for the metric tiles. */
export function summaryOf(model, readiness, now = Date.now()) {
  const ready = model.items.filter((i) => readinessFor(i.configId, readiness).status === "ready").length;
  const expiring = model.items.filter((i) => i.counted && expiryOf(i.checkedAt, now, i.expiresAt).state === "soon").length;
  return { verified: model.verified, requested: model.requested, ready, checked: Boolean(readiness), expiring };
}

function tile(value, label, cls, title) {
  const t = el("div", "tile " + cls);
  t.append(el("strong", "", value), el("span", "", label));
  if (title) t.title = title;
  return t;
}

export function renderSummary(root, model, readiness, now = Date.now()) {
  const s = summaryOf(model, readiness, now);
  const tiles = el("div", "tally coverage-tally");
  tiles.append(
    tile(s.verified + " of " + s.requested, "families verified", s.verified === s.requested ? "tile-released" : "tile-pending", model.statement),
    tile(s.checked ? s.ready + " of " + s.requested : "Not checked", "ready on this server", s.checked && s.ready ? "tile-released" : "tile-pending"),
    tile(String(s.expiring), "reports expiring soon", s.expiring ? "tile-pending" : "tile-released", "Verified reports within " + EXPIRY_WARN_DAYS + " days of expiry"),
  );
  root.replaceChildren(tiles);
}

function expiryCell(c, now) {
  const td = el("td", "nowrap");
  const x = expiryOf(c.checkedAt, now, c.expiresAt);
  if (x.state === "none") { td.append(el("span", "muted", "No report")); return td; }
  if (c.counted && x.state === "expired") {
    // The server says verified (its clock is authoritative); never show "Expired" beside it.
    const note = el("span", "expiry expiry-skew", "Verified per server");
    note.title = "Report " + c.reportId + " checked " + c.checkedAt + "; expiry " + x.at + " (" + x.source + ")";
    td.append(note, el("div", "muted", "Expiry computed from checked time; your clock may differ from the server's."));
    return td;
  }
  const span = el("span", "expiry expiry-" + x.state, (x.state === "soon" ? "⚠ " : "") + x.label.charAt(0).toUpperCase() + x.label.slice(1));
  span.title = "Report " + c.reportId + " checked " + c.checkedAt + "; expires " + x.at;
  td.append(span);
  if (needsReverify(c, now)) {
    const det = reverifyDisclosure(c.family);
    const btn = el("button", "reverify-btn", "Re-verify");
    btn.type = "button";
    btn.setAttribute("aria-label", "Re-verify " + c.title + ": show the probe commands");
    btn.addEventListener("click", () => { det.open = !det.open; if (det.open) det.scrollIntoView?.({ block: "nearest" }); });
    td.append(btn, det);
  }
  return td;
}

export function render(root, model, readiness = null, now = Date.now(), fetchProbes = null) {
  root.replaceChildren();
  const wrap = scrollRegion(el("div", "panel coverage-table"), "Provider coverage table");
  const table = el("table");
  const head = el("tr");
  for (const h of ["Provider", "Model · region", "Capability verified", "Ready on this server", "Report expires"]) head.append(el("th", "", h));
  const thead = el("thead"); thead.append(head); table.append(thead);
  const body = el("tbody");
  for (const c of model.items) {
    const tr = el("tr", "state-" + c.state);
    const name = el("td"); const nm = el("strong", "", c.title); nm.title = c.family; name.append(nm);
    tr.append(name);
    const mr = el("td"); const modelLine = el("div", "", c.model === "none" ? "none" : providerName(c.model));
    if (c.model !== "none") modelLine.title = c.model;
    mr.append(modelLine);
    if (c.region) mr.append(el("div", "muted", c.region));
    tr.append(mr);
    const cap = el("td"); cap.append(el("span", "badge " + (c.counted ? "ok" : "warn"), (c.counted ? "✓ " : "") + c.stateLabel));
    if (c.counted && c.checkedAt) { const rl = el("div", "muted", "Report "); rl.append(reportIdEl(c.reportId)); cap.append(rl); }
    if (c.counted) {
      const btn = el("button", "linklike", "View " + c.evidenceCount + " probe events");
      btn.type = "button";
      btn.addEventListener("click", () => showProbeEvents(c, fetchProbes));
      cap.append(btn);
    }
    if (c.reasons.length) { const ul = el("ul", "reasons"); for (const r of c.reasons) ul.append(el("li", "", r)); cap.append(ul); }
    tr.append(cap);
    const rd = readinessFor(c.configId, readiness);
    const rtd = el("td");
    const label = { ready: "Ready", setup: "Needs setup", none: "Not available here", unknown: "Not checked" }[rd.status];
    rtd.append(el("span", "badge " + (rd.status === "ready" ? "ok" : "warn"), (rd.status === "ready" ? "✓ " : "") + label));
    for (const f of rd.fixes) rtd.append(el("div", "muted", f));
    tr.append(rtd, expiryCell(c, now));
    body.append(tr);
  }
  table.append(body); wrap.append(table); root.append(wrap);
}

export async function load(baseUrl, token) {
  const response = await fetch(baseUrl + "/api/v1/providers", {
    headers: { Authorization: "Bearer " + token },
  });
  if (!response.ok) throw new Error("HTTP " + response.status);
  return view(await response.json());
}

/** Pure: one stored probe event -> display row. The probe records no pass/fail outcome, only direction and any error. */
export function probeRow(entry) {
  const e = entry.event ?? {};
  const d = e.detail && typeof e.detail === "object" ? e.detail : {};
  const failed = /error|fail/i.test(String(e.type ?? "")) || d.error !== undefined;
  const dir = e.dir === "out" ? "sent to provider" : e.dir === "in" ? "received from provider" : "recorded";
  return { id: e.id ?? "", type: e.type ?? "", time: e.t ?? "", outcome: failed ? "error reported" : dir, failed, source: entry.source ?? "", raw: JSON.stringify(e, null, 2) };
}

export async function loadProbeEvents(baseUrl, token, family) {
  const response = await fetch(baseUrl + "/api/v1/providers/" + encodeURIComponent(family) + "/probe-events", { headers: { Authorization: "Bearer " + token } });
  if (!response.ok) throw new Error("HTTP " + response.status);
  return response.json();
}

/** In-page dialog listing the probe events a verified report cites; raw JSON behind a toggle. */
export async function showProbeEvents(c, fetchProbes) {
  const dlg = el("dialog", "probe-dialog");
  dlg.setAttribute("aria-label", "Probe events for " + c.title);
  const close = el("button", "", "Close"); close.type = "button";
  close.addEventListener("click", () => dlg.close());
  dlg.addEventListener("close", () => dlg.remove());
  const body = el("div");
  dlg.append(el("h2", "", c.title + " probe events"), close, body);
  document.body.append(dlg);
  dlg.showModal();
  body.append(el("p", "muted", "Loading…"));
  try {
    if (!fetchProbes) throw new Error("not available");
    const data = await fetchProbes(c.family);
    const rows = data.events.map(probeRow);
    const raw = el("label", "raw-toggle"); const box = el("input"); box.type = "checkbox"; raw.append(box, document.createTextNode(" Raw"));
    const intro = el("p", "muted", "Report " + data.capability_report_id + " cites " + data.cited_count + " events. Recorded by the capability probe (read from the stored probe logs, no provider call). Event IDs restart each probe run, so an ID may match an event in more than one run." +
      (data.missing_ids.length ? " Not found in the stored logs: " + data.missing_ids.join(", ") + "." : ""));
    const table = el("table"); const head = el("tr");
    for (const h of ["Event", "Type", "Direction / outcome", "Timestamp", "Probe run"]) head.append(el("th", "", h));
    const th = el("thead"); th.append(head); table.append(th);
    const tb = el("tbody");
    for (const r of rows) {
      const tr = el("tr");
      for (const v of [r.id, r.type, r.outcome, r.time, r.source]) tr.append(el("td", "mono", v));
      tb.append(tr);
      const rawRow = el("tr", "raw-row"); rawRow.hidden = true;
      const td = el("td"); td.colSpan = 5; td.append(el("pre", "", r.raw.length > 4000 ? r.raw.slice(0, 4000) + "\n… truncated" : r.raw));
      rawRow.append(td); tb.append(rawRow);
    }
    box.addEventListener("change", () => { for (const r of tb.querySelectorAll(".raw-row")) r.hidden = !box.checked; });
    table.append(tb);
    const scroll = scrollRegion(el("div", "probe-scroll"), "Probe results table"); scroll.append(table);
    body.replaceChildren(intro, raw, scroll);
  } catch (e) {
    body.replaceChildren(el("p", "", "Could not load probe events: " + e.message));
  }
}
