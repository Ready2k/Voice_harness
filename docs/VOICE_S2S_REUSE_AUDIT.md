# Voice_S2S reuse audit

The existing `AntiGravity_projects/Voice_S2S` code is useful as a source of proven implementation patterns, but it is an application backend rather than a provider-neutral evaluation harness. The first port keeps the safety logic and removes assumptions that would undermine the v0.3 evidence model.

| Voice_S2S source | Reused learning | Voice Harness destination |
|---|---|---|
| `backend/src/banking-authorization.ts` | Server-side identity gating, account binding, deny-before-execute | `src/voice_harness/policy.py` |
| `backend/src/identity-digit-policy.ts` | Preserve leading zeros; exact 8/6 digit validation; replace whole attempts | `policy.py` contract tests; planned simulator prompts |
| `backend/src/tool-policy.ts` | Server candidate set is authoritative; client can only narrow exposure | Planned adapter/tool registry layer |
| `backend/src/tool-call-registry.ts` | A call settles once and duplicate events do not re-execute work | Planned `ToolLifecycle` state machine; golden path currently proves zero execution |
| `backend/src/transcript-coalescer.ts` | Provider transcript updates/replays need stable identity and revision handling | Planned transcript revision worker; current golden fixture uses final evidence |
| `backend/src/voice-timing.ts` | Separate speech end, first generated audio, and browser acknowledgement | Planned measurement module |
| `backend/src/evaluate-session.ts` | Correlate tool requests/results and surface audio metrics instead of hiding missing data | Golden export findings and ledger evidence |
| `backend/src/simulation-service.ts` | Keep simulator role separate from assistant role; explicit wait/completion tokens | Planned customer simulator package |

## What is already met by this port

- Spec-aligned append-only canonical events retain provider-neutral and evidence links.
- Synthetic banking state is run-scoped and never contacts an external banking endpoint.
- The guardrail is independent of the mock provider and blocks protected reads before execution.
- Generated audio is held separately from released audio; release requires an exact unit hash and epoch-bound token.
- A model proposal failure and platform enforcement pass are reported separately.
- CI can run without provider credentials.

## What should not be copied directly

- The existing Bedrock/AWS client setup and `.env` files: provider access belongs behind verified adapters and secrets must remain outside source control.
- Application playback/WebSocket routes: the gate must be the only privileged audio sink.
- Existing session history as benchmark evidence: it is not automatically a v0.3 manifest, has unknown fixture/clock/provenance status, and should only be imported through a redacted migration tool.
- Any claim that a registered provider is supported: v0.3 requires an unexpired capability probe and explicit coverage state.

## Remaining highest-value reuse

1. Port `ToolCallRegistry` to a typed lifecycle with `cancelled`, `abandoned`, `outcome_unknown`, and idempotency keys.
2. Port transcript coalescing behind `transcript.revised` events and add amount/negation revision fixtures.
3. Port `VoiceTiming` into the measurement harness with clock-domain labels and reconnect invalidation.
4. Convert the banking tool definitions into JSON Schemas with `additionalProperties: false`.
5. Wrap the source's simulation prompt principles in a branch-recording customer simulator; never mix oracle state into assistant context.

## Nova Sonic reference implementation (noted 2026-10-16)

`AntiGravity_projects/Voice_S2S` contains a working Amazon Nova Sonic implementation covering audio and text input, barge-in/interruption handling, and native tool calls and results. Treat it as the primary reference when building the Phase 2 Nova adapter:

- `backend/src/sonic-client.ts`: Bedrock bidirectional stream session lifecycle, event handling, interruption, tool-result queue and usage events.
- `backend/src/sonic-tool-result.ts`: the toolResult event sequence Nova expects.
- `backend/src/test-stream.ts`: minimal stream access test (already uses `amazon.nova-2-5-sonic`).
- `assume-role.sh`: MFA role assumption; mirrored for this repo by `scripts/aws-session.sh`.

It is a source of proven patterns, not evidence: Phase 0 capability claims still require this repo's probe (`scripts/probe/nova_probe.mjs`). In particular, barge-in and interruption were not exercised by that probe, and the Voice_S2S behaviour should be re-verified here (for example with `--speech-wav`) before the row moves from `unknown`.
