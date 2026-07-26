# QTA screen contracts

`qta-screen-1.0.0` applies a frozen cross-sectional selector to unmodified
`quant-stock-technical/v1` results. It does not modify `qta-1.0.0`.

## Legacy v1 input

Pass a universe manifest:

```json
{
  "schema": "qta-universe-manifest/v1",
  "analysis_date": "2026-07-25",
  "instruments": [
    {
      "market": "KR",
      "ticker": "005930",
      "ticker_csv": "data/005930.csv",
      "benchmark_csv": "data/kospi.csv",
      "tick_size": "100",
      "source_name": "licensed-adjusted-eod"
    }
  ]
}
```

Relative CSV paths resolve from the manifest directory. Require unique
`(market, ticker)` pairs and `market` equal to `KR` or `US`.

Pass an explicit selector:

```json
{
  "selector_version": "qta-screen-1.0.0",
  "min_total_score": "60",
  "eligible_setup_statuses": ["READY"],
  "top_k_by_market": {"KR": 5, "US": 5},
  "max_blocked_fraction": "0"
}
```

Do not hide strategy choices in defaults. Every field above is required.
`eligible_setup_statuses` must be a non-empty subset of the exact uppercase
values `READY` and `CONDITIONAL`; no other calculator or caller-defined status
is eligible.

## Calculation and ordering

Run the original calculator once per instrument with the manifest analysis
date. Preserve each complete calculator payload.

Filter by calculation status, setup status, and minimum score. Sort eligible
results with this total ordering:

```text
total_score descending
medium.score descending
long.score descending
short.score descending
risk.score ascending
market ascending
ticker ascending
```

The score is ticker-relative rather than a cross-sectional rank. Treat this
ordering as a new strategy assumption that needs its own historical,
walk-forward, and opening-hour execution validation.

If the fraction of `BLOCKED` instruments exceeds `max_blocked_fraction`, return
the whole screen as `BLOCKED`. Never silently drop enough invalid instruments
to change the effective universe beyond that frozen tolerance.

## Output

Emit `qta-screen/v1` with:

- selector and method versions;
- canonical hashes of the manifest and selector;
- the complete unmodified QTA payload for every instrument;
- deterministic eligibility decisions and ranks;
- selected candidates per market;
- explicit blocked reasons.

Use the output only as an input to a separate execution skill. It is not an
order, suitability decision, or profit forecast.

## Four-exchange v2 input

Read `universe-contract.md` first. Pass the complete, unmodified
`qta-universe-manifest/v2` emitted by `build_universe_manifest.py`; the screen
recomputes its canonical hash and verifies source, CSV, tick, count, and broker
tradability metadata. It rejects a `BLOCKED` manifest.

Pass this exact selector:

```json
{
  "selector_version": "qta-screen-1.1.0",
  "min_total_score": "60",
  "eligible_setup_statuses": ["READY"],
  "top_k_by_exchange": {
    "KOSPI": 2,
    "KOSDAQ": 2,
    "NYSE": 2,
    "NASDAQ": 2
  },
  "min_selected_by_exchange": {
    "KOSPI": 0,
    "KOSDAQ": 0,
    "NYSE": 0,
    "NASDAQ": 0
  },
  "max_blocked_fraction": "0"
}
```

Every field is required. `top_k_by_exchange` must contain all four keys,
including any exchange intentionally assigned zero slots.
`min_selected_by_exchange` must also contain all four keys, and every value
must be between zero and that exchange's top K. Use zero to allow a valid
no-trade result when no setup passes; use one or more only when underfilling
that exchange should block the whole screen. The same exact
`eligible_setup_statuses` restriction from v1 applies.

Run the same `qta-1.0.0` calculator once per included instrument. Use
`canonical_symbol` as the QTA ticker, the explicit exchange benchmark CSV, and
`tick_contract.resolved_tick_size`; never use `data_symbol` or `broker_symbol`
interchangeably.

Apply the v1 score ordering independently inside each exchange. The final
tie-break is canonical symbol ascending. Set `exchange_rank` before taking the
configured top K. A score remains ticker-relative rather than a
cross-sectional percentile or probability.

If the fraction of calculation-blocked included instruments exceeds
`max_blocked_fraction`, the entire v2 screen is `BLOCKED` and every exchange's
selected list is empty. The same happens when an exchange has fewer eligible
names than its explicit `min_selected_by_exchange`. Listings excluded by the
builder remain visible in the manifest and are not counted as attempted
calculations.

Emit `qta-screen/v2` with:

- the exact manifest hash, selector hash, and final screen hash;
- the complete unmodified QTA payload for each attempted instrument;
- explicit eligibility decisions and exchange ranks;
- exact selected keys `KOSPI`, `KOSDAQ`, `NYSE`, and `NASDAQ`;
- the complete v2 instrument metadata alongside every selected payload.

The polling trader combines KOSPI then KOSDAQ for a Korean session, and NYSE
then NASDAQ for a U.S. session. It orders by the frozen exchange order,
`exchange_rank`, then `broker_symbol`; it does not rescore candidates.
