---
name: quant-stock-polling-trader
description: Plan, validate, poll, and reconcile deterministic Korean and U.S. stock orders through broker adapters after a frozen quant-stock-technical screen. Use when the user wants a first-hour entry window, Toss Securities or Korea Investment & Securities API integration, paper/shadow/live promotion gates, cross-broker exposure snapshots, position sizing, order-state recovery, or fail-closed automated trading. Never infer missing risk, account, fee, FX, calendar, fractional-share, or broker semantics.
---

# Quant Stock Polling Trader

Keep model calculation and broker mutation separate. Accept only a complete
`qta-screen/v1` artifact from `$quant-stock-technical`.

## Workflow

1. Read `references/execution-contract.md`.
2. Read `references/broker-contracts.md` for the chosen adapter.
3. Read `references/policy-examples.md` when preparing frozen JSON inputs.
4. Freeze the screen, account snapshot, cross-broker exposure snapshot, risk
   policy, trading calendar, and execution policy before the session.
5. Run `scripts/plan_orders.py` to create a side-effect-free plan.
6. Run `scripts/run_session.py` in `paper` or `shadow` mode first.
7. Run `scripts/reconcile.py` after every session and after any timeout,
   restart, partial fill, cancel, or replace.
8. Return `BLOCKED` when a required value, capability, or reconciliation fact
   is missing. Never compensate with a guessed default.

Run all bundled self-tests after changing a script:

```bash
python3 scripts/execution_core.py --self-test
python3 scripts/broker_adapters.py --self-test
python3 scripts/plan_orders.py --self-test
python3 scripts/run_session.py self-test
python3 scripts/reconcile.py --self-test
```

## Safety boundary

- Treat the first hour as a new-entry window, not an automatic liquidation
  deadline. Continue protection and reconciliation after it closes.
- Persist the local intent before any broker mutation.
- Treat an HTTP timeout as `UNKNOWN`, not as rejection.
- Do not retry an ambiguous mutation unless the adapter proves a safe,
  body-identical idempotent replay.
- Use settled unborrowed cash only. Keep lending limits, margin, shorting,
  automatic FX, and existing-position liquidation disabled unless a later
  signed policy explicitly enables and validates them.
- Keep secrets in environment variables or an OS secret store. Never print,
  persist, package, or ask the user to paste credentials into chat.
- Keep live mode fail-closed until account allowlisting, contract probes,
  paper/shadow evidence, an external single-writer lock, and a matching arm
  artifact all pass.

An Agent Skill is not the scheduler. Use a deterministic process supervised by
`launchd` or `systemd`; do not put an LLM in the polling or order path.
