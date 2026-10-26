# Voice naturalness (matched script)

Same 6 banking lines per provider, default voice unless named; human = blind ratings, judge = gpt-audio-1.5 on the same clips (1-5, higher is better). Judge and OpenAI share a vendor.

| Provider | Condition | Clips | Human natural | Human clarity | Human warmth | Judge natural | Judge clarity | Judge warmth |
|---|---|---|---|---|---|---|---|---|
| OpenAI Realtime | clean | 6 |   -  |   -  |   -  | 4.67 | 5.00 | 4.42 |
| OpenAI Realtime | phone | 2 |   -  |   -  |   -  | 4.50 | 5.00 | 4.50 |
| Amazon Nova Sonic | clean | 6 |   -  |   -  |   -  | 4.75 | 5.00 | 4.58 |
| Amazon Nova Sonic | phone | 2 |   -  |   -  |   -  | 4.50 | 5.00 | 4.25 |
| xAI Grok Voice | clean | 6 |   -  |   -  |   -  | 4.67 | 5.00 | 4.42 |
| xAI Grok Voice | phone | 2 |   -  |   -  |   -  | 5.00 | 5.00 | 4.50 |
| Google Gemini Live | clean | 6 |   -  |   -  |   -  | 4.50 | 4.92 | 4.50 |
| Google Gemini Live | phone | 2 |   -  |   -  |   -  | 5.00 | 5.00 | 4.50 |
| ElevenLabs (George) | clean | 6 |   -  |   -  |   -  | 4.92 | 5.00 | 4.50 |
| ElevenLabs (George) | phone | 2 |   -  |   -  |   -  | 4.50 | 5.00 | 4.25 |
| ElevenLabs (second voice) | clean | 6 |   -  |   -  |   -  | 4.58 | 5.00 | 4.50 |
| ElevenLabs (second voice) | phone | 2 |   -  |   -  |   -  | 4.50 | 5.00 | 4.25 |

Clips rated by both a person and the judge: 0
- naturalness: Spearman agreement n/a
- clarity: Spearman agreement n/a
- warmth: Spearman agreement n/a
