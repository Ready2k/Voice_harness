// Pure view-model helpers for the Scenarios page: families, filters, latest results and the structured summary.
import { CRITERION_HELP, runResult } from "./runs.mjs?v=ux-final-20261025";
import { humanise } from "./format.mjs?v=ux-final-20261025";

export const AUDIO_LABEL = { clean_studio: "Clean audio", street_noise_10db: "Street noise", telephone_narrowband: "Telephone line" };
export const AUDIO_ORDER = Object.keys(AUDIO_LABEL);
const UNGROUPED = "Other scenarios";

export function audioLabel(profile) { return AUDIO_LABEL[profile] ?? humanise(profile); }
export function criterionHelp(id) { return CRITERION_HELP[id] ?? "No description recorded for this criterion."; }

/** Title with the locale and audio-profile parts removed, so locale x audio variants share one family. */
export function familyTitle(s) {
  const title = s.presentation?.title ?? s.scenario_id;
  const drop = new Set([s.definition?.locale, audioLabel(s.definition?.audio_profile ?? "").toLowerCase(), audioLabel(s.definition?.audio_profile ?? "")]);
  const kept = title.split(" · ").filter((p) => !drop.has(p) && !drop.has(p.toLowerCase()));
  return kept.join(" · ") || title;
}

/** Pure: scenarios -> [{group, order, families:[{key, title, outcome, variants}]}], variants ordered locale then audio. */
export function buildGroups(items) {
  const groups = new Map();
  for (const s of items) {
    const group = s.presentation?.group ?? UNGROUPED;
    const order = s.presentation?.group_order ?? 99;
    if (!groups.has(group)) groups.set(group, { group, order, families: new Map() });
    const key = s.presentation ? group + "|" + familyTitle(s) : "id|" + s.scenario_id;
    const families = groups.get(group).families;
    if (!families.has(key)) families.set(key, { key, title: s.presentation ? familyTitle(s) : s.scenario_id, outcome: s.presentation?.expected_outcome ?? "", variants: [] });
    families.get(key).variants.push(s);
  }
  const rank = (s) => [s.definition.locale === "en-GB" ? 0 : 1, s.definition.locale, AUDIO_ORDER.indexOf(s.definition.audio_profile) < 0 ? 9 : AUDIO_ORDER.indexOf(s.definition.audio_profile)];
  return [...groups.values()].sort((a, b) => a.order - b.order || a.group.localeCompare(b.group)).map((g) => ({
    group: g.group, order: g.order,
    families: [...g.families.values()].map((f) => ({ ...f, variants: f.variants.sort((x, y) => { const [a, b] = [rank(x), rank(y)]; return a[0] - b[0] || a[1].localeCompare(b[1]) || a[2] - b[2]; }) }))
      .sort((a, b) => a.title.localeCompare(b.title)),
  }));
}

/** Pure: locale x audio matrix for a family; cells are variants or null. */
export function matrix(variants) {
  const locales = [...new Set(variants.map((v) => v.definition.locale))];
  const audios = AUDIO_ORDER.filter((a) => variants.some((v) => v.definition.audio_profile === a));
  for (const v of variants) if (!audios.includes(v.definition.audio_profile)) audios.push(v.definition.audio_profile);
  const rows = locales.map((locale) => ({ locale, cells: audios.map((audio) => variants.find((v) => v.definition.locale === locale && v.definition.audio_profile === audio) ?? null) }));
  return { audios, rows, isMatrix: variants.length > 1 };
}

export function modeOf(s) { return !s.runnable ? "stored" : s.execution; }

/** Pure: does a scenario pass the active filters? Empty/"all" filters match everything. */
export function matches(s, f) {
  if (f.mode && f.mode !== "all" && modeOf(s) !== f.mode) return false;
  if (f.group && f.group !== "all" && (s.presentation?.group ?? UNGROUPED) !== f.group) return false;
  if (f.locale && f.locale !== "all" && s.definition.locale !== f.locale) return false;
  if (f.audio && f.audio !== "all" && s.definition.audio_profile !== f.audio) return false;
  if (f.criterion && f.criterion !== "all" && !(s.definition.applicable_criteria ?? []).includes(f.criterion)) return false;
  const q = (f.query ?? "").trim().toLowerCase();
  return !q || JSON.stringify([s.scenario_id, s.presentation, s.definition.goal, s.definition.applicable_criteria]).toLowerCase().includes(q);
}

export function filterOptions(items) {
  const uniq = (xs) => [...new Set(xs.filter(Boolean))];
  return {
    groups: uniq(items.slice().sort((a, b) => (a.presentation?.group_order ?? 99) - (b.presentation?.group_order ?? 99)).map((s) => s.presentation?.group ?? UNGROUPED)),
    locales: uniq(items.map((s) => s.definition.locale)).sort(),
    audios: AUDIO_ORDER.filter((a) => items.some((s) => s.definition.audio_profile === a)).concat(uniq(items.map((s) => s.definition.audio_profile)).filter((a) => !AUDIO_ORDER.includes(a))),
    criteria: uniq(items.flatMap((s) => s.definition.applicable_criteria ?? [])).sort(),
  };
}

/** Pure: newest run per scenario and provider. `runs` are newest first (the API order). */
export function latestResults(runs) {
  const byScenario = new Map();
  for (const r of runs) {
    if (!r.scenario_id || !r.provider_config_id) continue;
    if (!byScenario.has(r.scenario_id)) byScenario.set(r.scenario_id, new Map());
    const m = byScenario.get(r.scenario_id);
    if (!m.has(r.provider_config_id)) m.set(r.provider_config_id, { run_id: r.run_id, provider: r.provider_config_id, result: runResult(r), at: r.started_at ?? r.last_event_at ?? null, state: r.state });
  }
  return byScenario;
}

/** Pure: run id -> bake-off id from bake-off detail payloads (cells[].attempts[].run_id). */
export function bakeoffIndex(details) {
  const index = new Map();
  for (const d of details) for (const c of d?.cells ?? []) for (const a of c.attempts ?? []) if (a.run_id && !index.has(a.run_id)) index.set(a.run_id, { id: d.bakeoff?.bakeoff_id, name: d.bakeoff?.name });
  return index;
}

/** Pure: counts of results for a matrix cell. */
export function tally(resultMap) {
  const t = { pass: 0, fail: 0, other: 0 };
  for (const r of resultMap?.values?.() ?? []) { const k = r.result.key; if (k === "pass") t.pass++; else if (k === "fail") t.fail++; else t.other++; }
  return t;
}

/** Pure: tool ID -> readable label ("verify_identity" -> "Verify identity"). The raw ID stays available as a title. */
export const toolLabel = (id) => humanise(id);

/** Pure: what the scenario expects of the agent's tools. Empty sequence means the agent must make no tool calls. */
export function expectedTools(def) {
  const seq = def.expected_tool_sequence ?? [];
  const prohibited = [...new Set((def.turns ?? []).flatMap((t) => t.prohibited_actions ?? []))];
  const allowed = [...new Set((def.turns ?? []).flatMap((t) => t.allowed_actions ?? []))];
  return {
    sequence: seq,
    text: seq.length ? seq.join(" → ") : "No tool calls expected",
    none: !seq.length,
    note: seq.length ? "" : "By design: the agent should refuse or offer a human, not call account tools" + (prohibited.length ? "; calling " + prohibited.map((x) => toolLabel(x).toLowerCase()).join(", ") + " fails the scenario." : "."),
    prohibited, allowed,
    labels: seq.map(toolLabel),
  };
}

/** Pure: structured summary for the expanded card. */
export function summaryModel(s) {
  const d = s.definition;
  return {
    persona: humanise(d.customer?.persona ?? "unknown"),
    vulnerability: d.customer?.vulnerability && d.customer.vulnerability !== "none" ? humanise(d.customer.vulnerability) : "None",
    locale: d.locale, audio: audioLabel(d.audio_profile),
    identity: d.initial_state?.identity ?? "unknown", fixture: d.initial_state?.account_fixture ?? "unknown",
    goal: humanise(d.goal ?? ""),
    caller: (d.turns ?? []).map((t) => t.customer).filter(Boolean),
    tools: expectedTools(d),
    stops: (d.stop_conditions ?? []).map((c) => c.tool_called ? "Tool called: " + toolLabel(c.tool_called) : c.session_transferred ? "Session transferred to a human" : JSON.stringify(c)),
    criteria: (d.applicable_criteria ?? []).map((id) => ({ id, help: criterionHelp(id) })),
    faults: (s.faults ?? []).map((f) => humanise(f.kind) + (f.tool ? " on " + toolLabel(f.tool).toLowerCase() : "") + (f.delay_ms ? " (" + f.delay_ms + " ms)" : "") + (f.error_code ? " (" + f.error_code + ")" : "") + (f.snr_db != null ? " (" + f.snr_db + " dB SNR)" : "")),
    mechanics: (s.mechanics ?? []).map(humanise),
    limits: d.max_turns + " turns · " + d.max_duration_seconds + " s",
  };
}
