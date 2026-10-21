# Lost-wallet demo recording — 21 October 2026

Open [index.html](index.html) locally for playback and the event timeline, or download the WAV files directly:

For one download, use the [ZIP package](../../lost-wallet-recording-2026-10-21.zip), extract it and open `index.html`.

- [Remote/service audio](remote.wav) — 102.06 seconds.
- [Delegate/sent audio](sent.wav) — 7.8 seconds.
- [Independent remote transcript](remote-transcript.txt).
- [Summary](summary.json), [event ledger](events.jsonl), [original manifest](manifest.json).

The files are separate tracks, not a synchronised mixed recording. Captured remote segments and sent utterances are concatenated; consult stream-gap events for missing intervals. The playback page's event times reflect event arrival, not precise audio alignment.

Call: `CA7b5b4110b561f43a873baa5ddbab7215`. Destination: `0161 768 3827`; auto-test `770222`; sub-test `01`; duration 106 seconds. The delegate said the wallet statement and supplied Premier/business banking description. The service asked which card to sort first, then eventually ended without completing the task. No card/account change is claimed.

This is the fixed-phrase pilot, not yet an adaptive goal-based delegate. See [adaptive scope](../../../docs/DELEGATE_ADAPTIVE_SCOPE.md) and [full attempt report](../../delegate-lost-wallet-2026-10-21.md). The operator explicitly requested sharing/committing this demo recording. Credentials, .env and tunnel logs are excluded.

The original manifest and ledger can be verified using `voice_harness.telephony_demo.verify_output` with this directory. The manifest is unsigned; this is local integrity checking, not independent platform assurance. README and index.html are derived review aids outside the original manifest.
