// Pure view-models for the Review page: queue context, evidence panels, hold countdowns, remembered reviewer.
import { transcript } from "./runs.mjs?v=ux-final-20261025";
import { humanise, shortId } from "./format.mjs?v=ux-final-20261025";

export const SEVERITIES = ["critical", "high", "medium", "low"];
const RANK = Object.fromEntries(SEVERITIES.map((s, i) => [s, i]));
export const REVIEWER_KEY = "harness-reviewer-id";

/** Remembered reviewer ID (localStorage, then sessionStorage); storage may be blocked. */
export function rememberedReviewer() {
  for (const kind of ["localStorage", "sessionStorage"]) {
    try { const v = globalThis[kind]?.getItem(REVIEWER_KEY); if (v) return v; } catch { /* blocked */ }
  }
  return "";
}
export function rememberReviewer(value) {
  const v = String(value ?? "").trim();
  if (!v) return;
  for (const kind of ["localStorage", "sessionStorage"]) {
    try { globalThis[kind]?.setItem(REVIEWER_KEY, v); return; } catch { /* try next */ }
  }
}

/** Queue rows joined with run context. `runs` is the /api/v1/runs items array (may be missing the run). */
export function enrichQueue(items, runs, now = Date.now()) {
  const byRun = new Map((runs ?? []).map((r) => [r.run_id, r]));
  return items.map((r) => {
    const run = byRun.get(r.run_id);
    const created = r.created_at != null ? new Date(r.created_at * 1000).toISOString() : null;
    return {
      reviewId: r.review_id, runId: r.run_id, criterionId: r.criterion_id, reason: r.reason, state: r.state,
      severity: SEVERITIES.includes(r.severity) ? r.severity : "medium",
      scenario: run?.scenario_id ?? null, provider: run?.provider_config_id ?? null,
      createdIso: created, createdAt: r.created_at ?? 0, assignee: r.claimed_by || null,
      ageSeconds: r.created_at != null ? Math.max(0, Math.round(now / 1000 - r.created_at)) : null,
    };
  });
}

/** Severity filter ("all" keeps everything); most severe first, then oldest first. */
export function filterAndSort(rows, severity = "all") {
  return rows
    .filter((r) => severity === "all" || r.severity === severity)
    .sort((a, b) => (RANK[a.severity] ?? 9) - (RANK[b.severity] ?? 9) || a.createdAt - b.createdAt || a.reviewId.localeCompare(b.reviewId));
}

export function severityCounts(rows) {
  const counts = { all: rows.length };
  for (const s of SEVERITIES) counts[s] = rows.filter((r) => r.severity === s).length;
  return counts;
}

const text = (v) => (v == null ? "" : String(v));

/** One-line plain-language summary of a ledger event cited as evidence. */
export function describeEvent(e) {
  const c = e?.content ?? {};
  switch (e?.type) {
    case "tool.requested": {
      const args = Object.entries(c.arguments ?? {}).map(([k, v]) => k + "=" + text(v)).join(", ");
      return "Requested tool " + text(c.tool) + (args ? " (" + args + ")" : "");
    }
    case "tool.denied": return "Tool call " + text(c.call_id) + " denied: " + text(c.error_code);
    case "tool.executed": case "tool.result": return "Tool " + text(c.call_id ?? c.tool) + " executed";
    case "decision": return "Policy " + text(c.action) + (c.reason_code ? ": " + c.reason_code : "") + (c.target ? " (" + c.target + ")" : "");
    case "audio.output": return "Assistant said: " + text(c.transcript);
    case "simulator.utterance_generated": return "Caller said: " + text(c.output_text);
    case "caller.utterance": return "Caller said: " + text(c.text);
    default: {
      const extra = c.reason_code ?? c.error_code ?? (Array.isArray(c.reason_codes) ? c.reason_codes.join(", ") : "");
      return humanise(e?.type) + (extra ? ": " + extra : "");
    }
  }
}

/** The adjudication entry for this finding, or null. */
export function adjudicationEntry(review, events) {
  const adj = events.find((e) => e.event_id === review.adjudication_event_id) ?? events.find((e) => e.type === "judge.adjudicated" && e.content?.evaluation_id === review.evaluation_id);
  return adj?.content?.criteria?.find((c) => c.criterion_id === review.criterion_id) ?? null;
}

/** Transcript lines near the cited events (+- `window` ledger positions); falls back to the first lines. */
export function transcriptExcerpt(events, citedIds, window = 6, max = 8) {
  const lines = transcript(events);
  const cited = events.filter((e) => citedIds.includes(e.event_id)).map((e) => e.sequence);
  if (!lines.length) return [];
  if (!cited.length) return lines.slice(0, max);
  const lo = Math.min(...cited) - window, hi = Math.max(...cited) + window;
  const near = lines.filter((l) => l.sequence >= lo && l.sequence <= hi);
  return (near.length ? near : lines).slice(0, max);
}

/** Side-by-side evidence: what was expected, what the check observed (cited events), and the transcript around them. */
export function evidenceModel(review, events) {
  const crit = review.criterion ?? {};
  const entry = adjudicationEntry(review, events);
  const citedIds = entry?.evidence_event_ids ?? [];
  const byId = new Map(events.map((e) => [e.event_id, e]));
  const cited = citedIds.map((id) => byId.get(id)).filter(Boolean);
  const verdict = entry?.verdict ?? null;
  return {
    expected: { behaviour: crit.expected_behaviour ?? null, prohibited: crit.prohibited_behaviour ?? null, passAnchor: crit.anchors?.pass ?? null, failAnchor: crit.anchors?.fail ?? null },
    observed: {
      verdict, source: entry?.verdict_source ?? null, reasonCodes: entry?.reason_codes ?? [],
      cited: cited.map((e) => ({ eventId: e.event_id, sequence: e.sequence, type: e.type, summary: describeEvent(e) })),
      missing: citedIds.filter((id) => !byId.has(id)),
    },
    transcript: transcriptExcerpt(events, citedIds).map((l) => ({ sequence: l.sequence, speaker: l.speaker, text: l.text, cited: cited.some((e) => Math.abs(e.sequence - l.sequence) <= 1) })),
    citedIds,
  };
}

/** Validate the whole decision locally BEFORE any claim is recorded (claim+decide flow). Throws on missing integrity inputs. */
export function assertCitedEventsBelong(evidenceIds, events) {
  const valid = new Set(events.map((e) => e.event_id));
  if (evidenceIds.some((id) => !valid.has(id))) throw new Error("A cited event does not belong to this run. Inspect the evidence and use its event IDs.");
}

function duration(secs) {
  const s = Math.abs(Math.round(secs));
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d >= 1 ? d + "d " + h + "h" : h >= 1 ? h + "h " + m + "m" : Math.max(1, m) + "m";
}

/** Countdown label and tone for a hold: "6d 4h left" / "Expired 2h 5m ago". Tone: ok, warn (<7 days), urgent (<24 hours), bad (expired). */
export function countdown(expiresAt, now) {
  const left = expiresAt - now;
  if (left <= 0) return { label: "Expired " + duration(left) + " ago", tone: "bad", seconds: left };
  return { label: duration(left) + " left", tone: left < 86400 ? "urgent" : left <= 7 * 86400 ? "warn" : "ok", seconds: left };
}

/** Holds register payload -> table rows, soonest expiry first. `findings` maps review_id -> {criterionId}. */
export function holdTable(payload, findings = new Map(), runs = [], now = payload.server_time) {
  const byRun = new Map(runs.map((r) => [r.run_id, r]));
  return payload.items
    .filter((h) => h.released_at == null)
    .map((h) => {
      const run = byRun.get(h.run_id);
      const ids = h.finding_ids ?? [];
      return {
        holdId: h.hold_id, runId: h.run_id, owner: h.owner_id, expiresAt: h.expires_at, scenario: run?.scenario_id ?? h.scenario_id ?? null, provider: run?.provider_config_id ?? h.provider_config_id ?? null,
        scope: ids.length ? "Finding" : "Whole run",
        scopeDetail: ids.length ? ids.map((id) => findings.get(id)?.criterionId ?? shortId(id)).join(", ") : "All run evidence",
        findingIds: ids, countdown: countdown(h.expires_at, now),
      };
    })
    .sort((a, b) => a.expiresAt - b.expiresAt || a.holdId.localeCompare(b.holdId));
}

/** Keeps what a reviewer typed per finding so a reload (after a failed decision) can restore it. Nothing here weakens validation: restored values are re-checked on submit. */
export function createDraftStore() {
  const drafts = new Map();
  return {
    save(id, draft) { drafts.set(id, { ...draft }); },
    get(id) { const d = drafts.get(id); return d ? { ...d } : null; },
    clear(id) { drafts.delete(id); },
  };
}

/** Friendly text when a retry reuses an idempotency key with different content; null for other errors. */
export function holdErrorMessage(error) {
  return /IDEMPOTENCY_CONFLICT/.test(String(error?.message ?? error ?? ""))
    ? "This hold was already submitted with different details, so the earlier attempt may have been saved. Check the active holds list above, then submit again to create it with the current details."
    : null;
}
