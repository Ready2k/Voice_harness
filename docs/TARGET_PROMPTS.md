# Target prompt profiles

New live evaluations default to `standardised-v2`, shared verbatim by OpenAI,
xAI, Gemini and Nova. Each adapter supplies that text in its native system
instruction field with the same tool definitions. Fixed and adaptive callers use
the selected profile; human microphone sessions currently use `standardised-v2`.
Choose the agent prompt on the Runs page or set `prompt_profile` in the run API.

The shared prompt allows brief natural conversation, asks for the security answer
without inventing a question, hides internal identifiers from speech, and requires
exact amounts and currencies from successful tool results. It contains no expected
balance or security answer. Instructions support correct behaviour; the independent
assessment and playback gate remain necessary.

`legacy-v1` preserves the original instructions for historical reproduction. Old
runs without a recorded profile predate this change; the rerun action explicitly
selects `legacy-v1` for them. New results include the selected profile in their run
configuration and the exact instructions, tool schemas and SHA-256 hashes in the
provider's `target_prompt` binding. Comparisons disclose different or missing
profiles. Earlier recordings and evidence have not been rewritten.

## Vendor optimisation follows baseline validation

There are no validated vendor-optimised profiles yet. Do not label a reformatted
prompt as optimised or combine tuned results with the standardised baseline.
Future profiles should retain the same task, authority rules and tool facts;
record their exact text, vendor, version and tuning effort; and be validated on
held-out scenarios with comparable tuning budgets. Report first-attempt outcomes
separately from any recovery attempts.

The live customer currently uses OpenAI. Adaptive conversations, different turn
controls and provider defaults remain potential confounders. Include identical
recorded caller inputs alongside adaptive tests before making comparative claims.

Prompt guidance reviewed: [OpenAI voice prompting](https://developers.openai.com/api/docs/guides/voice-prompting).
These changes have deterministic integration coverage; improved live behaviour
requires fresh recordings and has not been established by those tests.
