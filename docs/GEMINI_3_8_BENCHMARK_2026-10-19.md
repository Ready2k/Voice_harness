# Gemini 3.8 migration and benchmark rerun

New `gemini-live` sessions request `gemini-3.8-live` on Vertex AI in `us-central1` using
Application Default Credentials. All 33 Gemini runs in this batch record that exact requested
model. The server does not echo a model version, so returned-version resolution remains unknown.
No fallback to 2.5 was used. Google documents this model and region in its
[3.8 Live guide](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/guides/gemini-3-8-live).
The current project's global endpoint rejected setup; `us-central1` accepted it.

## Results

The rerun covered all 33 live scenarios against all four providers: 132 new runs, one per cell,
without retries. Failed and untestable outcomes remain in the report. These are samples, not
statistically established differences between providers.

| Provider | Requested model | Assurance passes | Runs completed | Median first audio | 90th percentile | Gate result |
|---|---|---:|---:|---:|---:|---|
| Nova | `amazon.nova-2-5-sonic` | 33/33 | 33/33 | 654 ms | 1,755 ms | Advances |
| OpenAI | `gpt-realtime-2.1` | 32/33 | 33/33 | 1,066 ms | 1,378 ms | Advances |
| xAI | `grok-voice-3.0` | 30/33 | 31/33 | 971 ms | 1,859 ms | Eliminated: completion below 95% |
| Gemini | `gemini-3.8-live` | 28/33 | 30/33 | 1,505 ms* | 2,242 ms* | Eliminated: prohibited scenario tool and completion below 95% |

Nova's composite is 0.984 and OpenAI's is 0.962. Their paired outcome difference is not
significant (exact McNemar p = 1.0); no provider pair is significant at 0.05 in this sample.
Gemini's composite is 0.856. Composite ranks do not override the gates.

Gemini's five non-pass cells were three `TURN_TIMEOUT` failures (balance in street noise,
and the GB and US fraud-team scenarios in street noise), one untestable slow-lookup fault,
and one failed unconfirmed-cancellation scenario. In the latter, Gemini verified identity and
executed `get_account_summary`, which that scenario prohibits. This was a scenario-proposal
failure; the identity, confirmation and audio criteria passed. No payment cancellation was executed.

*Gemini first-audio timing remains estimated from native message sizes because transcripts
and audio share `serverContent` messages. Timing measures provider response after caller speech,
before gated playback. Overlapping replies are excluded from latency. Some matrix runs overlapped
the deterministic tests and component benchmark on the same host, so this is not an isolated
latency comparison with the historical batch. Each provider had at most two concurrent runs,
with at most eight overall. No automatic subjective judges were configured for these runs;
independent ASR and deterministic checks do not establish every subjective quality criterion.

## Deliverables and evidence

- [Interactive report](../reports/voice-provider-benchmark-gemini38-2026-10-19.html)
- [Printable report](../reports/voice-provider-benchmark-gemini38-2026-10-19.pdf)
- [Fresh matrix](../artifacts/provider-matrix/matrix-gemini38-2026-10-19.json)
- [Model bindings, report statistics and conditions](../artifacts/gemini38/benchmark-summary-2026-10-19.json)
- [Component benchmark](../artifacts/gemini38/component-benchmark-2026-10-19.json): 12 PostgreSQL profiles,
  1,000 observations per profile, with TCP WebSocket replay and fault measurements.
- [Current Gemini capability report](../artifacts/phase0/google_gemini-capability-report.json),
  generated from `google_gemini-observations-2026-10-19T11-19-49-451Z.json` and its redacted probe log.
- [Live AC-09](../artifacts/phase2/google_gemini_live-ac09.json): audio input/output, tool correlation,
  and error dimensions pass. Client truncation remains excluded. The interruption probe saw an
  interruption marker after 580 ms and continued audio afterward; it does not prove client truncation.

The old 2.5 capability report and conformance result are preserved in `artifacts/gemini38/baseline/`.
The original matrix and reports remain historical artifacts. The first 11 Gemini runs in this
new batch started before the capability report refresh and cite the previous 2.5 report;
the remaining 22 cite `cap_google_gemini_3_8_live_20261019`. Every one of the 33 requested 3.8.
Those immutable ledger bindings were not rewritten. The runtime now rejects a capability
binding whose model differs from the configured model, preventing this mismatch in future runs.

The benchmark used a fresh API process at `127.0.0.1:8767`, with ledger storage in
`.local/gemini38-data/development.sqlite`. Regenerate the HTML with `scripts/benchmark_report.py`
using that database and the fresh matrix. The delivered report footer additionally records
models, overlapping workloads and capability-binding provenance. The PDF uses the report's
print layout, which wraps wide tables and long result labels onto the page.

## Validation and engineering fixes

The rerun exposed concurrent PostgreSQL schema initialisation replacing the evidence trigger
function, producing `tuple concurrently updated` in MCP workers. A transaction-scoped advisory
lock now serializes schema initialisation across processes. A regression starts eight database
initialisers together and checks they can still read the original run's session.

The bake-off import test also raced completed-worker removal from the API job map. It now
waits for a worker when still present and asserts its terminal database state before importing.

Final validation: `make test` passed 639 Python tests (5 opt-in skips) and all 36 browser
tests, against PostgreSQL in Docker. The concurrent-initialisation regression and bake-off import
test both pass. `make lint`, `make audit` (no known vulnerabilities) and `git diff --check`
passed. The full Gemini 3.8 live probe and supported AC-09 dimensions passed. All 12 component
benchmark profiles completed. The final 10-page PDF was rendered and visually checked after
fixing print-table overflow; the HTML loaded without browser JavaScript errors.

The live API process was started after the Gemini model change and before the later capability
model-matching guard and PostgreSQL startup fixes. The matrix uses SQLite and describes that
process; the final deterministic suite verifies the subsequent fixes. Validation details and
artifact hashes are in `artifacts/gemini38/validation-2026-10-19.json`.
