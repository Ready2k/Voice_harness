// Provider coverage panel (AC-20). Pure view-model + DOM rendering; no ranking, no scores.
export const STATE_LABEL = {
  implemented_and_verified: "Implemented and verified",
  implemented_but_unverified: "Implemented, not verified",
  unavailable: "Unavailable",
  not_implemented: "Not implemented",
};

export const FAMILY_LABEL = {
  openai: "OpenAI Realtime",
  aws_nova_sonic: "Amazon Nova Sonic",
  xai_grok_voice: "xAI Grok Voice",
  google_gemini_live: "Google Gemini Live",
};

export const REASON_TEXT = {
  NO_ADAPTER: "No adapter has been built for this family.",
  REPORT_WITHOUT_ADAPTER: "A capability report exists but there is no adapter.",
  NO_CAPABILITY_REPORT: "No capability report has been produced.",
  REPORT_EXPIRED: "The capability report has expired (reports last 7 days).",
  REPORT_FAILED_CLOSED: "A required primitive is not verified or observed.",
  AC09_NOT_PASSED: "The AC-09 adapter conformance run has not passed for this report.",
};

/** Pure: one API item -> display model. Only implemented_and_verified counts as support. */
export function describe(item) {
  return {
    family: item.provider_family,
    title: FAMILY_LABEL[item.provider_family] ?? item.provider_family,
    state: item.coverage_state,
    stateLabel: STATE_LABEL[item.coverage_state] ?? item.coverage_state,
    counted: item.coverage_state === "implemented_and_verified",
    model: item.model_id ?? "none",
    region: item.region ?? null,
    checkedAt: item.checked_at ?? null,
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

export function render(root, model) {
  root.replaceChildren();
  root.append(el("p", "statement", model.statement));
  const grid = el("div", "grid");
  for (const c of model.items) {
    const card = el("article", "card state-" + c.state);
    card.append(el("h2", "", c.title));
    card.append(el("span", "badge", c.stateLabel));
    card.append(el("p", "meta", "Model: " + c.model + (c.region ? " · " + c.region : "")));
    if (c.checkedAt) card.append(el("p", "meta", "Report " + c.reportId + " checked " + c.checkedAt));
    if (c.counted) card.append(el("p", "meta", c.evidenceCount + " probe events"));
    if (c.reasons.length) {
      const list = el("ul", "reasons");
      for (const r of c.reasons) list.append(el("li", "", r));
      card.append(list);
    }
    grid.append(card);
  }
  root.append(grid);
}

export async function load(baseUrl, token) {
  const response = await fetch(baseUrl + "/api/v1/providers", {
    headers: { Authorization: "Bearer " + token },
  });
  if (!response.ok) throw new Error("HTTP " + response.status);
  return view(await response.json());
}
