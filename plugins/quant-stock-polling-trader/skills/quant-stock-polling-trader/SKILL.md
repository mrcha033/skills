---
name: quant-stock-polling-trader
description: Prepare and supervise deterministic Korean and U.S. KIS shadow sessions from a frozen quant-stock-technical screen. Use when the user wants secure KIS credential setup, pre-open screening, settled-cash sizing, first-hour quote polling, reconciliation, or recurring systemd automation. The daily path is shadow-only and sends no order, correction, or cancellation mutations.
---

# Quant Stock Polling Trader

Run a small, direct pipeline:

```text
prepare -> snapshot and plan -> first-hour shadow session -> reconcile
```

The default daily workflow does not use approved source digests, provenance
signatures, account-identity HMACs, trading-arm files, or mandatory Toss/NH
exports. Do not add those gates back unless the user explicitly asks for a
separate audited deployment profile.

## Credentials

Keep credentials out of chat, repositories, command arguments, plans, and
status files. Resolve them from the process environment first and
`~/.config/mrcha-skills/secrets.env` second. The fallback file must be a
regular user-owned `0600` file.

Use separate KIS paper and live values:

```text
QTA_KIS_PAPER_APP_KEY
QTA_KIS_PAPER_APP_SECRET
QTA_KIS_PAPER_ACCOUNT_PREFIX
QTA_KIS_PAPER_ACCOUNT_PRODUCT
QTA_KIS_LIVE_APP_KEY
QTA_KIS_LIVE_APP_SECRET
QTA_KIS_LIVE_ACCOUNT_PREFIX
QTA_KIS_LIVE_ACCOUNT_PRODUCT
```

For the daily workflow use `live` credentials only to read the live account
and market data while keeping `mode=shadow`.

Check presence first:

```bash
python3 scripts/broker_credentials.py status --environment live
```

If configuration is missing, ask the user to run this in their own terminal:

```bash
python3 scripts/broker_credentials.py configure --environment live
```

Never ask the user to paste a credential into chat. Report missing variable
names only. Wait for the user to confirm that interactive configuration has
finished, then check presence again and authenticate:

```bash
python3 scripts/broker_credentials.py status --environment live
python3 scripts/broker_credentials.py auth-check --environment live
```

Authentication is proven only when the result says `AUTHENTICATED`. An
authenticated token does not enable live trading.

## Daily workflow

Read `references/daily-pipeline-contract.md`, then:

1. Use one stable `qta-daily-shadow-config/v2` file.
2. Run `scripts/daily_pipeline.py prepare` before the selected market opens.
   It freezes the official calendar, stops normally on a closed market,
   refreshes only that market's exchange pair and adjusted EOD cache, and
   creates the deterministic screen.
3. Run `scripts/daily_pipeline.py snapshot` shortly before the open. It reads
   KIS settled cash, positions, and open orders, optionally merges any
   explicitly configured external exposure files, and creates the plan.
4. Run `scripts/daily_pipeline.py entry` before the 30-second authentication
   warm-up. The Python runner polls the first hour mechanically and then
   reconciles the local intent ledger.
5. Use `scripts/systemd_units.py generate` for recurring user services and
   timers. When the user asks to automate, generation is intermediate work:
   verify, install, enable, start, and inspect the requested units.

Daily preparation starts at `01:00` in each market timezone. This leaves eight
hours before the Korean open and eight and a half hours before the U.S. open,
without making a Korean session wait for U.S. EOD or vice versa.
Finish an initial multi-year bootstrap before relying on a daily timer; the
timer is intended to resume and increment an existing cache.

To receive optional ntfy completion/block notifications, place these values in
the same mode-`0600` environment file used by the generated services:

```text
QTA_NTFY_TOPIC_URL=https://ntfy.example/a-long-private-topic
QTA_NTFY_TOKEN=optional-access-token
```

The full topic URL and token are never written to a bundle, status file, or
journal. Notification delivery is best-effort and cannot turn a completed
shadow stage into a trading failure. A journal receipt records only
`SENT`, `FAILED`, or `DISABLED`.

The state path is:

```text
RUNTIME_ROOT/state/<account-alias>/<market>/<session-date>
```

The account alias is nonsecret and configured by the user. A current session
is never entered twice. An unresolved broker mutation from a paper-mode run
still requires reconciliation before another entry.

## Direct planning and polling

For a non-recurring session, read `references/execution-contract.md`,
`references/account-snapshot-contract.md`, and
`references/broker-contracts.md`.

```bash
python3 scripts/account_snapshot.py collect --job /absolute/account-job.json
python3 scripts/plan_orders.py \
  --screen /absolute/screen.json \
  --account /absolute/account.json \
  --exposure /absolute/exposure.json \
  --risk /absolute/risk.json \
  --execution /absolute/execution.json \
  --output /absolute/order-plan.json
python3 scripts/run_session.py run \
  --plan /absolute/order-plan.json \
  --broker kis-live \
  --mode shadow \
  --state-dir /absolute/state \
  --output /absolute/session-status.json
python3 scripts/reconcile.py \
  --plan /absolute/order-plan.json \
  --broker kis-live \
  --state-dir /absolute/state \
  --output /absolute/reconciliation-status.json
```

Additional Toss or NH exposure files are optional. If the user configures one,
validate and merge it; if none is configured, continue with the KIS account
view without fabricating an empty external-broker file.

## Fixed safety boundary

- The daily path accepts only `broker=kis-live`, `mode=shadow`.
- `live_enabled` stays `false` and `api_mutation_count` stays `0`.
- Do not submit, amend, or cancel an order in shadow mode.
- Use settled cash only. Exclude borrowed buying power.
- Keep margin, shorting, automatic FX, and additions to existing positions
  disabled.
- Use the official calendar snapshot; do not guess holidays, early closes,
  timezones, or DST.
- Use complete prior-session data; do not fill gaps with internet quotes or
  estimates.
- Keep ranking, sizing, quote checks, and cycle timing deterministic.
- Do not put an LLM in the polling loop.
- Never claim profit, guaranteed returns, or a recommendation.

Run all bundled self-tests after changing the implementation:

```bash
python3 scripts/execution_core.py --self-test
python3 scripts/freeze_market_session.py --self-test
python3 scripts/broker_adapters.py --self-test
python3 scripts/broker_credentials.py self-test
python3 scripts/account_snapshot.py self-test
python3 scripts/market_calendar.py self-test
python3 scripts/daily_pipeline.py self-test
python3 scripts/plan_orders.py --self-test
python3 scripts/run_session.py self-test
python3 scripts/reconcile.py --self-test
python3 scripts/systemd_units.py self-test
```
