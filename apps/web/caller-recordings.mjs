import {api, el} from "./runs.mjs";

export function pcmWav(hex, rate = 24000) {
  const bytes = Uint8Array.from(hex.match(/../g) ?? [], pair => parseInt(pair, 16));
  const header = new ArrayBuffer(44);
  const view = new DataView(header);
  const label = (at, text) => [...text].forEach((c, i) => view.setUint8(at + i, c.charCodeAt(0)));
  label(0, "RIFF"); view.setUint32(4, 36 + bytes.length, true); label(8, "WAVE"); label(12, "fmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true);
  view.setUint16(34, 16, true); label(36, "data"); view.setUint32(40, bytes.length, true);
  return new Blob([header, bytes], {type: "audio/wav"});
}

export function callerRecordings(root, token, runId) {
  const urls = [];
  const button = el("button", "", "Load caller conversation recordings");
  button.type = "button";
  const content = el("div");
  root.append(button, content);
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const bundle = await api(token, `/api/v1/runs/${encodeURIComponent(runId)}/evidence`);
      const clips = bundle.events.filter(e => ["caller.utterance", "caller.agent_audio"].includes(e.type));
      content.replaceChildren(el("p", "muted", "Recorded PCM · Caller transcripts are provider-reported. Only released agent replies are included."));
      for (const event of clips) {
        const c = event.content;
        const row = el("div", "conversation-line");
        row.append(el("strong", "", event.type === "caller.utterance" ? `Caller · turn ${c.turn + 1}` : "Agent · released reply"));
        const artifact = bundle.artifacts.find(a => a.sha256 === c.pcm_sha256 && a.available && a.data_hex);
        if (artifact) {
          const url = URL.createObjectURL(pcmWav(artifact.data_hex, c.sample_rate));
          urls.push(url);
          const player = document.createElement("audio");
          player.controls = true; player.src = url; player.preload = "none";
          row.append(player);
        } else row.append(el("p", "muted", "Audio unavailable · not retained, expired, or restricted for this role."));
        content.append(row);
      }
      if (!clips.length) content.append(el("p", "muted", "No interactive caller recordings have been recorded yet."));
    } catch (error) {
      content.textContent = "Could not load recordings: " + error.message;
    } finally { button.disabled = false; }
  });
  return () => { for (const url of urls) URL.revokeObjectURL(url); };
}
