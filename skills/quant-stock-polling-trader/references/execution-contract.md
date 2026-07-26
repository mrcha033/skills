# Open-one-hour execution contract

## Version boundary

Keep three independent contracts:

```text
qta-1.0.0          completed T-1 adjusted daily data -> technical payload
qta-screen-1.0.0   frozen universe payloads -> deterministic candidates
open1h-exec-1.0.0  broker/account snapshots -> intents and order lifecycle
```

The entry window is `09:00–10:00 Asia/Seoul` for Korea and
`09:30–10:30 America/New_York` for the U.S. Resolve the actual session from a
broker or exchange calendar. Do not hard-code a KST offset for the U.S.

## Required frozen inputs

- `qta-screen/v1` artifact and canonical hash;
- broker, environment, account alias, market, and trading date;
- broker account snapshot including settled cash, positions, open orders, and
  snapshot time;
- cross-broker exposure snapshot, including fractional holdings even when
  exact quantity is unavailable;
- numeric risk and execution policies;
- fee, tax, FX, tick, lot, fractional, and settlement semantics;
- market calendar and entry-window timestamps;
- code, configuration, and universe hashes.

Lending availability is not settled cash. Keep it in a distinct field and
exclude it from quantity calculations.

## Deterministic planning

Calculate every quantity from the same pre-session snapshot:

```text
effective_loss_per_share =
    entry_limit - stop
  + entry_limit * round_trip_cost_bps / 10000

quantity = floor_to_lot(min(
  per_trade_risk_native / effective_loss_per_share,
  max_symbol_notional_native / entry_limit,
  settled_unborrowed_cash_native / worst_case_all_in_price
))
```

Subtract existing and working exposure before creating an intent. If additions
to existing cross-broker exposure are disabled, reject the symbol even when it
is held at another broker or only as a fractional share.

Do not reallocate unused cash after a skipped or rejected order. Keep ranking,
tie-breaking, cash reservation, and candidate count fixed for the session.

## Trigger and polling

Poll at absolute times `window_start + n * interval`. Skip late cycles rather
than replaying them. Partition quote batches by market and join responses by
symbol.

Require a fresh quote and a price-capped limit order:

```text
last_price >= qta_entry
best_ask <= entry_limit
quote_age <= max_quote_age
spread_bps <= max_spread_bps
```

Freeze whether the trigger accepts a price already above entry at the first
poll (`AT_OR_ABOVE`) or requires a below-to-above transition
(`CROSS_FROM_BELOW`). Never infer this choice at runtime.

If an adapter does not provide bid/ask, do not substitute a market order.
Either obtain the order book through the broker-specific endpoint or block the
candidate.

At the window end, freeze new entries, cancel outstanding entry orders, and
reconcile. Continue managing already filled positions. Automatic market
flattening is a distinct, explicitly armed policy.

## State machine

```text
PLANNED -> WAIT_TRIGGER -> RESERVED -> SUBMITTING
SUBMITTING -> ACKNOWLEDGED | UNKNOWN
ACKNOWLEDGED -> PARTIALLY_FILLED | FILLED | CANCEL_PENDING | REJECTED
PARTIALLY_FILLED -> FILLED | CANCEL_PENDING
CANCEL_PENDING -> CANCELLED | PARTIALLY_FILLED | FILLED | UNKNOWN
UNKNOWN -> RECONCILING -> ACKNOWLEDGED | PARTIALLY_FILLED | FILLED
UNKNOWN -> RECONCILING -> MANUAL_BLOCK
```

Persist the intent and exact request hash before `SUBMITTING`. Store broker
status values without discarding unknown values.

## Live promotion

Keep live readiness false until all are true:

- exact account and environment allowlist match;
- all numeric policy fields are present and borrowed cash is disabled;
- QTA, screen, planner, adapter, ledger, and crash-recovery tests pass;
- broker paper mode has no duplicate or unresolved order lifecycle;
- production shadow mode matches paper decisions without mutations;
- accepted-then-timeout, partial-fill, cancel/fill, rate-limit, and restart
  faults produce no duplicate order;
- order-history pagination, broker-status normalization, and app-key/account
  shared pacing are complete for every enabled broker and environment;
- append-only receipt and final broker/local reconciliation are complete;
- a plan-hash-bound arm artifact authorizes that account and trading date.

An offline request preview uses a deliberately fake account identifier. Its
request hash proves deterministic serialization for that placeholder only; it
is not the account-bound hash that will be persisted immediately before an
actual submission. A preview lists potential requests for all planned intents
and does not claim that an intraday trigger was observed.

Default kill behavior is `ENTRY_FREEZE`: stop mutations, cancel known open
entry orders when safe, preserve protective orders, and reconcile. Never infer
permission to liquidate existing user positions.
