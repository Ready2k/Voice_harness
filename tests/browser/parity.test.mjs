import test from "node:test";
import assert from "node:assert/strict";
import {disclosureRows} from "../../apps/web/bakeoff-report.mjs";

const row = (provider, extra = {}) => ({provider, model_id: "m", runs_recorded: 2, agent_llm: null, agent_llm_vendor: null,
  sampling_overrides: [], output_limits: [], stored_agent_temperature: null, sampling_conforms: true, voice: "v", asr: "INDEPENDENT_VENDOR_ASR",
  asr_vendor: "openai", turn_commit: "explicit", commit_delay_ms: "500", provider_input_rate: "24000", client_truncation: "True",
  confirmation_delivery: null, prompt_delta: [], ...extra});

test("reports without parity data render no configuration table", () => {
  assert.equal(disclosureRows({limits: {}}), null);
});

test("configuration table marks unequal settings and names the ElevenLabs agent LLM", () => {
  const d = disclosureRows({limits: {provider_disclosures: {sampling_policy: "provider_default", prompt_profile: "advanced-v1", differs: ["voice"],
    limitations: ["x"], providers: [
      row("nova-sonic-live", {voice: "amy", output_limits: ["maxTokens=1024"], prompt_delta: ["Give a direct answer first."]}),
      row("elevenlabs-live", {voice: "Amelia", agent_llm: "gemini-3.5-flash", agent_llm_vendor: "google", stored_agent_temperature: ["0"],
        sampling_conforms: false})]}}}, {"nova-sonic-live": "Amazon Nova Sonic"});
  assert.ok(d.headers.includes("Voice ≠"));
  assert.ok(d.headers.includes("Turn control"));
  assert.equal(d.rows[0][0], "Amazon Nova Sonic");
  assert.equal(d.rows[0][3], "maxTokens=1024");
  assert.match(d.rows[1][1], /LLM gemini-3\.5-flash, google/);
  assert.match(d.rows[1][2], /stored agent temperature 0.*outside policy/);
  assert.deepEqual(d.prompts[0], ["Amazon Nova Sonic", "Give a direct answer first."]);
  assert.deepEqual(d.limitations, ["x"]);
});
