# Fair-harness provider bake-off — 28 October 2026: run notes

Bake-off `bko_851d76efa4ea410e91f30524c20d10e1`, launched 2026-10-28T08:10:42Z from `main` at `80eb8d1` (all bias
remediation in `docs/PROVIDER_BIAS_REVIEW_2026-10-27.md` merged). Re-issued with the claims-v2 parser fix in
[`rescored-2026-10-28/`](rescored-2026-10-28/README.md); read that report for conclusions.

**Plan.** Six providers (GPT-Live, OpenAI Realtime, Nova Sonic, xAI Grok Voice, Gemini Live, ElevenLabs Agents) × 40
live catalogue scenarios × 3 repetitions = 720 scored cells, advanced-v1 prompts, group-balanced rates, six concurrent
jobs and two per provider. Transport profile `provider-transport-v3`: first audio anchored at the recorded end of speech,
explicit commits 500 ms after speech, caller waits for reply playback plus 300 ms, caller audio deadline-paced.
Sampling policy `provider_default` for every provider (the ElevenLabs agent was re-provisioned before launch).

**Assessment.** Independent ASR (OpenAI) on every reply. Two judges on every run: OpenAI `gpt-6-sol` and Claude Haiku 4.5
on Bedrock (`us.anthropic.claude-haiku-4-5-20251001-v1:0`). `gpt-6-luna` was deliberately not used as a judge: it is
GPT-Live's backend model and would have been ineligible on GPT-Live runs. Judge blinding was measured hidden on the runs
checked; same-vendor and same-host relationships are disclosed in each run's independence record.

**Pauses and retries.** The bake-off paused twice and was resumed after fresh provider checks each time; completed
evidence was retained and failed attempts were retried under the normal retry rule.

1. At 306/720: `PROVIDER_CREDIT_BALANCE_EXHAUSTED` on the OpenAI account (affects GPT-Live, OpenAI Realtime, the
   independent ASR and the OpenAI judge). Resumed after a top-up.
2. At 665/720: `PROVIDER_HTTP_403` from xAI session setup (account credit). Resumed after a top-up.

All six providers passed pre-scoring checks at launch and at both resumptions. All 720 cells completed.

**Known limitations.** Latency is disclosed as not like for like (explicit-commit providers get a common emulated 500 ms
endpointing; provider-VAD providers pay their own). GPT-Live turn boundaries are a tuned heuristic (no provider
reply-complete event). Four OpenAI Realtime balance replies remain `AMBIGUOUS_AMOUNT` and their audio is retained for
human listening (see the re-score README). `commit-cancel-confirmed-001` (v2 in this run) had no clarification turn and
is excluded from task completion in the re-score; v3 adds one but has not yet been run live.

Local SQLite evidence, retained audio, signing keys and tokens stay in the git-ignored `.local/fair-bakeoff-2026-10-28`.
Portable files here contain results and prompt metadata, not raw audio or credentials.
