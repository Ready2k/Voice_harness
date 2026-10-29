import os, json, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from voice_harness.judges import calibration, holdout
from voice_harness.judges.backends import OpenAIBackend, BedrockConverseBackend, make_request, JudgeError
from voice_harness.judges.oracle import build_oracle

for path in [Path(".env"), Path(".local/aws-session.env")]:
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip().removeprefix("export ")
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ[k] = v.strip().strip("\"'")
os.environ["VOICE_HARNESS_LIVE_JUDGES"] = "openai,bedrock"
out = Path("artifacts/judge-calibration-2026-10-28")
out.mkdir(exist_ok=True)
data = holdout.holdout()
models = {
    "openai-gpt-6-sol": lambda: OpenAIBackend("gpt-6-sol"),
    "bedrock-haiku-4-5": lambda: BedrockConverseBackend("us.anthropic.claude-haiku-4-5-20251001-v1:0"),
}


# Each request gets a separate backend so call metadata cannot race. Responses are
# replayed once through the unchanged evaluate_holdout pipeline, without more API calls.
def run(name, factory):
    base = factory()
    cache = {}
    records = []
    start = time.time()

    def call(ex):
        b = factory()
        oracle = build_oracle(
            ex["events"],
            scenario=ex.get("scenario"),
            fixture=ex["fixture"],
            policy_version="strict-v1",
            criteria=[ex["criterion_id"]],
        )
        req = make_request("offline", oracle.payload, b.config)
        try:
            raw = b.complete(req)
            error = None
        except JudgeError as e:
            raw = None
            error = e.code
        except Exception:
            raw = None
            error = "JUDGE_BACKEND_FAULT"
        return req.user_payload, {
            "example_id": ex["example_id"],
            "raw": raw,
            "error": error,
            "last_call": getattr(b, "last_call", {}),
        }

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(call, ex) for ex in data["examples"]]
        for n, f in enumerate(as_completed(futures), 1):
            key, r = f.result()
            cache[key] = r
            records.append(r)
            if n % 20 == 0:
                print(name, n, "/240", flush=True)

    class Replay:
        config = base.config

        def complete(self, req):
            r = cache[req.user_payload]
            self.last_call = r["last_call"]
            if r["error"]:
                raise JudgeError(r["error"])
            return r["raw"]

    policy = calibration.default_policy(calibration.fingerprint(base.config))
    report = calibration.evaluate_holdout(Replay(), policy, approved=False, holdout_data=data)
    (out / (name + ".json")).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    (out / (name + "-responses.json")).write_text(
        json.dumps(sorted(records, key=lambda r: r["example_id"]), indent=2, sort_keys=True) + "\n"
    )
    (out / (name + "-policy.json")).write_text(
        json.dumps(policy.model_dump(), indent=2, sort_keys=True) + "\n"
    )
    print(name, "DONE", round(time.time() - start), report["enablement"], flush=True)


with ThreadPoolExecutor(max_workers=2) as pool:
    for f in as_completed([pool.submit(run, n, fac) for n, fac in models.items()]):
        f.result()
