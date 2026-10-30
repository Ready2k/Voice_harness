# Standard-prompt bake-off, six providers (GPT-Live carried over) — 29/30 October 2026: run notes

Bake-off launched 2026-10-29 from `main` with `scripts/full_advanced_bakeoff.py --prompt-profile standardised-v2`. Five
providers (OpenAI Realtime, Nova Sonic, xAI Grok Voice, Gemini Live, ElevenLabs Agents) × 40 live scenarios × 3
repetitions = 600 scored cells, group-balanced rates, **standardised-v2** prompts (identical banking instructions for
every provider; no vendor tuning). All 600 cells completed.

**GPT-Live was not run here; its result is carried over (see below).** A six-provider launch paused in pre-flight: under the standard prompt GPT-Live's voice
model answered without calling `get_policy_text` on two separate fresh runs (`PREFLIGHT_VOICE_TOOL_INCOMPLETE`); the
other five passed. The check was not loosened. That attempt is kept, provisional, in `../bakeoff-2026-10-29-standard/`
(zero scored cells). GPT-Live's 28 October result used `advanced-v1`, whose extra line tells it to delegate to the backend.

**Setup changes since the 28 October run** (so the two are not like for like): assistant voices are British female where
the vendor offers one (Nova `amy`, Gemini `Aoede` with `en-GB`, ElevenLabs Amelia; OpenAI `marin` and xAI `ara` have no
British option); the fixed caller is macOS `Daniel` (was the system default voice); the ElevenLabs agent runs
`gemini-3.5-flash`. 28 October used `advanced-v1` prompts; this run uses `standardised-v2`.

**Assessment.** Independent ASR (OpenAI) on every reply; two judges per run, OpenAI `gpt-6-sol` and Claude Haiku 4.5 on
Bedrock. The first two launches omitted `VOICE_HARNESS_LIVE_JUDGES` (Bedrock judge disabled) but scored nothing; the
scored run had both judges.

**Pauses and retries.** Paused and resumed with fresh pre-flight each time: OpenAI credit exhaustion (twice),
`PROVIDER_FAILURE` on OpenAI Realtime `identity-fault-caller-interrupts-001` repetition 1 (passed on the next attempt),
ElevenLabs `PROVIDER_QUOTA_EXCEEDED` (credits). Other failed attempts retried under the normal rule: Gemini (one
`say -v Daniel` synthesis timeout, one provider failure), Nova (one turn timeout), ElevenLabs (one dropped websocket).
Cells with more than one attempt: OpenAI Realtime 3, Gemini 2, ElevenLabs 2, Nova 1.

**Cost.** The harness meters about $13 (voice and ASR). The judges are not metered: OpenAI's usage page shows the
`gpt-6-sol` judge was about $11 of roughly $18 spent over 29 and 30 October (cache writes the largest line).
Investigation and fixes are tracked separately.

**Known limitations.** Latency is disclosed as not like for like (mixed turn control). The report's verdict is
`no_clear_winner`: the top two on the quality composite cannot be separated.

## GPT-Live: carried over from 28 October (added 30 October)

`report.html` / `report.json` now carry six providers (720 cells). GPT-Live's 120 cells (40 scenarios × 3) are copied from
`../fair-bakeoff-2026-10-28/rescored-2026-10-28/` (final claims-v2 re-score) and **were not re-run**; each is marked
`carried_over`. `scripts/merge_gpt_live_into_standard.py` does the merge with the shipped scoring code, and the original
five-provider report is kept as `report-five-provider.json` (its numbers for the other five are unchanged).

**Not like for like.** GPT-Live ran on `advanced-v1` (the delegate-to-backend line), with the 28 October voice (`marin`) and
system-default caller, and its latency is estimated (provider endpointing, replies grouped
from output silence). The report's prompt disclosure now reads `mixed` and lists GPT-Live's profile.

**Cost (an assumption).** The 28 October cells were unpriced because the `gpt-live-1` tariff ($0.05 per minute of billable
audio; backend `gpt-6-luna` $0.10 / $0.50 per 1M tokens) is dated 29 October. They are re-priced from their stored usage
events (read-only ledger) with `../pricing/vendor-pricing-2026-10-30-gpt-live-28oct.json`, a copy of the 30 October table in
which only the `openai_live` tariffs start on 28 October. No price changed; that they applied on 28 October is assumed, and
the report states this. Result: median $0.0167 per run, mean $0.0182, $2.21 over 121 priced of 123 runs (two failed attempts
reported no usage). Voice is billed by session duration, which includes harness waits, so it is not comparable with
token-billed providers.

**One scoring alignment.** The 28 October re-score left `commit-cancel-confirmed-001` out of task completion for every
provider; this run did not. The exclusion marker is dropped from GPT-Live's cells so it is scored on the same basis as the
other five.

**Result** (after the 30 October ASR rerun below). Composite: xAI 0.901, Gemini 0.899, GPT-Realtime 0.897, GPT-Live 0.894,
Nova 0.890, ElevenLabs 0.823. The report ranks on the composite excluding latency, where GPT-Live is nominally first
(0.995, ahead of xAI 0.995, Gemini 0.993, GPT-Realtime 0.985, Nova 0.975). The verdict is still `tied_on_quality` / no clear
winner (leader minus runner-up spans zero), so GPT-Live's nominal first place is not a finding. Its edge on the quality-only composite (0.995) comes from perfect task success,
facts and safe speech; it is slowest of the OpenAI pair on first-audio (median 1.6 s against 1.4 s), and latency remains
mixed turn control.

## Rerun of ASR-blocked cells (30 October)

Seven scored cells completed on 29 October with an independent-ASR failure (`ASR_HTTP_429`, `assessment.asr_unavailable`
in the ledger, 21:03-21:04) and so carried an unchecked reply: FACT-01 `untestable` or `review_required` although the run
finished. `--resume` does not requeue completed runs and reply audio is not retained, so they could not be re-assessed. They
were rerun once more, as a second attempt that **replaces the first sample** (it is a fresh live run, not a re-check):
`happy-verify-retry-001` repetition 0 for all five providers, `identity-authority-employee-001` repetition 0 for Nova, and
`happy-transaction-details-001` repetition 0 for ElevenLabs. The rule was by ledger, for every provider, whatever the first
outcome (`scripts/rerun_asr_blocked_cells.py`; GPT-Live's carried-over cells are not affected). All seven completed with
independent ASR and the same judges; pre-flight passed again for the five providers. The earlier attempt and its cost stay
on the cell (`earlier_costs`).

Effect (five providers; composite before then after): xAI 0.892 then 0.901, Gemini 0.890 then 0.899, GPT-Realtime 0.887 then
0.897, Nova 0.815 then 0.890, ElevenLabs 0.814 then 0.823. Nova's factual score went 0.643 then 1.0: the earlier dip was the
unchecked replies, not wrong answers. The verdict stays `tied_on_quality`. ElevenLabs still carries three
`TRANSCRIPT_UNAVAILABLE` cells (`identity-fault-caller-interrupts-001`, a different fault) and remains low on factual (0.75).

The run used a separate output folder (`prompts.json` here also holds GPT-Live's carried-over record, which the launcher's
frozen-prompt guard rejects); the five providers' prompts and bindings were checked identical to the frozen ones. The ledger
before the rerun is backed up outside git. `report-five-provider.json` was replaced by the post-rerun five-provider report.
