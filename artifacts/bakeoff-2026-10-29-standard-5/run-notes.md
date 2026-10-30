# Standard-prompt bake-off, five providers — 29/30 October 2026: run notes

Bake-off launched 2026-10-29 from `main` with `scripts/full_advanced_bakeoff.py --prompt-profile standardised-v2`. Five
providers (OpenAI Realtime, Nova Sonic, xAI Grok Voice, Gemini Live, ElevenLabs Agents) × 40 live scenarios × 3
repetitions = 600 scored cells, group-balanced rates, **standardised-v2** prompts (identical banking instructions for
every provider; no vendor tuning). All 600 cells completed.

**GPT-Live is not in this run.** A six-provider launch paused in pre-flight: under the standard prompt GPT-Live's voice
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
