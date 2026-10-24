"""The small OpenAI Chat Completions callers default to gpt-6-luna and survive a model that refuses an optional parameter."""

import json
import unittest

import httpx

from voice_harness.delegate_dialogue import DialogueReviewer
from voice_harness.model_simulator import OpenAIChooser
from voice_harness.openai_chat import DEFAULT_MODEL, request_body, without_rejected

CHOOSER_ENV = {"VOICE_HARNESS_LIVE_JUDGES": "openai", "OPENAI_API_KEY": "offline-test-key"}
ALLOW = {"choices": [{"message": {"content": '{"verdict":"allow"}'}}]}
BRANCH = {"choices": [{"message": {"content": '{"branch_id": "give-answer"}'}}]}


class Response:
    def __init__(self, status_code=200, text="", body=None):
        self.status_code, self.text, self._body = status_code, text, body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=httpx.Request("POST", "https://x"), response=httpx.Response(self.status_code))

    def json(self):
        return self._body


class Script:
    """A sync client whose responses are scripted in order; keeps every request body (copied, as the wire would)."""

    def __init__(self, *responses):
        self.responses, self.bodies = list(responses), []

    def post(self, url, **kw):
        self.bodies.append(json.loads(json.dumps(kw["json"])))
        return self.responses.pop(0)


class Defaults(unittest.TestCase):
    def test_the_default_is_the_named_replacement_for_gpt_4_1_mini(self):
        self.assertEqual(DEFAULT_MODEL, "gpt-6-luna")
        self.assertEqual(OpenAIChooser(environ=CHOOSER_ENV).model, "gpt-6-luna")
        self.assertEqual(OpenAIChooser(environ=CHOOSER_ENV).config["model"], "gpt-6-luna")
        self.assertEqual(DialogueReviewer({"OPENAI_API_KEY": "k"}).model, "gpt-6-luna")

    def test_overrides_still_win(self):
        self.assertEqual(OpenAIChooser("gpt-x", environ=CHOOSER_ENV).model, "gpt-x")
        self.assertEqual(DialogueReviewer({"OPENAI_API_KEY": "k", "VOICE_HARNESS_DELEGATE_REVIEW_MODEL": "gpt-y"}).model, "gpt-y")

    def test_the_request_asks_for_json_and_no_reasoning(self):
        body = request_body("m", "sys", "{}")
        self.assertEqual((body["response_format"], body["reasoning_effort"]), ({"type": "json_object"}, "none"))
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user"])


class Fallback(unittest.TestCase):
    def test_only_a_400_naming_an_optional_parameter_is_retried(self):
        body = request_body("m", "s", "{}")
        self.assertFalse(without_rejected(body, Response(500, "reasoning_effort")))
        self.assertFalse(without_rejected(body, Response(400, "bad json")))
        self.assertIn("reasoning_effort", body)
        self.assertTrue(without_rejected(body, Response(400, "Unsupported parameter: 'reasoning_effort'")))
        self.assertNotIn("reasoning_effort", body)
        self.assertIn("response_format", body)  # a required parameter is never dropped

    def test_chooser_retries_once_without_the_rejected_parameter(self):
        client = Script(Response(400, "Unrecognized request argument: reasoning_effort"), Response(body=BRANCH))
        self.assertEqual(OpenAIChooser(environ=CHOOSER_ENV, client=client)({"persona": "p"}), "give-answer")
        self.assertEqual(len(client.bodies), 2)
        self.assertIn("reasoning_effort", client.bodies[0])
        self.assertNotIn("reasoning_effort", client.bodies[1])
        self.assertEqual(client.bodies[1]["model"], "gpt-6-luna")

    def test_chooser_does_not_retry_an_unrelated_400(self):
        client = Script(Response(400, "invalid api key"))
        with self.assertRaises(httpx.HTTPStatusError):
            OpenAIChooser(environ=CHOOSER_ENV, client=client)({"persona": "p"})
        self.assertEqual(len(client.bodies), 1)


class Reviewer(unittest.IsolatedAsyncioTestCase):
    async def test_reviewer_retries_once_without_the_rejected_parameter(self):
        seen = []

        def handler(request):
            seen.append(json.loads(request.content))
            if "reasoning_effort" in seen[-1]:
                return httpx.Response(400, text="Unsupported parameter: reasoning_effort")
            return httpx.Response(200, json=ALLOW)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reviewer = DialogueReviewer({"OPENAI_API_KEY": "offline-test-key"}, client)
            self.assertEqual(await reviewer.review({"proposal": "Example"}), "allow")
        self.assertEqual([("reasoning_effort" in b) for b in seen], [True, False])
        self.assertEqual(seen[0]["model"], "gpt-6-luna")

    async def test_reviewer_still_fails_closed_on_an_unrelated_400(self):
        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(400, text="invalid request")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaisesRegex(ValueError, "DIALOGUE_REVIEW_HTTP_400"):
                await DialogueReviewer({"OPENAI_API_KEY": "offline-test-key"}, client).review({})
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
