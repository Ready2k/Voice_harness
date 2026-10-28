# Provider bias review: why Nova keeps winning bake-offs (2026-10-27)

Scope: the bake-off pipeline at `d8a591b` (transport, latency measurement, scoring, judges, prompts), checked against
the 600-cell bake-off in `artifacts/full-advanced-bakeoff-2026-10-26/report.json`. No code was changed.

Every finding below is backed by code that was read, plus run data where the effect can be measured. Where the code
shows an asymmetry but the size or direction of its effect could not be established, the finding says so.

## Summary

**Yes: there is a measurement bias, and it is what puts Nova first.** The first-audio latency figure includes
1.2 seconds of trailing silence for OpenAI Realtime, xAI and Gemini. Nova, ElevenLabs and GPT-Live are not charged
for it. In the 26 October bake-off the quality dimensions are almost tied, so the composite is decided by
responsiveness. With the 1.2 s removed, xAI overtakes Nova and every provider's ranking changes.

| Provider | Median / p90 first audio as reported | Without the trailing silence | Composite as reported | Composite without it |
|---|---|---|---|---|
| Nova Sonic | 843 / 1795 ms | 843 / 1795 ms (unchanged) | **0.918 (1st)** | 0.918 (2nd) |
| xAI Grok Voice | 2136 / 2472 ms | 936 / 1272 ms | 0.867 (2nd) | **0.920 (1st)** |
| Gemini Live | 2219 / 2510 ms | 1019 / 1310 ms | 0.867 (3rd) | 0.915 (3rd) |
| OpenAI Realtime | 2058 / 2392 ms | 858 / 1192 ms | 0.823 (5th) | 0.883 (4th) |
| ElevenLabs | 1982 / 3745 ms | 1982 / 3745 ms (unchanged) | 0.837 (4th) | 0.837 (5th) |

To produce the right-hand columns, 1200 ms is subtracted from every turn of the three providers that use explicit
commits. Subtracting a constant shifts the median and p90 exactly. The composites then use the shipped formula and
weights (`bakeoff.py:583-604`, `bakeoff_stats.py:46-60`). Even corrected, this is not a fully fair comparison; see
finding 2.

## Findings

### 1. Trailing silence counted as latency for explicit-commit providers (confirmed in code and data; high)

- `live_scenario.py:337-341`: each caller turn is streamed in real time (`_stream`, 20 ms chunks with
  `asyncio.sleep`, `live_scenario.py:154-157`). The PCM already ends with `CALLER_TRAIL_MS` = 1200 ms of silence
  (`live_providers.py:81`, `387`). `adapter.request_turn()` runs only after that.
- `request_turn` is the only thing that lets OpenAI Realtime and xAI respond (`input_audio_buffer.commit` plus
  `response.create`, `openai_realtime.py:243-246`, `xai_voice.py:226-229`). For Gemini it sends `activityEnd`
  (`google_gemini_live.py:283-287`). `configure_transport` puts all three in `manual` / `explicit` mode for fixed
  callers (`live_providers.py:284-286`).
- For Nova, `request_turn` does nothing (`nova_sonic.py:362-364`). Its provider VAD hears the trailing silence and can
  reply while it is still streaming. ElevenLabs and GPT-Live are also on provider VAD.
- `bakeoff.py:216` measures first audio from `stream_ended_ms - CALLER_TRAIL_MS`, which is the end of speech. That is
  correct for VAD providers. Explicit-commit providers could not have started until 1200 ms later.
- The data shows this floor. Of 480 measured turns for OpenAI Realtime, xAI and Gemini, none came in under
  1765 / 1890 / 1953 ms (minimum per provider). Nova had 92 of 155 turns under 1200 ms, with a minimum of 429 ms.
  ElevenLabs, also on VAD, had a minimum of 749 ms.
- Effect: 1.2 s on top of a 500 ms median target cuts the responsiveness score for these three providers from about
  0.53–0.61 to about 0.26. At a weight of 0.176, that is about 0.05 off the composite. This is roughly the whole of
  Nova's lead over xAI and Gemini (0.052).
- Origin: commit `795d8e7` (2026-10-20) introduced explicit commits for prerecorded input. The latency anchor
  written in `c5eaa59` assumed every provider could start responding at the end of speech, and it was not changed.

### 2. Latency is measured differently depending on turn mode (confirmed in code; medium)

Even after the 1.2 s is removed, the comparison is not like for like. VAD providers' figures include their own
endpointing delay, which is real and paid in production. Explicit-commit providers are told exactly when the turn
ends, so they pay no endpointing cost. The corrected table therefore leans slightly towards xAI, Gemini and OpenAI.
The true ranking lies between the two tables. Neither table can name a latency winner.

### 3. Quality is saturated, so the composite is effectively a latency ranking (confirmed in data; medium)

In the 26 October run, Nova, xAI and Gemini all score 1.0 for factual accuracy, safe speech and robustness. Their
assurance pass rates are 0.996, 0.989 and 0.993. The verdict already says `no_clear_winner: true`,
`speed_only_lead: true` and `quality_lead.lo: 0.0`, and the report text does not crown a winner
(`bakeoff-report.mjs:50-59`).

However, the scorecard still orders Nova first. The weight-sensitivity check reports `rank_stable: true` and
`weight_win_share: 1.0`. Those figures inherit finding 1: every random weighting includes the biased responsiveness
score, so Nova wins all of them. The page presents that as a stable result when it is a reproduction of the
measurement artefact.

### 4. Provider blinding for judges does not work and is uneven (confirmed in code; low, direction unknown)

`judges/oracle.py:21,36-42` masks only `openai, anthropic, amazon, nova, gemini, xai, grok`, matching lower case and
`.title()` only. The full `run.live_binding` event reaches every judge, including `provider` session metadata and the
adapter class name (`live_scenario.py:119-125`, `judges/service.py:79`). After masking:

- `elevenlabs_agents` and `gpt-realtime-2.1` pass through unchanged.
- `google_gemini_live` becomes `google_[provider]_live`.
- `aws_nova_sonic` becomes `aws_[provider]_sonic`.
- `NOVA` and `OpenAI` (as written) are not masked.

Judges can therefore identify every provider. The independence record still claims
`provider_identity_hidden: true` (`independence.py:52`). There is no evidence that this helps Nova: the judges are
Claude Haiku 4.5 on Bedrock and GPT, and the quality dimensions are tied anyway. It is still a bias channel.

### 5. Nova gets the orchestrator confirmation in a different way (confirmed in code; low, direction unknown)

In scenarios with `out_of_band_confirmation`, the five other providers receive the confirmation ID as context only
(`send_text(respond=False)`). Nova receives it as an interactive USER text turn that triggers a reply
(`live_providers.py:349, 380-383`; `nova_sonic.py:327-335`). This happens just before the caller's next turn is
streamed. It changes the turn structure for Nova in those scenarios only. Its effect on the score was not measured.

### 6. Judge-independence disclosure misses Nova's AWS link (confirmed in code; low)

Bedrock judges are recorded with `provider="bedrock"` (`judges/backends.py:231`). Nova is recorded with
`provider="aws"` (`live_providers.py:69`), so `SAME_PROVIDER_AS_TESTED_MODEL` is never raised for Nova. The OpenAI
judge does raise it for the OpenAI providers. This is a gap in what gets disclosed, not a scoring effect: the
default Bedrock judge is Anthropic, not Nova family.

### 7. Smaller asymmetries (confirmed in code; low)

- Gemini's first audio is the first `serverContent` payload of at least 2000 bytes (`bakeoff.py:366-367`). This can
  only make Gemini look later than it is, and the report flags it as `estimated`.
- Under `advanced-v1`, prompts are tuned per vendor (`target_prompts.py:70-85`). Nova alone gets
  "Give a direct answer first…". The prompt is marked `vendor_tuned`. It affects reply style, not first-audio
  timing, so its scoring effect is small.

### Checked and found neutral

- Scoring and statistics (`bakeoff_stats.py`, `bakeoff_metrics.py`) have no provider-specific branches.
- Every provider has the same weights, gates and Wilson / McNemar maths.
- Independent ASR (OpenAI) and the synthetic caller voice are the same for every provider. They are not
  Nova-affiliated.
- No judge is Nova family.
- Scheduling order and time-of-day effects were not examined in depth.

## Remedial steps (in priority order)

1. **Fix the latency anchor.** Either commit at the end of speech, by splitting the caller PCM into speech and then
   trailing silence and calling `request_turn()` before the silence, or put every provider on server VAD with the
   same trailing silence for fixed callers. Server VAD is the closest match to a real phone call and makes every
   provider pay for its own endpointing. Record which mode was used in each latency row.
2. **Re-score existing bake-offs without rerunning them.** `input.caller_audio.stream_ended_ms` is in every ledger,
   so latency for explicit-commit cells can be anchored at the commit. Mark rows `LATENCY_NOT_COMPARABLE` when
   providers in one bake-off used different turn modes. Re-issue the 26 October report with that caveat.
3. **Add a regression test.** A mock adapter with a fixed 300 ms response delay, run under explicit commit and under
   provider VAD, must produce the same measured first audio within tolerance. This would have caught finding 1.
4. **Stop the scorecard ranking a speed-only lead.** When `speed_only_lead` is true, show the scorecard unranked or
   tied, and suppress or caveat `rank_stable` and `weight_win_share`. Also show a "latency excluded" composite beside
   the full one.
5. **Make blinding real.** Leave `run.live_binding.provider` and the adapter name out of the oracle. Mask
   case-insensitively and extend the list (google, elevenlabs, gpt, aws, sonic, bedrock, x.ai). Add a test that no
   provider token survives in `build_oracle` output for each provider. Until then, set
   `provider_identity_hidden: false`.
6. **Deliver the confirmation the same way to every provider.** Use a non-interactive context injection for Nova, or
   document that it is not possible and exclude those scenarios from head-to-heads that include Nova.
7. **Normalise provider identity for independence checks** so that Bedrock-hosted judges and Nova both count as
   AWS.
8. **Rerun a bake-off after steps 1 and 3** and compare it with this review's corrected table.

## Remediation status (2026-10-28)

All eight remedial steps above, plus further issues found in an independent review, are implemented on `main`
(merged from `bias-remediation`); step 8 (a live rerun) follows this merge.

| Finding | Status | Where |
|---|---|---|
| 1. Trailing silence counted as latency | Fixed: first audio anchored at the recorded end of speech; explicit commits sent after a common 500 ms emulated endpointing delay; caller audio deadline-paced | `live_scenario._speak`, `bakeoff_metrics.speech_anchor`, `tests/test_latency_anchor.py` |
| 2. Mixed turn-control latency | Disclosed: `latency_comparability`; ranking falls back to the composite without latency | `bakeoff.report`, renderer |
| 3. Saturated quality, speed-only ranking | Fixed: tied standings instead of a first place; quality-only weight sensitivity; pairwise "X over Y" contrasts | `bakeoff.report`, `bakeoff_stats` |
| 4. Judge blinding | Fixed structurally; blinding claim now measured | `judges/oracle.py` |
| 5. Nova confirmation delivery | Recorded as `confirmation_delivery` and disclosed; a live probe is still needed to change it | `live_providers.run_live` |
| 6. AWS host not disclosed | Fixed: judge/tested host and vendor, component models (GPT-Live backend, ElevenLabs LLM) | `judges/independence.py` |
| 7. Gemini estimate, vendor prompt deltas | Fixed: exact audio bytes and common first *audible* audio; prompt deltas disclosed | `audio_activity.py`, `bakeoff_disclosure.py` |
| 8. Rescore and rerun | 26 Oct report rescored (`artifacts/full-advanced-bakeoff-2026-10-26/rescored-2026-10-27/`); live rerun pending | `scripts/rescore_bakeoff_report.py` |

Found in the follow-up review and fixed: task completion was unscored (now a dimension); spoken transaction ids were
rejected (`spoken_ids.py`); FACT-01 failed ambiguous spoken amounts (v2: untestable, needs review); sampling overrides
for Nova (temperature 0.2) and ElevenLabs (0) removed; the scripted caller spoke over still-playing replies and stale
provider interruptions settled or cancelled the next reply (this is why xAI never cancelled in
`commit-cancel-confirmed-001`); disputed reply audio is now retained for human review; GPT-Live's turn boundary,
pacing, hidden prompt wrapper and backend-model independence were corrected (`docs/OPENAI_LIVE.md`).

Rescored 26 October result: all five providers are tied on safety and correctness; Nova's first place was a
measurement artefact. See `docs/SCENARIO_VALIDITY_AUDIT_2026-10-27.md` and `docs/PROVIDER_CONFIG_PARITY.md`.
