# Consultant UI/UX review — follow-up and verification

20 October 2026. This follow-up implements and tests the first consultant-review priorities. It is not a claim of completed user research or accessibility certification.

## GUI changes

- A run explanation appears before the activity view. It keeps model and platform verdicts separate, describes recorded denials, links directly to supporting events, and proposes the next inspection step. It does not infer safety, physical audibility or unauthorized execution counts from missing events.
- The fixed-stage diagram is called **System activity**. The chronological event timeline remains available. The inspector offers links to recorded causes only when those events exist in the viewed ledger. Unrecorded causal relationships are not drawn or invented.
- X/Y selectors, differences and paired inspection precede the full outcomes/configuration/metrics/cost tables. Detailed all-run tables are available in an expandable section.
- Review dispositions start with **Choose a disposition**, without a directional default. Reviewers can select supporting events from the run rather than transcribe event IDs. The picker shows the most recent 100 events; manual entry remains available for earlier evidence. Existing server validation and decision rules are unchanged.
- Manual testing labels playback **Assessed before playback** or **Assessed after playback**. Assessment is explicitly not a guarantee of safety.
- Unchanged lists, transcripts, timeline, inspector and cost views avoid destructive redraws. Updated lists/timelines retain focus and reading position where possible. Cost disclosures retain their open state. Opening a raw event pins that event during subsequent updates.

## Verification

- **86 browser regression tests passed**, including a second run against an isolated snapshot of committed files plus only this UI patch. New tests check evidence-bound explanations, missing assessments, available causal links, unchanged-view detection and explicit review disposition.
- **670 Python tests passed, 8 skipped**, in the current working tree. Live adapter, judge and ASR opt-ins were disabled. This run also exercises unrelated, uncommitted prompt/API changes; those changes are not part of the UI commit.
- An isolated API/GUI preview runs on **127.0.0.1:8767**, using its own SQLite data and token. It is separate from the existing Compose service on 8765 and the live host service on 8766. Provider credentials were removed from the preview environment.
- All six primary preview pages returned HTTP 200: Runs, Manual test, Compare, Review, Provider coverage and Scenarios.
- Browser verification: connected, launched a scripted fixture, inspected live evidence and explanations, claimed a synthetic review, verified its disposition placeholder, and selected a denial event to populate the supporting-evidence field. No paid-provider session was launched.

## Remaining work

- A full branching causal graph, including repeated calls and elapsed-time lanes, is a separate enhancement; the current system map is explicitly not that graph.
- A genuinely actionable overview still needs queue-level counts and changes since the previous evaluation. Current counts are scoped to recent runs.
- Primary-action discoverability and readable naming throughout the recent-run list need a further design pass.
- Representative-user tests and a complete keyboard/screen-reader/mobile audit remain necessary before a general-release UX sign-off.
- Existing backend constraints in the [capability audit](UI_UX_CAPABILITY_AUDIT_2026-10-18.md) still apply; this commit does not implement exact recorded-audio replay.

## Commit scope

Only the consultant-follow-up GUI modules, styling, documentation and its new regression test are included. Existing prompt-profile/API/contracts changes, their tests, README changes and unrelated artifacts remain outside this commit.
