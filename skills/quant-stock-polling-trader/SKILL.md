---
name: quant-stock-polling-trader
description: Plan, validate, poll, and reconcile deterministic Korean and U.S. stock orders through broker adapters after a frozen quant-stock-technical screen. Use when the user wants a first-hour entry window, Toss Securities or Korea Investment & Securities API integration, paper/shadow/live promotion gates, cross-broker exposure snapshots, position sizing, order-state recovery, or fail-closed automated trading. Never infer missing risk, account, fee, FX, calendar, fractional-share, or broker semantics.
---

# Quant Stock Polling Trader

Keep model calculation and broker mutation separate. Accept only a complete
`qta-screen/v1` or exchange-aware `qta-screen/v2` artifact from
`$quant-stock-technical`. Require `qta-exposure-snapshot/v2` with explicit
exchange membership for a v2 screen. The current v2 universe is qualified from
KIS stock-master snapshots, so v2 planning is KIS-only; block Toss until a
separate Toss-qualified universe snapshot contract exists.

## Workflow

1. Read `references/execution-contract.md`.
2. Read `references/broker-contracts.md` for the chosen adapter.
3. Read `references/policy-examples.md` when preparing frozen JSON inputs.
4. Freeze the screen, account snapshot, cross-broker exposure snapshot, risk
   policy, trading calendar, and execution policy before the session. Run
   `scripts/freeze_market_session.py` to bind the local calendar file and its
   hashes before embedding the result in the execution policy. Put only the
   HMAC-derived `broker_account_identity_hash` in the account snapshot; keep
   the account number, product code, account sequence, and binding key in the
   secret environment.
5. Run `scripts/plan_orders.py` to create a side-effect-free plan.
6. Generate the nonsecret account-identity receipt, create a plan-, account-,
   mode-, and trading-date-bound arm artifact, then start
   `scripts/run_session.py` before its 30-second pre-open authentication
   warm-up in `paper` or `shadow` mode first.
7. Run `scripts/reconcile.py` after every session and after any timeout,
   restart, partial fill, cancel, or replace.
8. Return `BLOCKED` when a required value, capability, or reconciliation fact
   is missing. Never compensate with a guessed default.

Run all bundled self-tests after changing a script:

```bash
python3 scripts/execution_core.py --self-test
python3 scripts/freeze_market_session.py --self-test
python3 scripts/broker_adapters.py --self-test
python3 scripts/plan_orders.py --self-test
python3 scripts/run_session.py self-test
python3 scripts/reconcile.py --self-test
```

## Safety boundary

- Treat the first hour as a new-entry window, not an automatic liquidation
  deadline. Continue protection and reconciliation after it closes.
- Enforce `09:00–10:00 Asia/Seoul` for Korea and
  `09:30–10:30 America/New_York` for the U.S. with `ZoneInfo`; reject a local
  clock or UTC offset that does not match the market contract.
- For v2, accept only the complete emitted screen contract. Bind account and
  exposure views to one timezone-aware pre-window snapshot instant on the
  entry session's local date. Require a readable, SHA-256-bound
  `qta-market-session/v1` snapshot, bind the screen date to its
  `previous_session_date`, and reject snapshots older than the execution
  policy permits.
- Persist the local intent before any broker mutation.
- Keep the intraday path rank-ordered and mechanical. Reject a poll interval
  that cannot fit the candidate count and broker pacing; record cycle,
  quote/status, and submit latency in the session receipt, and freeze when a
  quote exhausts its cycle budget.
- Treat an HTTP timeout as `UNKNOWN`, not as rejection.
- Do not retry an ambiguous mutation unless the adapter proves a safe,
  body-identical idempotent replay.
- Use settled unborrowed cash only. Keep lending limits, margin, shorting,
  automatic FX, and existing-position liquidation disabled unless a later
  signed policy explicitly enables and validates them.
- Keep secrets in environment variables or an OS secret store. Never print,
  persist, package, or ask the user to paste credentials into chat.
- Require the runtime account credentials to reproduce the frozen
  `broker_account_identity_hash` before the first quote or mutation. A missing
  arm artifact or an old account snapshot without this digest is `BLOCKED`.
- Keep live mode fail-closed until account allowlisting, contract probes,
  paper/shadow evidence, an external single-writer lock, and a matching arm
  artifact all pass.

An Agent Skill is not the scheduler. Use a deterministic process supervised by
`launchd` or `systemd`; do not put an LLM in the polling or order path.
