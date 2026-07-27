---
name: quant-stock-polling-trader
description: Plan, validate, poll, and reconcile deterministic Korean and U.S. stock orders through broker adapters after a frozen quant-stock-technical screen. Use when the user wants a first-hour entry window, Toss Securities or 한국투자증권 (KIS) API integration, secure KIS credential setup, dotenv or OS secret-store resolution, credential status inspection, token authentication checks, paper/shadow/live promotion gates, cross-broker exposure snapshots, position sizing, order-state recovery, or fail-closed automated trading. Never infer missing risk, account, fee, FX, calendar, fractional-share, or broker semantics.
---

# Quant Stock Polling Trader

Keep model calculation and broker mutation separate. Accept only a complete
`qta-screen/v1` or exchange-aware `qta-screen/v2` artifact from
`$quant-stock-technical`. Require `qta-exposure-snapshot/v2` with explicit
exchange membership for a v2 screen. The current v2 universe is qualified from
KIS stock-master snapshots, so v2 planning is KIS-only; block Toss until a
separate Toss-qualified universe snapshot contract exists.

## KIS credential setup

Keep credentials out of chat, plans, receipts, repositories, and shell
arguments. Resolve them in this order:

1. existing process environment;
2. an OS secret store that injects process environment;
3. `~/.config/mrcha-skills/secrets.env` as a `0600` fallback.

Use separate paper and live values because KIS issues them independently:

```text
QTA_KIS_PAPER_APP_KEY
QTA_KIS_PAPER_APP_SECRET
QTA_KIS_PAPER_ACCOUNT_PREFIX
QTA_KIS_PAPER_ACCOUNT_PRODUCT
QTA_KIS_LIVE_APP_KEY
QTA_KIS_LIVE_APP_SECRET
QTA_KIS_LIVE_ACCOUNT_PREFIX
QTA_KIS_LIVE_ACCOUNT_PRODUCT
QTA_ACCOUNT_BINDING_KEY
```

Legacy process variables `QTA_KIS_APP_KEY`, `QTA_KIS_APP_SECRET`,
`QTA_KIS_ACCOUNT_PREFIX`, and `QTA_KIS_ACCOUNT_PRODUCT` take precedence for
one-off runtime injection. Override the fallback path with `QTA_SECRETS_FILE`
when needed. Never persist an access token in the dotenv file.

### Agent guidance contract

When assisting a user with KIS credentials, follow this sequence exactly:

1. Confirm whether the user means `paper` or `live`; never infer the credential
   scope.
2. Run the noninteractive presence check first:

   ```bash
   python3 scripts/broker_credentials.py status --environment paper
   ```

3. If the result is `BLOCKED`, report only the missing variable names. Do not
   run `configure` in an agent-owned, hidden, or delegated PTY, and never ask
   the user to paste a key into chat.
4. Tell the user to run the following command in their own interactive local
   terminal, substituting `live` only when that scope was explicitly selected:

   ```bash
   python3 scripts/broker_credentials.py configure --environment paper
   ```

5. Wait for the user to confirm completion. Then rerun `status`; only when it
   returns `READY`, run the token-only probe:

   ```bash
   python3 scripts/broker_credentials.py status --environment paper
   python3 scripts/broker_credentials.py auth-check --environment paper
   ```

6. Say authentication is proven only when `auth-check` returns a
   `status` field of `AUTHENTICATED`. Distinguish missing configuration,
   network failure, and broker rejection; never infer success from credential
   presence alone.

The `configure` command writes through a local TTY so values do not enter chat
or shell history. Use `live` instead of `paper` only for production
credentials. An
`AUTHENTICATED` result proves token issuance at that moment; it does not arm,
submit, or enable live orders.

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
   For KIS live/shadow, read `references/account-snapshot-contract.md` and use
   `scripts/account_snapshot.py collect` to fetch the KIS balance and working
   orders and merge fresh Toss/NH exposure components. This collector is
   read-only and records `api_mutation_count: 0`.
5. Run `scripts/plan_orders.py` to create a side-effect-free plan.
6. Generate the nonsecret account-identity receipt, create a plan-, account-,
   mode-, and trading-date-bound arm artifact, then start
   `scripts/run_session.py` before its 30-second pre-open authentication
   warm-up in `paper` or `shadow` mode first.
7. Run `scripts/reconcile.py` after every session and after any timeout,
   restart, partial fill, cancel, or replace.
8. Return `BLOCKED` when a required value, capability, or reconciliation fact
   is missing. Never compensate with a guessed default.

For deterministic Linux supervision, read `references/systemd-contract.md` and
use `scripts/systemd_units.py generate`. It emits hardened user service/timer
files and a hash receipt but never installs, enables, or starts them. Entry
jobs permit KIS paper/paper and KIS live/shadow only; live mode remains
blocked.

Run all bundled self-tests after changing a script:

```bash
python3 scripts/execution_core.py --self-test
python3 scripts/freeze_market_session.py --self-test
python3 scripts/broker_adapters.py --self-test
python3 scripts/broker_credentials.py self-test
python3 scripts/account_snapshot.py self-test
python3 scripts/plan_orders.py --self-test
python3 scripts/run_session.py self-test
python3 scripts/reconcile.py --self-test
python3 scripts/systemd_units.py self-test
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
- Treat the `0600` dotenv file as a local fallback, never as a distributable
  asset. Let process environment override it and report only key presence and
  source, never values.
- Require the runtime account credentials to reproduce the frozen
  `broker_account_identity_hash` before the first quote or mutation. A missing
  arm artifact or an old account snapshot without this digest is `BLOCKED`.
- Keep live mode fail-closed until account allowlisting, contract probes,
  paper/shadow evidence, an external single-writer lock, and a matching arm
  artifact all pass.

An Agent Skill is not the scheduler. Use a deterministic process supervised by
`launchd` or `systemd`; do not put an LLM in the polling or order path.
