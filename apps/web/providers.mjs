// One display name per provider. The technical ID stays available on hover.

export const PROVIDER_NAMES = {
  "openai-realtime-live": "OpenAI Realtime",
  "nova-sonic-live": "Amazon Nova Sonic",
  "xai-voice-live": "xAI Grok Voice",
  "gemini-live": "Google Gemini Live",
  "mock-banking-v1": "Scripted mock",
};

// Model IDs and families seen in run records resolve to the same display name as their configuration.
const ALIASES = {
  "amazon.nova-2-5-sonic": "nova-sonic-live", "aws_nova_sonic": "nova-sonic-live", "nova": "nova-sonic-live",
  "gemini-3.8-live": "gemini-live", "google_gemini_live": "gemini-live",
  "gpt-realtime-2.1": "openai-realtime-live", "gpt-realtime": "openai-realtime-live", "openai_realtime": "openai-realtime-live",
  "grok-voice-3.0": "xai-voice-live", "grok-voice-latest": "xai-voice-live", "grok-voice": "xai-voice-live", "grok-transcribe": "xai-voice-live", "xai": "xai-voice-live",
  "gemini-live": "gemini-live", "gemini": "gemini-live", "google": "gemini-live",
  "openai": "openai-realtime-live", "xai_grok_voice": "xai-voice-live",
};

/** Display name for a provider configuration ID, model ID or family; unknown or empty values fall back to themselves. */
export function providerName(id) {
  if (id == null || id === "") return "Unknown provider";
  const lower = String(id).toLowerCase();
  const key = PROVIDER_NAMES[id] ? id : ALIASES[lower] ?? (/^gpt-realtime/.test(lower) ? "openai-realtime-live" : /^grok-/.test(lower) ? "xai-voice-live" : /^gemini-[\d.]+-live|^gemini-live/.test(lower) ? "gemini-live" : /^amazon\.nova/.test(lower) ? "nova-sonic-live" : undefined);
  return PROVIDER_NAMES[key] ?? String(id);
}

/** <span class="provider-name" title="technical id"> with the display name. */
export function providerEl(id) {
  const s = document.createElement("span");
  s.className = "provider-name";
  s.textContent = providerName(id);
  if (id) s.title = String(id);
  return s;
}
