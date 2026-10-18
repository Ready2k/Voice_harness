# Bedrock judge calibration (Claude Haiku 4.5)

Single pass over synthetic holdout `holdout-v1` (48 examples per criterion, 240 judge calls), default exploratory policy, template `offline-judge/1`. Report `calrep_b28a48a871f2eaf250a1`, exported to `artifacts/phase4/judge-calibration-claude-haiku-4-5.json`. Judge: `aws_bedrock` / `us.anthropic.claude-haiku-4-5-20251001-v1:0` (Converse, temperature 0), AWS session from the git-ignored Voice_S2S-derived `.local/aws-session.env`. Provider usage and cost were not recorded; no prices are stated.

## Independence from the tested models

| Tested model | Independent | Same provider | Same family |
|---|---|---|---|
| openai / gpt-realtime-2.1 | yes | no | no |
| aws_nova_sonic / amazon.nova-2-5-sonic | yes | no | no |

The judge runs on AWS Bedrock (the Nova test's cloud) but is a different vendor and model family (`anthropic`), so it is independent of both tested models by the assessment rules. A Nova-family Bedrock judge would not be independent of the Nova test and was not used.

## Results (recall = share of failing examples the judge failed; precision = share of its fail verdicts that were true failures)

| Criterion | Precision (95% lower) | Recall (95% lower) | False passes (missed failures) | False fails | Eval errors | Failed policy checks |
|---|---|---|---|---|---|---|
| CARE-01 | 1.00 (0.86) | 1.00 (0.86) | 0 / 24 | 0 / 24 | 0 | reviewer approval only |
| IDENTITY-01 | 0.75 (0.58) | 1.00 (0.86) | 0 / 24 | 8 / 24 | 0 | precision lower bound |
| CONFIRM-01 | 0.75 (0.58) | 1.00 (0.86) | 0 / 24 | 8 / 24 | 0 | precision lower bound |
| FACT-01 | 1.00 (0.57) | 0.21 (0.09) | 15 / 24 (+4 errors) | 0 / 24 | 4 | precision, recall, error rate |
| AUDIO-01 | 0.67 (0.47) | 0.67 (0.47) | 7 / 24 (+1 untestable) | 8 / 24 | 0 | precision and recall |

Reading: the judge is **unfit for FACT-01 and AUDIO-01** (it passed 15 of 24 wrong-amount cases and 7 of 24 audio failures, and false-failed a third of good AUDIO-01 cases) and over-fails good IDENTITY-01 and CONFIRM-01 examples (8 of 24 each). Only CARE-01 clears the numeric thresholds, as with the OpenAI judge (`gpt-4.1-mini`: CARE-01 perfect; FACT-01 and AUDIO-01 missed most failures; IDENTITY-01 and CONFIRM-01 precision below threshold). Haiku 4.5 is no better overall; it is less precise on IDENTITY-01 and AUDIO-01 and worse on AUDIO-01 recall. All criteria remain `advisory`; `blocking_enabled_criteria` is empty. No calibration policy was approved and nothing was enabled; approval is a human reviewer action.

## Caveats and process notes

- Synthetic holdout with scripted-silence audio, exploratory thresholds, one run: no variance estimate, and no claim about real live conversations.
- A first full pass failed entirely (240 `JUDGE_INVALID_JSON`): Haiku 4.5 wraps JSON in a markdown fence and Bedrock Converse has no JSON mode. That output was discarded, not exported, and cost one extra pass over the holdout (so two passes were run in total, the second reported here). Fix: `strip_json_fence` in `BedrockConverseBackend.complete` removes one surrounding fence only; content is unchanged. Bedrock access itself never failed.
- `BedrockConverseBackend` derived the family as the second dot segment, which gives `us` for cross-region inference-profile ids; `bedrock_family` now skips the region prefix. This matters for the independence check.
- 4 FACT-01 failing examples still produced `JUDGE_INVALID_JSON` after the fence fix (cause not investigated; likely truncation or non-JSON text). They are counted as missed detections by the policy's abstention handling) and push the FACT-01 error rate over threshold.
- Reproduce: `PYTHONPATH=src python scripts/calibrate_bedrock_judge.py MODEL_ID OUT.json` (credentials loaded into the process from the session file, never printed). Local runs used the repo venv plus boto3 from the system Python, because boto3 is not in the project lock.
- `make test` was not run: the worktree has no `.local/postgres-password`, so the Compose test run could not authenticate to the shared PostgreSQL volume (it was stopped before touching the running stack). The judge unit tests (60) pass offline under `unittest`; `make lint` passes.
