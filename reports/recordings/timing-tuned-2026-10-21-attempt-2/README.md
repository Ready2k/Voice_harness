# Timing tuning — attempt 2

Open index.html for playback and transcripts.

Call CAa1a74cdfac7b692b1cdf36f14c414cb5, 81 seconds, destination 0161 768 3827, test 770222 / 01 using DTMF. Controller quiet wait 250 ms, provider VAD silence 500 ms, parallel speech review/preparation enabled.

Both routing values were submitted. A reply proposed to intent metadata was withheld as stale. A generated reason for calling began release 4.57 seconds after its final prompt transcript. On a partial reprompt, another reply began 5.20 seconds after its final transcript, including time waiting for earlier playback to finish. All released speech passed semantic review and independent ASR. The service acknowledged, then said it could not put the caller through and ended. Neither banking card was discussed; the goal was not completed.

The prepared opening was not used on the actual reason-for-calling question: the model paraphrased, requiring fresh TTS/ASR. Intent-metadata suppression was added after this call and tested offline, not in another live run. The source summary also contains a blocking_question left by a denied report; that state mutation has been fixed in code, while the original evidence is preserved unchanged.

Source and playback manifests verify locally; both are unsigned. No credentials or tunnel logs are included.
