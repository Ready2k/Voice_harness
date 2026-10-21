# Timing tuning — attempt 1

Open index.html for playback and transcripts.

Call CA49eee292ff10a106e5c661dc31ac4953, 23 seconds, destination 0161 768 3827, test 770222 / 01 using DTMF. Controller quiet wait 250 ms, provider VAD silence 500 ms, parallel speech review/preparation enabled.

The model reacted to a routing announcement before the sub-test request completed, proposed speech before routing was complete (denied), then incorrectly reported access_denied. The independent transcript shows the actual service was asking for the sub-test. No delegate speech was sent. Cleanup confirmed. This failed run is preserved; guards were then added to ignore routing announcements and reject unsupported access-denial claims.

Source and playback manifests verify locally; both are unsigned. No credentials or tunnel logs are included.
