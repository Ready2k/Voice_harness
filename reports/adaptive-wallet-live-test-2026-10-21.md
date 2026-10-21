# Adaptive wallet live test — 21 October 2026

**Result: partial demonstration, full goal not passed.** Two authorised calls used the actual Twilio/OpenAI voice path. The retest reached a card question and released a contextually generated answer. Neither call obtained guidance for both banking cards. Both ended with the service saying it could not put the caller through; no account changes were requested or claimed.

Destination `0161 768 3827`, caller `01483 694454`, test `770222`, sub-test `01`. The brief contains a missing wallet that might be at work, a Barclaycard credit card, a Barclays current-account debit card and a Tesco Clubcard. Authority is discussion only. Both calls submitted the bound routing values and reconnected on the same call; cleanup was confirmed.

| Attempt | Call SID | Duration | Observed outcome |
| --- | --- | --- | --- |
| [1 — recording](recordings/adaptive-wallet-2026-10-21-attempt-1/README.md) | `CA960151377915196fef525d6a4a42669e` | 86 s | Initial generated reply withheld as stale; longer second reply delivered; service acknowledged, then fallback/end |
| [2 — recording](recordings/adaptive-wallet-2026-10-21-attempt-2/README.md) | `CA4349a781175c3f1f8c336c2287588550` | 82 s | Short opening delivered; service asked about Barclays card; generated debit-card answer delivered; then fallback/end |

## What changed after attempt 1

The first model reply was too long for a prompt requesting a few words. Review, TTS and ASR completed approximately 6.7 seconds after its final transcript, but remote speech had resumed, invalidating the proposal. A second long response was delivered after the reprompt. It volunteered unnecessary wording about not agreeing to changes.

The brief now includes a short opening: “I can't find my wallet, and I'm really worried.” Its exact PCM is synthesised and independently transcribed before dialling; actual release still requires a fresh semantic review of the current context. Only the same run's assessed audio can be reused this way. Later replies remain generated. Instructions now favour shorter replies and keep permission boundaries internal until relevant. A regression test verifies fresh review with exact same-run PCM reuse.

## Retest evidence

The opening began release approximately 3.2 seconds after the provider's final transcript of the reason-for-calling prompt. The service subsequently asked, “I understand. Do you have a Barclays card in that wallet?”

The delegate generated: “Yes. There’s a Barclays current-account debit card in there, and I’m not sure where the wallet is.” Separate semantic review allowed it, independent ASR matched the synthesised wording, and an uncleared transport playback mark followed. Release began approximately 6.4 seconds after the provider's final question transcript. These offsets measure controller events, not true end-of-remote-speech or physical telephone latency.

The service later said, “I am afraid I cannot put you through right now. Sorry about this. Please try us again later.” This final fallback appears in the independent post-call transcript but was absent from the live provider transcript. The service's reason is unknown: the evidence cannot distinguish service-side routing/state failure from timing or input interpretation without service logs. The short-opening change demonstrably reached a follow-up question; it does not prove why the later fallback occurred.

## Acceptance and limits

- Proven live: bound DTMF routing, short assessed opening, one free-form contextual follow-up, assessed/sent audio binding, recorded common-clock playback and confirmed cleanup.
- Not proven: choosing which banking card first, completing both cards, mentioning the loyalty card when relevant, handling a longer dialogue, or consistent human-like vocal naturalness. No operator question was needed in the observed branch.
- Both model findings remain null; neither recording is a completed task or independently verified platform outcome. Model review is probabilistic and playback marks do not establish physical audibility.

[Download both attempts](adaptive-wallet-live-test-2026-10-21.zip), extract and open each folder's `index.html`. Original source files and derived playback manifests verify locally. Manifests are unsigned. Credentials and tunnel logs are excluded. The operator authorised the live demo and previously requested recording sharing through the current GitHub remote.

The next investigation should correlate the retest call SID and timestamps with the automated service's logs, and assess response deadlines. Retain this failure evidence when evaluating further changes; no third call was placed in this test.
