# Full advanced bake-off — completed

All 600 scored cells completed on 26 October 2026: 40 scenarios × five providers × three repetitions.

The final assessment records 588 passes, 11 failures and one untestable outcome. Each provider has 120 completed runs. All pass the report’s critical safety and availability gates, with zero prohibited tool executions. These are results under the harness’s banking and speech controls, not proof of unrestricted native-model safety.

Nova Sonic has the fastest measured median first audio (843 ms), followed by ElevenLabs (1,982 ms), OpenAI (2,058 ms), xAI (2,136 ms) and Gemini (2,219 ms, estimated timing). The report finds no statistically clear overall winner; Nova’s lead is primarily speed. First audio is not necessarily completion of the caller’s task.

## Pauses, retries and reuse

The first pause followed exhausted ElevenLabs credits at 243 finished cells, including 241 completed underlying runs. After the user replenished credits, all five providers passed preflight and those 241 completed runs were reused unchanged. The quota classifier now recognises ElevenLabs’ quota-exceeded socket rejection; earlier failure codes and bounded retries remain in the evidence.

A later pause at 569 finished cells included six incomplete runs after local macOS caller-synthesis timeouts. Local synthesis health checks passed, including an affected phrase. An attempted restart in the restricted sandbox stopped during preflight at Google token acquisition; it launched no scored work. The restart with the required execution permissions passed all five preflights and retained all 563 then-completed runs, retrying only incomplete work. The final export retains earlier attempts and their costs where recorded. `reuse-manifest.json` verifies the original 241 and subsequent 563 completed run IDs were unchanged.

Google also had a transient token-acquisition failure before the original credit pause; an isolated refresh returned HTTP 200 without displaying or changing credentials.

## Scoring and provenance

The plan is 40 scenarios × five providers × three repetitions, with group-balanced rates and the existing six-job / two-per-provider limits. All scored runs use frozen advanced-v1 provider-specific prompts and retain the standard banking rules, full tool schemas, verification, confirmation and speech gates. Exact prompt bindings were checked against the run ledgers before export.

Before scoring began, preflight exposed a child-process module path issue and then an OpenAI policy answer that did not call the required policy tool. The path was corrected; explicit identity-policy tool routing was added for all providers; prior prompt manifests and preflight attempts were retained. A fresh five-provider preflight passed before scored work began. No scored prompts were subsequently changed.

Independent ASR and deterministic assessment ran. Optional live judges were not configured; for example, CARE-01 remains untestable where it requires a judge. This report does not establish human likeness: blind human ratings remain pending in the separate conversation comparison.

Local SQLite evidence, signing keys and tokens remain under the ignored .local/full-advanced-bakeoff-2026-10-26 directory. Portable files contain results and prompt metadata, not raw retained audio or credentials.

## Cost coverage

Provider cost reporting is incomplete: ElevenLabs did not supply priceable voice usage, and some earlier xAI/Gemini attempts lack usage. Recorded costs are not a complete invoice, including for retries and preflight. See the report for per-provider coverage.
