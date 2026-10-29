# Phase 6 S1: provider transport survey (29 October 2026)

Stage S1 of `PHASE6_SPEC.md`. Desk research only: no provider calls, no spend. Every claim below cites the page it came from.
Anything not found in a provider's own documentation is marked **not documented** and is not guessed.

Method limits: pages were read on 2026-10-29 through a fetch tool that summarises each page, so quotes are as the tool
returned them. Re-check the exact wording before quoting this note externally. Where the only source was a search result
page, it is marked **secondary**. No WebRTC-specific pricing or terms statement was found on any page read; "terms" below
therefore means auth model and what the documentation says about who may be the WebRTC peer.

## Result

| Provider (harness config) | Documented WebRTC path | Verdict |
|---|---|---|
| OpenAI (`openai-live`, `openai-realtime-live`) | Yes, first-party | **In scope, conditional** |
| ElevenLabs (`elevenlabs-live`) | Yes, first-party (JavaScript SDK) | **In scope, conditional** |
| xAI (`xai-voice-live`) | No. WebSocket only; the WebRTC demo relays over WebSocket | Out of scope |
| Google Gemini (`gemini-live`) | No native path. WebRTC only through third-party partners | Out of scope |
| Amazon Nova Sonic (`nova-sonic-live`) | No. HTTP/2 bidirectional stream; WebRTC only through frameworks | Out of scope |

So two providers (three harness configs, because OpenAI has two) have a documented path, fewer than the three the spec
anticipated. "Conditional" means: the docs show the browser as the WebRTC peer. The harness needs to be the peer (spec:
terminate the provider leg in the adapter), and **no page read documents a server acting as the peer**. S3 must show a
server-side peer can complete the documented handshake before S4 is planned for these providers.

## OpenAI

Sources:
- Realtime overview, <https://developers.openai.com/api/docs/guides/realtime>: "The session connects over WebRTC in the browser or WebSocket on the server." Also: "Use `/v1/realtime/calls` when establishing WebRTC sessions." Links the WebRTC guide and the WebSocket guide.
- WebRTC guide, <https://developers.openai.com/api/docs/guides/voice-webrtc?api=realtime>.

Findings from the WebRTC guide:
- **Realtime, unified interface:** the server posts the browser's SDP to `/v1/realtime/calls` (multipart, with session config), authenticated by the standard API key. Described as "simpler setup and faster connections" but it "puts your application server in the critical path for session initialization."
- **Realtime, ephemeral key:** the server mints a key at `/v1/realtime/client_secrets`; the browser posts its SDP to `https://api.openai.com/v1/realtime/calls` with that key as Bearer.
- **GPT-Live (`gpt-live-1` family):** the browser's SDP offer goes to the app server, which calls `POST /v1/live/sessions` with the project key; the answer comes back as `transport.sdp`.
- **Auth model:** standard keys on the server only ("you only use standard OpenAI API keys on the server, not in the browser"); `OpenAI-Safety-Identifier` set on the server request when minting a client secret.
- **Media:** browser adds microphone tracks; remote audio arrives on the `track` event. Codec is **not named**; "WebRTC negotiates the audio format through SDP, so omit `audio.format` from session configuration." Events travel on a data channel labelled `oai-events`; audio never travels on it.
- **Server as peer: not documented.** The guide uses the server for SDP relay and key minting only.

What the harness would terminate: an RTP audio track (codec chosen by SDP negotiation, so the sample rate and codec must be recorded in the run binding) plus the `oai-events` data channel carrying the same JSON events the WebSocket adapter already parses. Turn control: the data channel carries session commands, but whether explicit commit/`response.create` is available in the same form as on the WebSocket was not confirmed. See S2 on matched turn control.

## ElevenLabs

Sources:
- JavaScript SDK, <https://elevenlabs.io/docs/eleven-agents/libraries/java-script.md>: "Voice conversations use WebRTC and text-only conversations use WebSocket by default"; `connectionType: 'webrtc'` or `'websocket'` can be set explicitly. "In WebRTC mode the input format and sample rate are hardcoded to `pcm` and `48000`" (same for output). A conversation token is needed for WebRTC; a signed URL for WebSocket; both need "a dedicated endpoint to your server."
- Token API, <https://elevenlabs.io/docs/eleven-agents/api-reference/conversations/get-webrtc-token.md> (search-result summary, **secondary** for parameters): `GET https://api.elevenlabs.io/v1/convai/conversation/token`, required `agent_id`, returns a token and conversation id; regional hosts exist. The page itself says only "Get a WebRTC session token for real-time communication." It does not state the auth header and does not mention LiveKit.
- WebSocket library, <https://elevenlabs.io/docs/eleven-agents/libraries/web-sockets>: server obtains a signed URL; "Never expose your ElevenLabs API key on the client side." This page does not cover WebRTC.
- Python SDK, <https://elevenlabs.io/docs/eleven-agents/libraries/python.md>: **no statement** on WebRTC or transport. Only the JavaScript SDK documents it.

Consequences: the documented WebRTC peer is the JavaScript client, so a Python-side peer is **not documented** (same condition as OpenAI, stronger because the Python SDK is silent). WebRTC mode fixes audio at 48 kHz PCM, different from the harness's current ElevenLabs settings, so any difference between arms includes resampling and must be disclosed. What the server side must terminate: the media connection the token points to; the underlying media server was not named in the pages read, so it is not assumed.

## xAI: out of scope

- Voice guide, <https://docs.x.ai/docs/guides/voice>: WebSocket is documented ("Build real-time, speech-to-speech voice agents over WebSockets"; `wss://api.x.ai/v1/realtime?model=grok-voice-latest`). WebRTC appears only as a link to a "WebRTC Agent" demo.
- Demo README, <https://github.com/xai-org/xai-cookbook/tree/main/examples/voice-agent-webrtc>: Browser (WebRTC) to the developer's own Node.js server (relay) to xAI over WebSocket. The WebRTC leg ends at the relay, so the xAI leg is WebSocket.
- Ephemeral tokens, <https://docs.x.ai/developers/model-capabilities/audio/ephemeral-tokens>: `POST https://api.x.ai/v1/realtime/client_secrets`, transport is WebSocket.
- A third-party tutorial (DataCamp, via search, **secondary**) pairs Grok with LiveKit. That is a third-party platform, not an xAI endpoint.

Nothing documents WebRTC to xAI itself. The demo shows the pattern that would have a WebRTC leg to the harness and WebSocket to the provider, which measures nothing about the provider leg.

## Google Gemini: out of scope

- Live API guide, <https://ai.google.dev/gemini-api/docs/live>: protocol listed as "Stateful WebSocket connection (WSS)"; both server-to-server and client-to-server use WebSockets; ephemeral tokens recommended for client-side use. WebRTC "appears only in the partner section", which refers to third-party integrations that support the Live API "over WebRTC or WebSockets."
- Vertex AI overview (search, **secondary**), <https://docs.cloud.google.com/vertex-ai/generative-ai/docs/live-api>: names Daily, LiveKit and Voximplant as partners.

The partner path inserts a third party that terminates WebRTC and talks to Google itself, so it changes who is in the path, not only the transport. It is excluded. The docs read describe the general Live API; the page for the configured `gemini-3.8-live` model was not separately checked, but no WebRTC statement was found on the pages for Gemini generally.

## Amazon Nova Sonic: out of scope

- Integrations, <https://docs.aws.amazon.com/nova/latest/nova2-userguide/sonic-integrations.html>: Nova 2 Sonic is reached through the bidirectional streaming API. WebRTC appears only in framework integrations. LiveKit: clients connect over WebRTC to a LiveKit server and a LiveKit agent calls Nova through "the bidirectional streaming API." Pipecat: "Built-in integration with Daily and other WebRTC providers." AgentCore: clients connect "via WebSocket with SigV4 authentication."
- Bidirectional streaming API (search, **secondary**), <https://docs.aws.amazon.com/nova/latest/userguide/speech-bidirection.md>: `InvokeModelWithBidirectionalStream`, HTTP/2. The primary page was not fetched.

As with Gemini, every WebRTC route ends at a framework server that calls Nova over the same bidirectional stream the harness already uses. No faster path to the model is documented.

## Other lower-latency paths

The spec also asks about "other lower-latency" paths. None was found in the pages read for any provider. OpenAI's overview mentions the unified `/v1/realtime/calls` setup as faster to connect, which affects connection setup, not first-audio latency within a turn. SIP/telephony paths exist for some providers but are out of scope (`PHASE6_SPEC.md`).

## Implications for S3 and S4

- Candidate S4 set: `openai-live`, `openai-realtime-live`, `elevenlabs-live`, only if S3 shows a server-side peer completes each documented handshake. If it cannot for a provider, that provider drops out; the study does not fall back to a browser peer, because the spec keeps WebRTC out of the harness-to-browser leg.
- Budget (open question 3): per-run cost for these three configs should come from the existing cost export; this note makes no cost claim.
- Fairness: three of six providers are covered. Results are a labelled separate study (spec) and cannot rank providers.
- Open points for the operator: whether to also approve a vendor-supplied server SDK path if one appears later (none was found in the pages read).
