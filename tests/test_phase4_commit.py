"""Phase 4 / AC-15: synthetic commit (cancel_payment) proposed by an adapter, through the real MCP gateway."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from voice_harness.contracts import RunConfig
from voice_harness.engine import Engine
from voice_harness.mcp_gateway import gateway
from voice_harness.storage import Database

VERIFY = {"challenge_id": "challenge_001", "answer": "blue"}


class SyntheticCommit(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Database(os.environ.get("HARNESS_TEST_DATABASE_URL", str(Path(directory.name) / "t.sqlite")))
        self.engine = Engine(self.database)
        self.run = self.engine.create(RunConfig().model_dump())
        self.context = self.engine.context(self.run)

    def types(self):
        return [e["type"] for e in self.database.events(self.run)]

    def confirmed(self, observation="obs-1"):
        """Trusted channel: the harness (not the model) presents the proposal and observes the caller's yes."""
        request, proposal_hash = self.engine.present_cancellation(self.run, "payment_001")
        return self.engine.confirm(self.run, self.context, request, observation, proposal_hash, "affirm")

    def test_model_proposed_commit_is_denied_until_identity_and_confirmation(self):
        async def go():
            async with gateway(self.engine, self.run) as bank:
                bare = await bank.tool("c0", "cancel_payment", {"payment_id": "payment_001", "confirmation_id": "forged"})
                await bank.tool("c1", "verify_identity", VERIFY)
                forged = await bank.tool("c2", "cancel_payment", {"payment_id": "payment_001", "confirmation_id": "forged"})
                accepted = self.confirmed()
                args = {"payment_id": "payment_001", "confirmation_id": accepted["confirmation_id"]}
                done = await bank.tool("c3", "cancel_payment", args)
                replay = await bank.tool("c4", "cancel_payment", args)
                return bare, forged, done, replay

        bare, forged, done, replay = asyncio.run(go())
        self.assertNotEqual(bare["status"], "success")
        self.assertNotEqual(forged["status"], "success")
        self.assertEqual(done["status"], "success")
        self.assertEqual(replay["structured_content"]["status"], "not_cancellable")  # already committed; no second consumption
        self.assertEqual(self.types().count("confirmation.consumed"), 1)
        self.assertEqual(self.database.get(self.run)["payments"]["payment_001"]["status"], "cancelled")

    def test_changed_payment_after_presentation_invalidates_consent(self):
        asyncio.run(self._verify())
        accepted = self.confirmed()
        with self.database.transaction(self.run) as tx:
            tx.state["payments"]["payment_001"]["amount_minor"] += 1
        async def go():
            async with gateway(self.engine, self.run) as bank:
                return await bank.tool("c5", "cancel_payment", {
                    "payment_id": "payment_001", "confirmation_id": accepted["confirmation_id"]})
        result = asyncio.run(go())
        self.assertNotEqual(result["status"], "success")
        self.assertNotEqual(self.database.get(self.run)["payments"]["payment_001"]["status"], "cancelled")
        self.assertEqual(self.types().count("confirmation.consumed"), 0)

    def test_commit_survives_restart_without_double_consumption(self):
        asyncio.run(self._verify())
        accepted = self.confirmed()
        args = {"payment_id": "payment_001", "confirmation_id": accepted["confirmation_id"]}

        async def go():
            async with gateway(self.engine, self.run) as bank:
                return await bank.tool("c6", "cancel_payment", args)

        first = asyncio.run(go())
        restarted = Engine(Database(self.database.url))
        again = restarted.tool(self.run, restarted.context(self.run), "c6", "cancel_payment", args)
        self.assertEqual(first["status"], "success")
        self.assertEqual(again["status"], first["status"])
        self.assertEqual(self.types().count("confirmation.consumed"), 1)

    async def _verify(self):
        async with gateway(self.engine, self.run) as bank:
            await bank.tool("v", "verify_identity", VERIFY)


if __name__ == "__main__":
    unittest.main()
