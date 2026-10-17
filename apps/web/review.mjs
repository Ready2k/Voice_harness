// Hold review view (HD-04): unresolved holds and disputes. Pure view-model + DOM render.
import { api, el } from "./runs.mjs";

export const NOTICE_WINDOW_SECONDS = 7 * 24 * 3600; // owner is notified seven days before expiry (spec section 10)

export const URGENCY_LABEL = {
  expired: "Expired, awaiting purge grace",
  expiring: "Expires within 7 days",
  active: "Active",
};

/** Pure: one API hold -> display model. A finding-scoped hold is a dispute. */
export function describeHold(h, now) {
  const remaining = h.expires_at - now;
  const urgency = h.released_at != null ? "released" : remaining <= 0 ? "expired" : remaining <= NOTICE_WINDOW_SECONDS ? "expiring" : "active";
  return {
    holdId: h.hold_id,
    runId: h.run_id,
    owner: h.owner_id,
    requester: h.requester_id,
    isDispute: h.scope_type === "findings",
    findings: h.finding_ids ?? [],
    access: h.access_classification,
    urgency,
    urgencyLabel: URGENCY_LABEL[urgency] ?? urgency,
    daysLeft: urgency === "released" ? null : Math.floor(remaining / 86400),
    expiresAt: h.expires_at,
    auditEventId: h.audit_event_id,
  };
}

/** Pure: API payload -> review model. Most urgent first; unresolved only. */
export function view(payload) {
  const now = payload.server_time;
  const unresolved = payload.items
    .filter((h) => h.released_at == null)
    .map((h) => describeHold(h, now))
    .sort((a, b) => a.expiresAt - b.expiresAt || a.holdId.localeCompare(b.holdId));
  return {
    disputes: unresolved.filter((h) => h.isDispute),
    runWide: unresolved.filter((h) => !h.isDispute),
    needsAttention: unresolved.filter((h) => h.urgency !== "active").length,
    total: unresolved.length,
  };
}

function holdTable(rows) {
  const t = el("table");
  const head = el("tr");
  for (const h of ["Hold", "Run", "Owner", "Findings", "Status", "Days left"]) head.append(el("th", "", h));
  t.append(head);
  for (const h of rows) {
    const tr = el("tr");
    tr.append(el("td", "wrap", h.holdId));
    const run = el("td");
    const link = el("a", "", h.runId);
    link.href = "./runs.html?run=" + encodeURIComponent(h.runId);
    run.append(link);
    run.className = "wrap";
    tr.append(run, el("td", "", h.owner), el("td", "wrap", h.findings.length ? h.findings.join(", ") : "run-wide"));
    tr.append(el("td", "flag-" + h.urgency, h.urgencyLabel), el("td", "", h.daysLeft));
    t.append(tr);
  }
  return t;
}

export function render(root, model) {
  root.replaceChildren();
  root.append(el("p", "", model.total + " unresolved hold" + (model.total === 1 ? "" : "s") + "; " + model.needsAttention + " expiring or expired."));
  for (const [title, rows, empty] of [
    ["Unresolved disputes (finding-scoped holds)", model.disputes, "No unresolved disputes."],
    ["Run-wide holds", model.runWide, "No run-wide holds."],
  ]) {
    const panel = el("section", "panel");
    panel.append(el("h2", "", title), rows.length ? holdTable(rows) : el("p", "muted", empty));
    root.append(panel);
  }
}

export async function load(token) {
  return view(await api(token, "/api/v1/preservation-holds?state=active"));
}
