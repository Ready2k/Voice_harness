from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class BankFixture:
    account_id: str = "acct_1001"
    owner_id: str = "customer_001"
    balance_minor: int = 123456
    identity_status: str = "unverified"


class SyntheticBank:
    """Run-scoped bank fixture. No real banking endpoint is contacted."""

    def __init__(self, fixture: BankFixture | None = None):
        self.fixture = fixture or BankFixture()
        self.read_count = 0
        self.mutation_count = 0

    def get_account_summary(self, args: dict[str, Any]) -> dict[str, Any]:
        self.read_count += 1
        return {
            "account_id": args["account_id"],
            "balance_minor": self.fixture.balance_minor,
            "currency": "GBP",
        }
