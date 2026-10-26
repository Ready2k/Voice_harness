# Two blind voice comparisons

Open [the comparison menu](../artifacts/voice-comparisons/index.html) after cloning or pulling the repository. GitHub previews source; open the downloaded HTML in a browser, or serve the repository with `python3 -m http.server 8000` and visit `http://localhost:8000/artifacts/voice-comparisons/`.

1. **Fixed-line delivery:** the existing 48 clips, six voices across five providers, each reading the same banking lines. Measures voice naturalness, clarity and warmth. The original recording set is unchanged.
2. **Advanced conversation:** six three-turn situations in one provider session: balance clarification, correcting an amount/name, an anxious lost-card caller, bereavement, a failed payment and remembering earlier details. Measures natural delivery, appropriate tone, clarity, conversational flow, scenario accuracy and overall human likeness.

The advanced option compares a concise common conversational prompt against a provider-specific prompt **within the same experiment**. The earlier read-aloud recordings remain a separate comparison: they are not the control for conversational performance.

## Recorded comparison

`artifacts/voice-conversation-v1/plan.json` is the frozen first plan: five providers × two prompt conditions × six scenarios × two repetitions = **120 conversations, 360 planned caller turns**. The first live run produced **115 complete conversations and five response-length failures**. Open its blind rating page to review the completed audio. Human ratings are pending. The [unblinded run notes](../artifacts/voice-conversation-v1/run-notes.md) disclose setup corrections and timing limitations. For new comparisons, setup still creates the plan and empty page without making API calls.

Each provider keeps the same voice and model across its two prompt conditions. Gemini uses its provider-default voice, disclosed in recorded configuration. Voices differ between providers, so the result compares these configured products rather than isolating model quality. Settings use the existing adapter-supported transport controls; no undocumented controls or unsupported emotional features are enabled.

The prompts follow the official guidance checked on 26 October 2026:

| Provider | Guidance applied | Source |
|---|---|---|
| OpenAI Realtime | Separate tone, response length and pacing instructions | [Voice prompting](https://developers.openai.com/api/docs/guides/voice-prompting) |
| Amazon Nova 2 Sonic | Direct, spoken-first answers; avoid lists of suggested fillers | [Voice conversation prompts](https://docs.aws.amazon.com/nova/latest/nova2-userguide/sonic-system-prompts.html) |
| xAI | Second-person instructions in the recommended section order, concise turns and varied phrasing | [Prompting guide](https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech/prompting-guide) |
| Gemini Live | Conversational system instructions through the existing Live adapter | [Live capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities) |
| ElevenLabs Agents | Separate role, goal and tone sections; platform owns turn-taking | [Prompting guide](https://elevenlabs.io/docs/eleven-agents/best-practices/prompting-guide) |

These are documentation-informed starting prompts, not demonstrated best configurations. Use a separate output folder for development/tuning; reserve a new, unseen scenario set before making claims about tuned generalisation. Do not tune on the scored comparison and call the improvement independent evidence.

## Record

Use the existing local `.venv` and provider configuration. The script loads missing environment variables from `.env` without printing their values. Nova still needs its configured AWS environment and transport runtime; Gemini needs its existing Google credentials/project; ElevenLabs needs its stored agent. The existing macOS caller synthesiser is used unless `VOICE_HARNESS_CALLER_VOICE` selects another configured backend. On another OS, configure the existing OpenAI TTS caller backend as described in `caller_voice.py`.

Before the first recording, prepare the separate ElevenLabs conversation agent:

```sh
.venv/bin/python scripts/voice_conversation.py prepare-elevenlabs --out artifacts/voice-conversation-v1
```

This creates a dedicated agent with no tools, retaining the existing harness agent's configured LLM and leaving that agent unchanged. It verifies the remote tool configuration before use and stores the new identifier in the ignored `.local/voice-conversation-elevenlabs-agent.json`. An empty tool list in a session config does not remove tools stored on the original ElevenLabs agent. Requested LLM, dedicated agent ID and empty-tools fingerprint are disclosed in the result metadata.

Start with two conversations to check provider connectivity and audio:

```sh
.venv/bin/python scripts/voice_conversation.py record --out artifacts/voice-conversation-v1 --limit 2
```

Then complete the plan:

```sh
.venv/bin/python scripts/voice_conversation.py record --out artifacts/voice-conversation-v1
```

To record across providers concurrently, add `--concurrency 5`. This opens at most one session per provider, preserves the plan's order within each provider, and records the concurrency setting alongside each result. The default remains sequential. Parallel execution can add local scheduling/network contention to response timings; disclose it when comparing timings between runs.

Recording calls paid APIs. Conversations are bounded to 45 seconds per turn including input streaming, 30 seconds of output audio per turn, and 240 seconds of exported audio per conversation. `--limit` caps attempted conversations for that invocation. No automatic provider retries occur. Every attempt checkpoints; completed recordings are skipped on resume. Failed/interrupted recordings require an explicit `--retry-failed`; retrying may incur another charge and previous attempt metadata is retained. A lock prevents concurrent recording processes. If a process is killed without cleanup, confirm it is stopped before deleting that output folder's `.record.lock` file.

`--retry-interrupted` resumes only interrupted or setup-invalidated attempts, without retrying ordinary performance failures. This is useful after correcting an integration problem. Keep the invalidation reason and prior attempt evidence; do not invalidate failures merely because their scores are poor.

Caller speech is synthesised once per turn and reused byte-for-byte across providers and conditions. The cache hashes are verified on resume. The plan is hashed and checked before recording/reporting; changing it requires a new comparison folder. Public requested model/voice, returned model where observable, prompt hash, transport configuration, provider-reported transcripts and first-audio measurements are recorded in `results.json`. Credentials and full provider receipts are excluded.

Recordings go into this tracked artifacts folder, rather than `.local/`. Commit and push the completed folder to review it on another machine. No generated files are automatically uploaded.

For a new plan:

```sh
.venv/bin/python scripts/voice_conversation.py setup --out artifacts/voice-conversation-v2 --repetitions 2
```

## Blind review and report

Open `artifacts/voice-conversation-v1/index.html`, then the blind rating page. Enter an anonymous reviewer code, listen with both audio channels enabled and rate each dimension 1–5. Caller is on the left and assistant on the right. Review notes describe expected facts and behaviour. Provider, prompt condition and transcripts are excluded from the blind page; voice recognition remains a limitation. Each page load randomises sample order.

Use several independent reviewers. Ratings are stored separately per comparison and reviewer in that browser. Download each reviewer's latest JSON export; use one export per reviewer. Avoid `plan.json`, `results.json` and `report.md` until rating is finished because they reveal identities.

```sh
.venv/bin/python scripts/voice_conversation.py report --out artifacts/voice-conversation-v1 \
  --ratings /path/to/reviewer-a.json /path/to/reviewer-b.json
```

The report shows completion/failure counts, ratings with per-axis conversation counts, median response times and paired tuned-minus-common differences. Pairing requires the same scenario/repetition and the same reviewer rating both conditions. It averages reviewers within each conversation before averaging conversations. Missing ratings are excluded, not treated as zero; mismatched plans and duplicate reviewer codes are rejected. Small-sample scores are descriptive; the report does not declare a statistically established winner.

## What this adds, and what still needs live testing

The second comparison evaluates spontaneous multi-turn responses, remembered details, corrections, appropriate wording and voice delivery. Audio is sent in real time through the live adapters. Incoming output is placed on a simulated playback timeline, preserving waiting gaps; the next caller waits until that reply would finish playing. First-audio time is measured from the last supplied caller frame to first provider PCM arrival. It excludes setup, but does not measure a real speaker/browser audio device. Negative times expose early responses and should be inspected.

For OpenAI, xAI and Gemini, explicit turns isolate prompting; Nova and ElevenLabs use native VAD. This transport difference is recorded. The test does **not** measure natural endpoint detection equally across providers, live barge-in, hesitation handling or recognising emotion from a human voice. Caller emotion is expressed in the wording; synthetic speech does not validate emotional listening. There are no tool calls, delayed-tool tests or banking actions, and provider-reported transcripts are not independent ASR evidence. Use the existing bake-off for task/safety assessment and the manual microphone page for a subsequent live interaction study. Human-rated scenario accuracy here supplements those checks.

The existing automated read-aloud judge is not applied to conversations: its rubric ignores wording and would score the wrong thing. Human reviewers are the primary assessors for this second option.
