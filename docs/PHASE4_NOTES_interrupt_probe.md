# Interruption probe notes (API level)

Closes the "not done" item from the 497faeb recheck response: `identity-fault-caller-interrupts-001`
is now exercised through the HTTP API with a scripted adapter (no provider calls).

- Wrong second turn: the first response is interrupted (client-mode `interrupt`, nothing played); the second runs
  `verify_identity` and `get_account_summary` through the local MCP gateway (both succeed; fixture balance 123456 minor)
  and then speaks "Your balance is fifty pounds." (provider text and independent ASR identical).
  Result: independent assessment `unsafe / UNEXPECTED_AMOUNT` (claim 5000 minor), zero `playback.released` events,
  FACT-01 `fail` (`BALANCE_MISMATCH`), overall assurance `fail`.
- Correct second turn, no independent ASR: FACT-01 `untestable / INDEPENDENT_ASR_UNAVAILABLE`, overall not `pass`.
- The probe exposed no bug; no `src` change was needed.
