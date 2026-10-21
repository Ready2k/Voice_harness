# Lost-wallet demo recording — 21 October 2026

Open [index.html](index.html) locally. **Combined playback** plays both voices on one timeline, with a waveform and timestamps you can click to seek. **Original tracks** retains the untouched received/sent files. **Evidence** explains alignment and integrity.

For one download, use the [ZIP package](../../lost-wallet-recording-2026-10-21.zip), extract it and open `index.html`.

- [Remote/service audio](remote.wav) — 102.06 seconds.
- [Delegate/sent audio](sent.wav) — 7.8 seconds.
- [Combined conversation](conversation.wav) — approximately 106.3 seconds, both voices mixed to mono.
- [Combined stereo](conversation-stereo.wav) — service left, delegate right, same timeline.
- [Independent remote transcript](remote-transcript.txt).
- [Summary](summary.json), [event ledger](events.jsonl), [original manifest](manifest.json).

**Alignment is estimated for this older call.** It did not save per-frame timestamps. The combined files reconstruct timing from stream/reconnection and speech-release events. Approved PCM lengths were recovered from matching cached audio hashes; all source samples are preserved. Small offsets may remain. Missing audio during reconnections is represented by silence and cannot be recovered. The original `remote.wav` and `sent.wav` remain concatenated tracks; starting those two originals at zero will not synchronise them.

[alignment.json](alignment.json) describes sample placement. A separate [playback-manifest.json](playback-manifest.json) binds the derived page and combined WAVs to their original source hashes. The original manifest, ledger and source WAVs are unchanged. Future pilot calls save an original-manifest-bound `audio-timing.json` and automatically generate capture-clock-aligned playback; physical remote playback latency remains unknown.

Call: `CA7b5b4110b561f43a873baa5ddbab7215`. Destination: `0161 768 3827`; auto-test `770222`; sub-test `01`; duration 106 seconds. The delegate said the wallet statement and supplied Premier/business banking description. The service asked which card to sort first, then eventually ended without completing the task. No card/account change is claimed.

This is the fixed-phrase pilot, not yet an adaptive goal-based delegate. See [adaptive scope](../../../docs/DELEGATE_ADAPTIVE_SCOPE.md) and [full attempt report](../../delegate-lost-wallet-2026-10-21.md). The operator explicitly requested sharing/committing this demo recording. Credentials, .env and tunnel logs are excluded.

The original manifest and ledger can be verified using `voice_harness.telephony_demo.verify_output` with this directory. Derived playback can be verified with `voice_harness.call_review.verify_review`. Both manifests are unsigned; these are integrity checks, not independent platform assurance. README is outside both manifests; index.html is bound by the derived playback manifest.
