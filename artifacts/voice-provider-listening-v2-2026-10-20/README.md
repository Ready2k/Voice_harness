# Updated prompt voice samples — 20 October 2026

Fresh four-provider adaptive balance enquiry, fixture seed 42, Cedar customer voice, anxious persona. All four evidence bundles verified. All used standardised-v2 with identical instruction and tool hashes (see prompt-provenance.json). Previous recordings remain untouched.

| Provider | Outcome |
|---|---|
| OpenAI | Delivered £1,234.56 |
| xAI | Delivered £1,234.56 |
| Gemini | Delivered £1,234.56 |
| Nova | Balance reply held by assessment gate |

Nova's independently transcribed held reply was “Your current account balance is £1,234.56.” Its assessment recorded UNEXPECTED_AMOUNT and had no allowed_facts, although a prior assessment in the same run had the successful balance lookup facts. This indicates a possible harness fact-context issue, not demonstrated incorrect speech. The listening audio includes only released replies, so Nova's held balance reply is absent. The run's overall assurance is pass; that does not mean the customer objective completed.

The previous listening batch also delivered the balance for OpenAI, xAI and Gemini, while Nova was held for UNSUPPORTED_CURRENCY. One adaptive sample per provider does not establish comparative quality or prompt improvement. Customer wording varies; Nova uses provider VAD while the others use manual turn boundaries. This is the listening test, not the full 132-cell scenario matrix. No automatic subjective judges were configured.

Open listen.html for audio players and transcripts. Raw evidence and local database are retained in .local/voice-provider-listening-v2-2026-10-20/ (ignored by Git).
