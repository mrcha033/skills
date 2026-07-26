# Frozen policy examples

These values define a software canary, not an expected-return claim. Replace
dates and account-derived values only after a broker snapshot and calendar
preflight.

## Risk policy

```json
{
  "schema": "qta-risk-policy/v1",
  "base_currency": "KRW",
  "per_trade_risk_krw": "10000",
  "max_symbol_notional_krw": "200000",
  "max_concurrent_positions": 1,
  "max_daily_loss_krw": "20000",
  "round_trip_cost_bps": "30",
  "cash_buffer_bps": "100",
  "allow_existing_additions": false,
  "allow_borrowed_cash": false,
  "allow_margin": false,
  "allow_short": false,
  "allow_auto_fx": false,
  "whole_shares_only": true
}
```

`borrowed_buying_power` may be present in an account snapshot for audit, but
this policy always excludes it. A `qta-screen/v2` plan requires
`"broker": "kis"` because its `broker_symbol` values are qualified against KIS
stock-master snapshots. Do not change this field to `toss`; build and validate
a separate Toss-qualified universe contract first.

## Account snapshot

```json
{
  "schema": "qta-account-snapshot/v1",
  "broker": "kis",
  "environment": "paper",
  "account_alias": "kis-paper-kr",
  "broker_account_identity_hash": "replace-with-64-char-hmac-sha256",
  "market": "KR",
  "currency": "KRW",
  "as_of": "2026-07-27T08:50:00+09:00",
  "settled_cash": "200000",
  "borrowed_buying_power": "5000000",
  "fx_to_krw": "1",
  "positions": [],
  "open_orders": []
}
```

Never put a real account number, account product code, Toss account sequence,
app key, binding key, or secret in this file. Generate the digest with a
stable, secret `QTA_ACCOUNT_BINDING_KEY` of at least 32 bytes and the runtime
account-routing environment; copy only the 64-character digest here. The same
key and routing credentials must reproduce it immediately before polling and
reconciliation.

Generate only the nonsecret digest receipt, then copy its
`broker_account_identity_hash` into the snapshot:

```bash
python3 scripts/run_session.py account-identity \
  --broker kis-paper \
  --environment paper \
  --output /secure/runtime/account-identity.json
```

After planning, create the one-plan arm and supply the same file to the runner
and reconciler:

```bash
python3 scripts/run_session.py arm \
  --plan /secure/runtime/order-plan.json \
  --broker kis-paper \
  --mode paper \
  --output /secure/runtime/trading-arm.json

python3 scripts/run_session.py run \
  --plan /secure/runtime/order-plan.json \
  --arm /secure/runtime/trading-arm.json \
  --broker kis-paper \
  --mode paper \
  --state-dir /secure/runtime/state

python3 scripts/reconcile.py \
  --plan /secure/runtime/order-plan.json \
  --arm /secure/runtime/trading-arm.json \
  --broker kis-paper \
  --state-dir /secure/runtime/state
```

All four commands must run with the same binding key and matching broker
account-routing environment. Store those values in an OS secret store or
process environment; do not write them into these JSON files.

## Execution policy

Korea:

```json
{
  "schema": "qta-execution-policy/v1",
  "market": "KR",
  "timezone": "Asia/Seoul",
  "entry_window_start": "2026-07-27T09:00:00+09:00",
  "entry_window_end": "2026-07-27T10:00:00+09:00",
  "market_session": {
    "schema": "qta-market-session/v1",
    "provider": "KRX",
    "source_id": "replace-with-frozen-calendar-source-id",
    "source_as_of": "2026-07-27T08:30:00+09:00",
    "source_path": "/absolute/path/to/kr-market-session-2026-07-27.json",
    "source_sha256": "replace-with-lowercase-sha256",
    "market": "KR",
    "timezone": "Asia/Seoul",
    "session_date": "2026-07-27",
    "previous_session_date": "2026-07-24",
    "scheduled_status": "OPEN",
    "regular_open": "2026-07-27T09:00:00+09:00",
    "regular_close": "2026-07-27T15:30:00+09:00",
    "session_hash": "replace-with-canonical-session-sha256"
  },
  "snapshot_max_age_seconds": 1800,
  "poll_interval_seconds": 4,
  "quote_max_age_seconds": 5,
  "max_spread_bps": "25",
  "max_gap_bps": "20",
  "trigger_mode": "AT_OR_ABOVE",
  "order_ttl_seconds": 30,
  "order_type": "LIMIT",
  "time_in_force": "DAY",
  "allow_partial_fill": true,
  "cancel_remainder_at_window_end": true
}
```

The four-second example is the minimum admission value for one KIS-paper
intent: two quote calls plus one possible submit at the adapter's conservative
1.05-second pacing. Recompute it from the final number of planned intents;
two KIS-paper intents require at least seven seconds. KIS-live and Toss
shadow sessions have different pacing floors, but live mutation remains
disabled until the promotion gates pass. Start the runner at least 30 seconds
before the regular open so authentication is not on the first quote cycle.

For the U.S., use `America/New_York` and `09:30–10:30` with the correct offset
for that session. Do not derive it by subtracting a fixed number of hours from
KST. The planner checks the supplied offset against `ZoneInfo`, so use `-04:00`
only when New York is actually on daylight time and `-05:00` when it is on
standard time.

For a v2 run, stamp the account snapshot and
`qta-exposure-snapshot/v2` with the same timezone-aware `as_of` instant after
both reads are frozen. That instant must be on the local session date, at or
after `market_session.source_as_of`, no later than the entry-window start, and
no older than `snapshot_max_age_seconds`. The screen `analysis_date` must equal
the hashed calendar snapshot's `previous_session_date`.

The file at `market_session.source_path` contains exactly the market-session
fields shown above except `source_path`, `source_sha256`, and `session_hash`.
Generate the two hashes with the execution helper after freezing that file;
the placeholder values above are intentionally not runnable.

```bash
python3 scripts/freeze_market_session.py \
  --source /absolute/path/to/kr-market-session-2026-07-27.json \
  --output /absolute/path/to/kr-market-session-bound-2026-07-27.json
```

Copy the complete bound object into the execution policy without editing it.

## Venue map

Legacy `qta-screen/v1` plans require:

```json
{
  "schema": "qta-venue-map/v1",
  "venues": {
    "KR:005930": "KRX",
    "US:AAPL": "NASD"
  }
}
```

An exchange-aware v2 plan carries the verified venue inside each hashed intent,
so `run_session.py` does not require `--venue-map`. If a v2 map is supplied for
an independent cross-check, prefer exchange-qualified keys:

```json
{
  "schema": "qta-venue-map/v1",
  "venues": {
    "KOSPI:005930": "KRX",
    "NASDAQ:AAPL": "NASD",
    "NYSE:IBM": "NYSE"
  }
}
```

A supplied map must match the hashed intent or the session blocks. KIS paper
uses KRX for Korea. KIS U.S. order venues are `NASD`, `NYSE`, and `AMEX`; quote
endpoints use different codes internally, which the adapter maps. Toss ignores
the venue map for legacy v1 previews, but v2 planning blocks Toss because the
universe has not been Toss-qualified.
