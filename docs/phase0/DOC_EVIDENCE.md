# Phase 0 documentation evidence pass

Retrieved: 2026-10-16. Method: pages fetched and summarised by an extraction model, so every row is **secondhand, documentation-only**. Per spec §2, documentation alone never makes a cell `verified`; all claims below are `hypothesis` until a live account/region probe passes. `PROVIDER_MATRIX.md` (approved) is unchanged. "not found" means the fetched page did not cover it, not that it is unsupported.

## OpenAI
Sources: developers.openai.com/api/docs/guides/realtime, /guides/realtime-conversations, /models/gpt-realtime-2.1 (platform.openai.com 301-redirects there).
| Row | Doc claim |
|---|---|
| Model | `gpt-realtime-2.1`, single snapshot, no aliases listed; lifecycle/deprecation not stated. Endpoint `v1/realtime`. Seed `gpt-realtime` in spec is superseded as a candidate. |
| Transport | WebSocket (server), WebRTC (browser); SIP not mentioned on the fetched page. Ephemeral keys via `POST /v1/realtime/client_secrets`. |
| Audio | `audio/pcm` 24 kHz examples (matches harness D-01), `audio/pcmu` also shown; base64 over WebSocket, 15 MB max per append. Full format list not found. |
| Transcript | `response.output_audio_transcript.delta/.done`; input-transcript finality not documented on the page. |
| Turns | VAD default on (`semantic_vad` in examples); `turn_detection: null` for manual; `input_audio_buffer.speech_started/stopped/committed`. |
| Cancel/truncate | Server emits `response.cancelled` on barge-in; WebSocket clients send `conversation.item.truncate` (`item_id`, `content_index`, `audio_end_ms`); `response.cancel` for push-to-talk. |
| Tools | `function_call` in `response.done`; return via `conversation.item.create` `function_call_output` then `response.create`. MCP listed as supported (details not fetched). |
| Limits | 60 min max session; 128k in / 32k out context. Rate limits Build 400 RPM / 200k TPM. Pricing per 1M tokens: audio in $32, out $64. |
| Not found | Usage fields in `response.done`, reconnect/resume, retention terms. |

## Amazon Nova 2 Sonic (Bedrock)

**Operator decision (2026-10-16): Nova 2.5 Sonic is the target model.** Evidence for it is thin: one secondary blog (aitamer.news, "generally available in Bedrock", dated 2026-10-05) reports regions us-east-1, us-west-2, eu-north-1, ap-northeast-1, and no primary AWS page or model ID was found. The Nova 2 rows below are the closest documented baseline and are **not** evidence for 2.5. **Primary source now read:** [Nova 2.5 Sonic service card](https://docs.aws.amazon.com/ai/responsible-ai/nova-2-5-sonic/overview.md) (current as of 2026-10-05). It states 2.5 is the successor to Nova 2 Sonic with "the same API surface, voices, and language support" and improved tool calling and instruction following, so the Nova 2 API rows below are a plausible baseline for 2.5 but still `hypothesis` until probed. Also stated: Bedrock bidirectional streaming API; 18 voices; tool use and async tool completion; cannot be fine-tuned; safety filters not configurable; inaudible watermark on generated audio; Bedrock "does not store or review" prompts or generations and does not train on them (Service Terms 50.3). The card gives **no model ID, regions, quotas or pricing**, and retention terms still need operator attestation (spec §3). Watermarking and non-configurable filters are limitations for assurance runs.

The 2.5 model ID stays unconfigured until `aws bedrock list-foundation-models --region <region>` (filter "sonic") or AWS docs name it, then a probe passes (spec §3). Status: `unknown`.
Sources: docs.aws.amazon.com/nova/latest/nova2-userguide/ (using-conversational-speech, sonic-getting-started; search hits for sonic-input-events, sonic-tool-configuration, sonic-event-flow not read in full).
| Row | Doc claim |
|---|---|
| Model | `amazon.nova-2-sonic-v1:0`; sample default region `us-east-1`; other regions not found. |
| API | Bedrock Runtime `InvokeModelWithBidirectionalStream`, SigV4 auth, JSON events (`sessionStart`, `promptStart`, `contentStart`, `audioInput`, `textOutput`, `audioOutput`, `contentEnd`, `promptEnd`, `sessionEnd`). |
| Audio | Input 16 kHz, output 24 kHz, 16-bit mono LPCM base64. **Input differs from harness 24 kHz, so resampling is required** (see PHASE1_STATUS prerequisite). |
| Turns | `turnDetectionConfiguration.endpointingSensitivity` (`HIGH` in sample); graceful interruption claimed. |
| Transcript | `textOutput` for USER and ASSISTANT; assistant text gated on `generationStage` `SPECULATIVE` vs final. |
| Tools | Model emits `toolUse` (name, JSON params, `toolUseId`); app must answer with `toolResult` (TOOL-role content block), else the model waits. Asynchronous tool handling claimed. |
| Limits | 8-minute connection limit; session-continuation pattern in samples. |
| Not found | Nova 2 `usageEvent` schema (only Nova 1 shape seen), Nova 2 interruption event (Nova 1 used `{"interrupted": true}` text), quotas, pricing, retention. |

## xAI Grok Voice
Sources: docs.x.ai/developers/model-capabilities/audio/speech-to-speech, /developers/models.
| Row | Doc claim |
|---|---|
| Model | `grok-voice-3.0`; `grok-voice-latest` aliases it (pin the versioned name; alias resolution is exactly the `version_unresolved` case). `grok-voice-2.0` deprecated. |
| Transport | `wss://api.x.ai/v1/realtime?model=<id>`, Bearer key or `xai-client-secret.` ephemeral token; `session.update` after connect. |
| Audio | `audio/pcm` (8/16/22.05/24/32/44.1/48 kHz, default 24k), `pcmu`, `pcma`, `opus`; JSON base64 or binary frames. |
| Transcript | Cumulative `conversation.item.input_audio_transcription.updated` only with `grok-transcribe`. |
| Turns | `server_vad` or manual commit. |
| Cancel/truncate | No explicit cancel or truncation event documented; barge-in automatic under `server_vad`. `output_audio_buffer.clear` unsupported. |
| Tools | `function` (client) plus server-side `mcp`/`web_search`/`file_search`/`x_search`; return via `function_call_output` then `response.create`. Server-side MCP is not the harness `gateway_bridge`. |
| Resume | Opt-in history replay, 30-minute cache. |
| Pricing | $0.08/min. OpenAI-compatibility is claimed for most SDKs but several events differ or are unsupported, so do not assume compatibility. |
| Not found | Regions, rate limits, retention terms, usage fields. |

## Google Gemini Live
Sources: ai.google.dev/gemini-api/docs/live-api, /live-guide, /live-session.
| Row | Doc claim |
|---|---|
| Model | `gemini-3.8-live`, `gemini-3.8-live-extended-thinking`; `gemini-3.1-flash-live-preview` legacy. |
| API surface | Developer API documented here; Vertex AI differences **not found**, so the surface decision stays open. |
| Transport | Stateful WebSocket; server-to-server or client-to-server with ephemeral tokens. |
| Audio | Input raw PCM16 16 kHz; output PCM16 24 kHz. **Input rate differs from harness 24 kHz.** |
| Transcript | `inputAudioTranscription` / `outputAudioTranscription` in setup. |
| Turns | `realtimeInputConfig.automaticActivityDetection` (sensitivity, `silenceDurationMs`), manual `activityStart/End`. |
| Cancel | `interrupted` flag in server content; pending function calls discarded, cancelled IDs reported. No truncation event documented. |
| Tools | Function calling with `NON_BLOCKING` default on 3.8; sequential on 3.1; behaviour/scheduling options per model. |
| Limits | Audio-only 15 min, 2 min with video, ~10 min per connection; context 128k/32k. |
| Resume | `sessionResumption` handles valid 2 h; `GoAway` with `timeLeft`; `contextWindowCompression`. |
| Usage | `usageMetadata` with `totalTokenCount`, per-modality details. |
| Not found | Regions, retention, pricing. |

## Cross-provider findings
- Nova (16 kHz input) and Gemini (16 kHz input) do not accept the harness 24 kHz input per the fetched pages; OpenAI and xAI document 24 kHz. Resampling is a prerequisite for the Nova and Gemini adapters.
- Direct MCP: only xAI documents server-side `mcp`; the others need `gateway_bridge`.
- Truncation: only OpenAI documents explicit truncation, relevant to AC-03 playback-range semantics.
- Spec seed names differ from what the pages show: `gpt-realtime` vs documented `gpt-realtime-2.1`; Bedrock shows `amazon.nova-2-sonic-v1:0`, not "Nova 2.5 Sonic". I did not search for "GPT-Live"; it stays unconfigured.

## Next
1. Read the pages marked partially read (Nova input events, tool configuration, event flow; OpenAI VAD and Tools/MCP guides; Gemini Vertex Live page).
2. Operator attestation of retention, quotas and pricing, which probes cannot establish.
3. Live probes need credentials (spec §3 steps 1–9).

## Secondary lead: benchr model-ID directory
Source: https://benchr.org/api-model-ids (third-party "dated record, not live discovery", headed 2026-10-15; retrieved 2026-10-16). It is a **candidate list, not evidence**: spec §3 requires primary documentation plus a passing probe before any ID is configured.
| Provider | benchr ID | Cross-check |
|---|---|---|
| OpenAI | `gpt-realtime-2.1`, `gpt-realtime-2.1-mini`, `gpt-live-1` (record 2026-09-19; no ID for GPT-Live-1 mini) | `gpt-realtime-2.1` matches OpenAI docs. `-mini` and `gpt-live-1` have no primary-doc confirmation; "GPT-Live" stays unconfigured until OpenAI docs and a probe confirm it. |
| Amazon | `amazon.nova-2.5-sonic-v1:0` (record 2026-10-10, not labelled Bedrock) | Matches the Nova 2 naming pattern (`amazon.nova-2-sonic-v1:0`) but no AWS page confirms it. To be checked with `list-foundation-models`. |
| xAI | only `grok-voice-transcribe-2.0` (STT) | No conversational model listed; use `grok-voice-3.0` from xAI docs. |
| Google | `gemini-3.8-live`, `gemini-3.8-live-extended-thinking` | Match Google docs. |

## Runtime evidence: Bedrock model discovery (2026-10-16)
Command: `aws bedrock list-foundation-models --region <r>` filtered on "sonic", using the Voice_S2S role with MFA (`scripts/aws-session.sh`). This is spec §3 step 2 (model discovery) only; steps 1 and 3–9 are not yet run, so nothing is `verified`.
| Region | Returned model IDs | Lifecycle | Inference | Streaming |
|---|---|---|---|---|
| us-east-1, us-west-2, eu-north-1, ap-northeast-1 | `amazon.nova-2-sonic-v1:0`, **`amazon.nova-2-5-sonic`** | ACTIVE | ON_DEMAND | true |

- **Correction:** the Nova 2.5 ID is `amazon.nova-2-5-sonic` (no `-v1:0` suffix). The benchr ID `amazon.nova-2.5-sonic-v1:0` is wrong and must not be used; this confirms the rule to treat third-party lists as unverified.
- The four regions match the secondary blog's list. The returned ID has no version suffix, so treat it as an alias: `version_unresolved` per spec §2 until the invoke response reports metadata.

## Runtime evidence: Nova 2.5 Sonic live probe (2026-10-16)
Probe: `scripts/probe/nova_probe.mjs` (us-east-1, `amazon.nova-2-5-sonic`, SDK 3.1136.0). Report: `artifacts/phase0/nova-capability-report.json` (passes `probe validate`, expires in 7 days). Raw redacted events: `artifacts/phase0/nova-probe-*.jsonl` (audio stored as hash and length only).
- **Verified (docs + probe):** audio in (silence only), audio out, native tool call and result round trip, usage events (speech/text token deltas and totals).
- **Observed only:** 16 kHz input and 24 kHz output accepted, SPECULATIVE/FINAL transcripts, `stopReason` turn signalling (PARTIAL_TURN, END_TURN, TOOL_USE).
- **Unknown:** barge-in/cancel (needs `--speech-wav` with real speech), audio alignment, session resume (a fresh session after a clean close works), direct MCP.
- **Findings:** Nova rejects a prompt with no audio content ("must have at least one audio content") and times out after 55 s without audio, so text turns need a silence stream held open. A toolResult with an unknown toolUseId raises `ValidationException` and ends the stream. First audio arrived about 0.9-1.4 s after stream open (text-driven turn, not a latency claim). The stream returned no model version, so the alias remains `version_unresolved`.

## Runtime evidence: OpenAI live probe (2026-10-16)
Account discovery (`GET /v1/models`, HTTP 200) lists `gpt-realtime-2.1`, `gpt-realtime-2.1-mini`, `gpt-realtime-2`, `gpt-realtime-1.5`, `gpt-realtime`, `gpt-realtime-mini` and `gpt-live-1` (plus transcribe/translate variants). `gpt-live-1` is therefore real for this account, but its protocol is unprobed and it stays unconfigured.
Probe: `scripts/probe/openai_probe.mjs` (WebSocket, `gpt-realtime-2.1`). Report: `artifacts/phase0/openai-capability-report.json` (passes `probe validate`, 7-day expiry). `session.created` returned `gpt-realtime-2.1`, matching the request and docs' single snapshot.
- **Verified (docs + probe):** audio in (silence append + commit), audio out, function call and `function_call_output` round trip, output transcript delta/done, `response.done` status, usage in `response.done`.
- **Observed only:** 24 kHz PCM accepted for input and output; `response.cancel` yields `response.done` status `cancelled`.
- **Unknown:** truncate (no `conversation.item.truncated` seen on the cancelled item), server VAD and barge-in by speech, alignment, resume, MCP tools.
- **Findings:** first audio about 0.9 s after `response.create` on a text turn (not a latency claim). An unknown event type returns an `error` (`invalid_value`) and the connection stays open.

### Nova barge-in probe (2026-10-16, synthetic speech)
No microphone front end was needed: macOS `say` plus `afconvert` produced a 16 kHz mono clip ("Stop. Please stop and listen to me for a moment."), streamed into the open audio content while the model was speaking a long reply. Two runs: both showed `contentEnd stopReason=INTERRUPTED` and an in-band `{ "interrupted" : true }` text event about 0.45-0.49 s after speech began, and the speech was transcribed correctly. A second marker also appears *before* speech when a new text turn arrives mid-reply, so a marker alone must not be read as barge-in (Voice_S2S also suppresses such self-interruptions). Limits: synthetic voice, one phrase, no noise or accents; Nova offers no truncate-at-offset primitive, so the client must flush its own buffered audio and the harness must bind approved audio ranges itself.

### OpenAI barge-in probe (2026-10-16, synthetic speech)
Same `say` clip at 24 kHz streamed with `turn_detection: server_vad` while `gpt-realtime-2.1` was speaking. Two runs: `input_audio_buffer.speech_started` 0.21-0.23 s after speech began streaming, the in-flight response ended with `response.done` status `cancelled`, `conversation.item.truncate` was acknowledged with `conversation.item.truncated`, and the speech was transcribed correctly (`conversation.item.input_audio_transcription.completed`). The clip needs trailing silence for VAD to close the turn. Truncating an already-cancelled item from the manual-turn stage was acknowledged in one run and silent in another, so treat truncate acknowledgement as reliable only on the VAD path. Limits: synthetic voice, one phrase, no noise, accents or overlapping speech.
