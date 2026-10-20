# UX to-do

Sources: two UX/UI reviews (2026-10-20): the Runs page, then the other six tabs.
Status as of the final validation round: each page checked in a real browser, light and dark themes, at 1440 / 900 / 390px, with seeded data. Tests: 143 browser, 684 Python.

## Review 1: Runs page: all done
- [x] Connection pill in the header; setup and "Get started" shown only when not connected
- [x] Clickable summary cards as filters (Active, Execution failures, Needs review, Passed)
- [x] Full-width runs table with Result badge, relative time, duration, short ID + copy; mobile card layout
- [x] Failed rows marked; "Needs attention first" sort; "Load more" via `next_cursor`
- [x] Filters: search, family, provider, result, status, date
- [x] Run detail in a side drawer (focus trap, Escape, focus returns to the row); pulsing Live indicator only for live runs
- [x] "+ New evaluation" modal; Locale/Audio narrowing; key/value scenario details with tooltips
- [x] Refresh in list header; Launch is the only form button; seed hint; chevron toggles
- [x] Header pill stays inline; readable audio profile, status and policy labels

## Review 2: other six tabs: all done
### 1. Cross-cutting
- [x] Header "Connected" pill on every page (shared `chrome.mjs`); "Get started" only when disconnected on every page
- [x] One header/title/breadcrumb template; "Review & holds" everywhere; Auto/Light/Dark theme toggle
- [x] One display name per provider (`providers.mjs`), technical ID on hover; readable report IDs
- [x] Short IDs + copy, relative times, humanised labels, raw JSON behind a toggle (`format.mjs`)
- [x] `revealResult` after click-through results (Bake-off, Manual, Compare, Review)
- [x] Run IDs link to `./runs.html?run=`
- [ ] Bake-off / finding / hold membership badges on runs: needs a backend field on the runs list (not exposed today)

### 2. Bake-off: done
Collapsing left column, sticky section index, unclipped tables and short names, confidence note + caveats up, failures first + "only rows that differ", weights as % with live re-rank and Custom banner, distinct chart colours, sensible axes and "≥ cap" marks, launch confirmation with run count and estimate, rank-ordered matrix cards, "Eliminated at gates" badge, merged scenario groups, export note.

### 3. Review & holds: done
Expected / Observed / Transcript evidence with Raw toggle, finding list context + severity filter, remembered reviewer ID, claim + decide flow with draft preserved on failure, one holds table with countdown (amber < 7 days, red < 24 h / expired), add-hold from a finding, friendly idempotency-conflict message.

### 4. Compare: done
Defaults to a task with 2+ runs and shows counts, run picker table with quick picks, X / Y / Δ diff table with numeric latency rows, turn-by-turn aligned conversations with "show revisions", one disclaimer, live controls only while running.

### 5. Manual test: done
"Challenge path" wording, shared-scenario picker, playback-gate warning listing untestable criteria, history of recent sessions (`GET /api/v1/manual-sessions`, filtered in SQL), compact journeys and sticky panel (consistent breakpoint).

### 6. Scenarios: done
Group by family with collapsible headers and locale x audio matrix, filters (group, locale, audio, criterion), latest result per provider with run / bake-off links, structured Summary with Raw tab, readable tool labels, Import in header. The fraud-team "no expected tools" is by design (the scenario fails if an account tool is called).

### 7. Provider coverage: done
One merged table, expiry from the server's `expires_at` (falls back to checked time + 7 days; never contradicts server status), summary tiles, probe-event dialog (`GET /api/v1/providers/{family}/probe-events`). No free re-verify flow exists: the page shows how to re-verify (the probe makes paid provider calls).

## Known limits / possible follow-ups
- [ ] Runs DOM-flow browser test (needs a DOM test dependency; current tests cover pure logic and fake DOMs)
- [ ] Index on `config.input_mode` if the runs table grows large (manual-sessions filter is unindexed JSON extraction)
- [ ] Exported `report.html` follows the OS colour scheme only (it is self-contained and cannot read the page theme)
- [ ] Bake-off launch confirmation verified by layout and unit tests only (no live provider available in the test environment)
