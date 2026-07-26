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
this policy always excludes it.

## Account snapshot

```json
{
  "schema": "qta-account-snapshot/v1",
  "broker": "kis",
  "environment": "paper",
  "account_alias": "kis-paper-kr",
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

Never put a real account number, app key, or secret in this file.

## Execution policy

Korea:

```json
{
  "schema": "qta-execution-policy/v1",
  "market": "KR",
  "timezone": "Asia/Seoul",
  "entry_window_start": "2026-07-27T09:00:00+09:00",
  "entry_window_end": "2026-07-27T10:00:00+09:00",
  "poll_interval_seconds": 3,
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

For the U.S., use `America/New_York` and `09:30–10:30` with the correct offset
for that session. Do not derive it by subtracting a fixed number of hours from
KST.

## Venue map

```json
{
  "schema": "qta-venue-map/v1",
  "venues": {
    "KR:005930": "KRX",
    "US:AAPL": "NASD"
  }
}
```

KIS paper uses KRX for Korea. KIS U.S. order venues are `NASD`, `NYSE`, and
`AMEX`; quote endpoints use a different code internally, which the adapter
maps. Toss ignores the venue map but the frozen file remains part of the
session input.
