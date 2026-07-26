# Open-one-hour execution contract

## Version boundary

Keep three independent contracts:

```text
qta-1.0.0          completed T-1 adjusted daily data -> technical payload
qta-screen-1.0.0   legacy KR/US manifest -> deterministic candidates
qta-screen-1.1.0   four-exchange manifest -> exchange-ranked candidates
open1h-exec-1.0.0  broker/account snapshots -> intents and order lifecycle
```

The entry window is `09:00–10:00 Asia/Seoul` for Korea and
`09:30–10:30 America/New_York` for the U.S. The planner validates those exact
local clocks with the IANA timezone database and rejects a supplied UTC offset
that disagrees with `ZoneInfo`, including an incorrect U.S. DST offset. It also
requires a frozen `qta-market-session/v1` JSON snapshot whose local file
SHA-256 and canonical `session_hash` match the policy. The snapshot binds the
scheduled open session, regular open/close, and previous session date. A
weekday check is only a second defense; holiday and early-close truth comes
from the frozen calendar snapshot.

## Required frozen inputs

- `qta-screen/v1` or `qta-screen/v2` artifact and canonical hash;
- broker, environment, account alias, HMAC-derived broker account identity,
  market, and trading date;
- broker account snapshot including settled cash, positions, open orders, and
  snapshot time;
- cross-broker exposure snapshot, including fractional holdings even when
  exact quantity is unavailable;
- numeric risk and execution policies;
- fee, tax, FX, tick, lot, fractional, and settlement semantics;
- hashed local market-calendar snapshot and entry-window timestamps;
- code, configuration, and universe hashes.

A v2 screen requires `qta-exposure-snapshot/v2`. Every exposure position must
name one of `KOSPI`, `KOSDAQ`, `NYSE`, or `NASDAQ`; match positions by
`exchange:broker_symbol`. Never collapse a v2 position back to a market-only
key. Order intents use the manifest's verified `broker_symbol`, while
`canonical_symbol` and `data_symbol` remain provenance fields.

For v2, broker symbols are qualified by the KIS stock-master snapshots used by
the universe builder. The planner therefore requires account `broker: "kis"`.
It rejects Toss planning even if a caller supplies a self-consistent screen
hash. Toss remains unavailable for v2 until a separate Toss-qualified
all-universe snapshot contract exists; a symbol lookup endpoint is not evidence
that every selected symbol was qualified for that broker.

The v2 screen must match the complete `finalize_screen_v2` top-level contract:
source/schema/status/method and selector versions, analysis date, manifest and
selector hashes, counts and blocked fraction, normalized selector, all four
selected arrays, decisions, and screen hash. The planner validates the exact
field set, selector hash, counts, eligibility reasons, decision-to-selection
identity, instrument provenance, and canonical screen hash. It validates the
exact emitted READY or BLOCKED QTA schema, binds every READY payload's
`analysis_date` to the screen date, and recomputes the documented
total/medium/long/short/risk/ticker ranking before accepting exchange ranks or
selection order. It also checks the qta-1.0.0 score formula within the
two-decimal rounding envelope, score/opinion and score/setup relationships,
resolved-tick alignment, and the emitted 2R take-profit geometry. A smaller,
reranked, or semantically forged object cannot make itself acceptable merely
by hashing its own contents.

Each account position must include `market` and `symbol`, plus `exchange` for a
v2 screen. Each item in `open_orders` must represent a currently working order
and include `side, market, symbol`, plus `exchange` for v2. Existing positions
and working buy orders occupy exposure keys and concurrent-position slots
before any new intent is sized.

For v2, `analysis_date` must equal the calendar artifact's
`previous_session_date`. For both legacy and v2 screens, account and
cross-broker exposure snapshots must be timezone-aware, share one exact frozen
`as_of` instant, fall on the entry session's local date, be no earlier than the
calendar `source_as_of`, and be no later than the window start. Their age at
the window start must not exceed the policy's `snapshot_max_age_seconds`; that
field is capped at 3,600 seconds.

The exact market-session object contains:

```text
schema, provider, source_id, source_as_of, source_path, source_sha256,
market, timezone, session_date, previous_session_date, scheduled_status,
regular_open, regular_close, session_hash
```

`source_path` must be absolute and contain a JSON object exactly matching the
same fields except `source_path, source_sha256, session_hash`.
`source_sha256` is recomputed from that file. `session_hash` is canonical
SHA-256 over the full market-session object excluding only `session_hash`.
The complete object and hash are copied into the plan context and revalidated
from disk by the session runner, not only by the planner.

`scheduled_status: OPEN` means the calendar scheduled a regular session. It is
not evidence that the exchange is currently healthy. Emergency closures,
market-wide halts, broker incidents, and stale quotes remain runtime
`ENTRY_FREEZE` conditions. The current market-level contract treats KOSPI and
KOSDAQ as sharing the KR session, and NYSE and NASDAQ as sharing the U.S.
session. If venue calendars diverge, migrate to exchange-specific artifacts
instead of guessing.

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

Every plan embeds the complete normalized `screen, account, exposure, risk,
execution` objects under exact `frozen_inputs`. Before preview, polling, or
reconciliation, the runner validates the exact plan/context/window/policy and
intent schemas, verifies each deterministic intent ID and intent hash, then
runs the planner again from those frozen inputs and requires the rebuilt plan
to equal the supplied plan. Recomputing only `plan_hash`, or even making a
self-consistent quantity/cash/intent rehash, cannot change the result without
also changing the frozen inputs.

These hashes prove deterministic internal consistency, not who supplied an
input. Do not put account numbers or secrets in frozen inputs. Derive
`broker_account_identity_hash` as HMAC-SHA-256 over the canonical broker,
environment, and account-routing identity, using a separately stored
`QTA_ACCOUNT_BINDING_KEY` of at least 32 bytes. KIS identity includes both
`CANO` and `ACNT_PRDT_CD`; Toss identity includes `accountSeq`. The plan copies
only the digest into its context.

Before the first quote or mutation, require an exact `qta-trading-arm/v1`
artifact bound to `plan_hash`, selected adapter, environment, mode, trading
date, and the same account identity digest. Recompute the digest from the
runtime credentials and compare it with both plan and arm. Missing legacy
fields, a missing arm, the wrong KIS account/product, or the wrong Toss account
sequence is `BLOCKED`; never include a raw identifier in a plan, receipt,
event, or error. This development arm has an integrity hash but is not an
external signature; live promotion still requires the separately signed
authorization listed below.

## Trigger and polling

Poll at absolute times `window_start + n * interval`. Skip late cycles rather
than replaying them. Run all slow work—universe construction, daily QTA,
ranking, sizing, account/exposure freezing, calendar validation, and request
serialization—before the opening bell. The hot path contains only a
deterministic rank-ordered quote, trigger evaluation, persisted transition,
and broker request.

KIS KR and Toss need two REST calls per waiting intent: price and order book.
KIS-live US requires one same-source order-book call that contains last, bid,
ask, date, and time; its timestamp-less price fallback is forbidden. The
adapters cannot otherwise provide a safely joined bid/ask batch, so do not
claim batch latency. Before accepting a session, compute a broker- and
market-specific pacing budget from candidate count, quote-call count, and one
possible submit per intent in paper mode. Reserve one pacing interval for each
request, including response/persistence headroom:

```text
KIS paper: ceil(intent_count * 3 * 1.05s)
KIS live KR shadow: ceil(intent_count * 2 * 0.12s)
KIS live US shadow: ceil(intent_count * 1 * 0.12s)
Toss shadow: ceil(intent_count * 2 * 0.10s)
```

Reject a configured interval below that floor. This is only an API-pacing
feasibility bound, not proof of network latency. Production promotion also
requires observed shadow percentiles.

Start the runner before the open. It acquires/refreshes the broker token
30 seconds before the scheduled open and blocks the session if authentication
fails or finishes late. Before warm-up, resolve every venue and serialize every
potential submit request; a missing venue or invalid request must fail before
the opening bell. Every cycle records scheduled/start/end times, lateness,
duration, quote/status/submit latency, skipped-cycle counts, and expected REST
request counts. Non-mutation quote telemetry is committed once per cycle;
`RESERVED`, `SUBMITTING`, acknowledgements, and other mutation states retain
individual `synchronous=FULL` commits. If a quote consumes the cycle deadline,
freeze new entries instead of evaluating later ranks from a delayed cycle.

Do not start a submission inside its 10-second HTTP timeout plus broker pacing
margin before the window end. Pass the absolute window end into the broker
limiter, refuse a paced send that would cross it, and record both submit start
and acknowledgement time. If an already-sent request acknowledges after the
window, persist the acknowledgement, freeze entries, then immediately enter
the normal cancel/reconciliation phase.

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
For `CROSS_FROM_BELOW`, update the prior-price baseline only after quote
status, instrument identity, positive bid/ask/last, receipt freshness, and both
source timestamps pass. A stale, missing-source, mismatched, or malformed
quote cannot seed a later crossing.

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
CANCEL_PENDING -> CANCELLED | FILLED | UNKNOWN
UNKNOWN -> RECONCILING -> ACKNOWLEDGED | PARTIALLY_FILLED | FILLED
UNKNOWN -> RECONCILING -> MANUAL_BLOCK
```

Persist the intent and exact request hash before `SUBMITTING`. Store broker
status values without discarding unknown values.

One state directory may never silently bridge plans. Immediately after opening
the ledger, reject any nonterminal intent—or any unresolved `MANUAL_BLOCK`—
whose `plan_hash` or intent ID is outside the current plan. Also reject
`RESERVED`, `SUBMITTING`, `UNKNOWN`, `RECONCILING`, `CANCEL_PENDING`, and
`MANUAL_BLOCK` even when they belong to the same plan. Require broker evidence
and explicit clearance before warming authentication, reading quotes, or
sending a new mutation.

When reconciliation observes `ACKNOWLEDGED` or `PARTIALLY_FILLED` after a
cancel request, keep the local state at `CANCEL_PENDING`, return `BLOCKED`, and
monitor again. A cancel acknowledgement is not terminal proof that the
original order can no longer fill.

## Live promotion

Keep live readiness false until all are true:

- exact account and environment allowlist match;
- all numeric policy fields are present and borrowed cash is disabled;
- QTA, screen, planner, adapter, ledger, and crash-recovery tests pass;
- broker paper mode has no duplicate or unresolved order lifecycle;
- production shadow mode matches paper decisions without mutations;
- production-shadow authentication, quote, persistence, and order-history
  telemetry proves the configured candidate count fits the interval at the
  accepted latency percentile, with no unreported skipped cycles;
- accepted-then-timeout, partial-fill, cancel/fill, rate-limit, and restart
  faults produce no duplicate order;
- order-history pagination, broker-status normalization, and app-key/account
  shared pacing are complete for every enabled broker and environment;
- the calendar snapshot collector is authenticated, provider responses are
  archived, and emergency-closure/market-halt handling is fault-tested;
- append-only receipt and final broker/local reconciliation are complete;
- a plan-hash-bound arm artifact authorizes that account and trading date.

The current implementation intentionally blocks every live run. In particular,
`order_ttl_seconds`, `allow_partial_fill`, and `max_daily_loss_krw` are frozen
and hashed but are not yet complete runtime enforcement loops. Paper and
shadow receipts are development evidence only; they do not satisfy the live
promotion contract until those controls and their restart/fault tests exist.

An offline request preview uses a deliberately fake account identifier. Its
request hash proves deterministic serialization for that placeholder only; it
is not the account-bound hash that will be persisted immediately before an
actual submission. A preview lists potential requests for all planned intents
and does not claim that an intraday trigger was observed.

Default kill behavior is `ENTRY_FREEZE`: stop mutations, cancel known open
entry orders when safe, preserve protective orders, and reconcile. Never infer
permission to liquidate existing user positions.
