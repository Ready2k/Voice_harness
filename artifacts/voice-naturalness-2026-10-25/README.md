# Blind voice listening test — 25 October 2026

This is the latest completed local matched-script recording set (`naturalness-2026-10-25b`): 48 clips covering five providers, with two ElevenLabs voices. Each voice reads six banking lines, with two additional telephone-quality clips.

## Review on another machine

Clone or pull this repository, then open `rate.html` in a browser. Keep the `clips/` folder beside it. GitHub’s file preview does not run the rating page.

Alternatively, from the repository root run:

```sh
python3 -m http.server 8000
```

Open http://localhost:8000/artifacts/voice-naturalness-2026-10-25/rate.html . No provider credentials or new API calls are needed.

Score the clips and click **Download ratings** to save your answers. In-progress ratings stay in that browser’s local storage; they do not sync between machines.

For a blind review, finish rating before opening `key.json`, `report.md`, `judge.json`, or `verify.json`: these reveal provider identities or assessment information. `key.json` maps anonymous clip IDs to providers; `judge.json` contains automated scores; `verify.json` contains transcript checks. The existing report contains automated scores only; no saved human ratings were found in this recording set.

The files were copied from the ignored local output directory without regenerating audio. The earlier `naturalness-2026-10-25` recording set remains local.
