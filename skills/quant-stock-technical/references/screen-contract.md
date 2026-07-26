# QTA screen contract

`qta-screen-1.0.0` applies a frozen cross-sectional selector to unmodified
`quant-stock-technical/v1` results. It does not modify `qta-1.0.0`.

## Inputs

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
