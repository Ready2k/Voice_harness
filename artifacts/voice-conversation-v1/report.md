# Advanced conversation comparison

Scripted synthetic caller, explicit turns where supported; no live barge-in or emotional-voice inference test. Caller audio is reused across providers and prompt conditions. Recordings preserve arrival gaps with simulated agent playout; latency is provider PCM arrival, not measured loudspeaker output. Provider transcripts are not independent accuracy verification. No tools or real banking actions are executed. Provider-specific prompts are documented starting configurations, not proven optimal tuning.

Rating files: 0. Partial ratings are included per axis; missing ratings are not zero.
Descriptive results only: repeated turns and ratings are not independent participants. No winner is inferred.

| Provider | Prompt | Completed / planned | Failed / interrupted | Rated conversations | Natural delivery | Appropriate tone | Clarity | Conversation flow | Scenario accuracy | Overall human likeness | Median first audio (ms) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| OpenAI Realtime | common | 8 / 12 | 4 | 0 | — | — | — | — | — | — | 807 |
| OpenAI Realtime | tuned | 12 / 12 | 0 | 0 | — | — | — | — | — | — | 928 |
| Amazon Nova Sonic | common | 12 / 12 | 0 | 0 | — | — | — | — | — | — | 918 |
| Amazon Nova Sonic | tuned | 12 / 12 | 0 | 0 | — | — | — | — | — | — | 874 |
| xAI Grok Voice | common | 11 / 12 | 1 | 0 | — | — | — | — | — | — | 903 |
| xAI Grok Voice | tuned | 12 / 12 | 0 | 0 | — | — | — | — | — | — | 943 |
| Google Gemini Live | common | 12 / 12 | 0 | 0 | — | — | — | — | — | — | 815 |
| Google Gemini Live | tuned | 12 / 12 | 0 | 0 | — | — | — | — | — | — | 800 |
| ElevenLabs Agents | common | 12 / 12 | 0 | 0 | — | — | — | — | — | — | 1284 |
| ElevenLabs Agents | tuned | 12 / 12 | 0 | 0 | — | — | — | — | — | — | 1292 |

## Paired prompt comparison

Tuned minus common, using only the same scenario and repetition with ratings in both conditions. Positive rating differences favour tuned prompts. Each conversation is averaged over reviewers first.

| Provider | Natural delivery | Appropriate tone | Clarity | Conversation flow | Scenario accuracy | Overall human likeness |
|---|---|---|---|---|---|---|
| OpenAI Realtime | — | — | — | — | — | — |
| Amazon Nova Sonic | — | — | — | — | — | — |
| xAI Grok Voice | — | — | — | — | — | — |
| Google Gemini Live | — | — | — | — | — | — |
| ElevenLabs Agents | — | — | — | — | — | — |

Timing medians include completed conversations only; interpret them alongside failure counts.
Negative response times mean audio arrived before caller input finished; inspect those exchanges.
Accuracy and safety require separate review of the scenario facts and independent speech assessment.
