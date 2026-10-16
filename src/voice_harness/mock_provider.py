"""Credential-free implementation of the runtime-neutral adapter operations."""

import asyncio

from .execution import MockResultReceiver


class CapabilityUnsupported(ValueError):
    code = "CAPABILITY_UNSUPPORTED"


class MockProvider:
    def __init__(self):
        self.receiver = MockResultReceiver()
        self.queue = asyncio.Queue(maxsize=100)
        self.session_id = None
        self.closed = False

    def capabilities(self):
        return {
            "provider": "mock",
            "model_id": "scripted-fixture-v1",
            "profile": "controlled_fixture",
            "audio_in": True,
            "audio_out": True,
            "transcript": "scripted",
            "direct_mcp": False,
            "tool_delivery": "gateway_bridge",
            "semantic_safety": "not_measured",
        }

    async def open_session(self, config):
        self.session_id = config["session_id"]
        return {"session_id": self.session_id, "model_id": "scripted-fixture-v1"}

    async def send_audio(self, data, metadata):
        if self.closed or len(data) != metadata["sample_count"] * 2:
            raise ValueError("INVALID_MOCK_AUDIO")

    async def send_text(self, text):
        if self.closed:
            raise ValueError("SESSION_CLOSED")

    async def submit_tool_result(self, call_id, result):
        if call_id != result["call_id"]:
            raise ValueError("TOOL_RESULT_CORRELATION_FAILED")
        self.receiver.deliver(result)
        return {
            "local_send": "accepted",
            "provider_acknowledgement": "acknowledged",
            "call_id": call_id,
        }

    async def cancel_tool(self, call_id, reason):
        return {
            "call_id": call_id,
            "cancelled": False,
            "reason": "GATEWAY_OWNS_EXECUTION",
        }

    async def interrupt(self, response_id, played_until_sample, reason):
        return {
            "response_id": response_id,
            "generation_cancelled": True,
            "local_playback_stopped": False,
            "history_truncation": "scripted",
            "played_until_sample": played_until_sample,
        }

    async def request_turn(self):
        if self.closed:
            raise ValueError("SESSION_CLOSED")

    async def receive(self):
        while not self.queue.empty():
            yield await self.queue.get()

    async def close(self, reason):
        self.closed = True

    def estimate_cost(self, usage):
        return {
            "amount": None,
            "provenance": "unknown",
            "unknown_reason": "NO_PROVIDER_BILLING_IN_MOCK",
        }
