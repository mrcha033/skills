---
name: quant-stock-technical
description: Acquire resumable read-only KIS Korean and U.S. adjusted-EOD bundles, calculate reproducible numbers-only stock technical analysis, and screen deterministic KOSPI, KOSDAQ, NYSE, and NASDAQ target universes. Use when the user needs completed historical OHLCV and benchmarks, an as-of-date market/ticker report, short-, medium-, and long-horizon opinions, a quantitative risk counterpoint, entry/stop/take-profit levels, a 0-100 technical-strength score, or a frozen exchange-aware screen for a separate execution system. Do not use news, fundamentals, analyst views, narrative judgment, intraday execution, or live-order claims.
---

# Quant Stock Technical

Produce technical-analysis outputs only through the bundled deterministic calculator. Do not estimate indicators, prices, labels, or scores in prose.

## Required inputs

Obtain two finalized daily CSV files, or build the Korean files with the
read-only KIS EOD workflow below:

- ticker OHLCV with `date,open,high,low,close,adjusted_close,volume`
- benchmark OHLCV with the same columns

Also obtain `market`, `ticker`, and the market's valid `tick_size`. Treat the last shared CSV date as the default analysis date. Use `--analysis-date` for historical cutoffs. Require at least 756 shared completed sessions; recommend 1,260.

Never treat an unfinished daily candle as completed. Never silently substitute unadjusted data, another ticker, another benchmark, a guessed tick size, or synthetic observations.

## Korean EOD acquisition

When completed Korean history is missing, read
`references/eod-provider-contract.md`. Do not stop merely because the caller
did not prebuild thousands of CSVs. Before the entry session:

1. Run `scripts/fetch_kis_kr_eod.py snapshot-sources` to freeze KRX KIND and
   KIS KOSPI/KOSDAQ master files.
2. Bind `analysis_date` to the frozen calendar's previous completed session.
3. Run `scripts/fetch_kis_kr_eod.py collect --job ...` with at least 756
   sessions and the existing secure KIS credential environment.
4. Preserve `eod-bundle-receipt.json`, `eod-catalog.csv`, benchmarks, stock
   CSVs, and `universe-build-spec.json` together.
5. Continue to universe build and screening only when the required market
   scope is complete. Never lower coverage or fabricate inactive exchanges.

The collector is resumable: the first run backfills in bounded pages and later
runs update only missing completed sessions. Run it outside the first-hour
polling path. It performs authentication and market-data reads only;
`api_mutation_count` must remain zero and no account identifier is required.
Do not issue a separate token-only auth probe immediately before starting the
collector; KIS limits token issuance to once per minute. Treat the collector's
first successful read as authentication evidence or inject one
supervisor-held token through the environment.

## U.S. EOD acquisition

When NYSE/NASDAQ history is missing, read
`references/us-eod-provider-contract.md`. Freeze Nasdaq Trader official symbol
directories and KIS overseas masters with
`scripts/fetch_kis_us_eod.py snapshot-sources`, then run `collect --job ...`
outside the U.S. entry window. KIS stock history must use adjusted `MODP=1`;
preserve exchange-specific membership, broker symbols, benchmarks, and tick
mappings. A U.S.-scoped build uses NYSE/NASDAQ only; combine a completed
Korean catalog only when the caller explicitly requests an all-four-exchange
research universe. If KIS supplies an interior
adjusted row with invalid OHLC geometry, preserve its hash and values in the
invalid-row audit and exclude only that real row without interpolation. An
invalid cutoff row remains blocking.

## Workflow

1. Read `references/methodology.md` when explaining, reviewing, or changing the model.
2. Run `scripts/analyze_stock.py` with the user-supplied files and identifiers.
3. Return the calculator output without changing values or labels.
4. State the data source and cutoff. Keep `score_basis` visible whenever a user could mistake the score for a probability.
5. If the calculator returns `BLOCKED`, report the missing or invalid input. Do not manufacture a partial recommendation.

Example:

```bash
python3 scripts/analyze_stock.py \
  --ticker-csv arm.csv \
  --benchmark-csv soxx.csv \
  --market NASDAQ \
  --ticker ARM \
  --tick-size 0.01 \
  --source-name "licensed-eod-export" \
  --format markdown
```

Use `--format json` for machine-readable output. Run `python3 scripts/analyze_stock.py --self-test` after modifying the calculator.

For a Korean, U.S., or all-four-exchange target universe, first read
`references/universe-contract.md`, build `qta-universe-manifest/v2` with
`scripts/build_universe_manifest.py`, then read `references/screen-contract.md`
and run `scripts/screen_universe.py`. Use `qta-universe-manifest/v1` only for
legacy, explicitly curated KR/US inputs. The selector is a separate
cross-sectional strategy assumption; it does not change `qta-1.0.0` or turn
its score into a probability. Pass the complete frozen screen artifact to
`$quant-stock-polling-trader`; never submit orders from this skill.

After changing universe or screening code, run:

```bash
python3 scripts/fetch_kis_kr_eod.py --self-test
python3 scripts/fetch_kis_us_eod.py --self-test
python3 scripts/build_universe_manifest.py --self-test
python3 scripts/screen_universe.py --self-test
```

When `$stock-scenario-story` will run next, save the unmodified JSON output to a file. That downstream skill requires `source_skill`, `result_schema`, `method_version`, and the complete calculator payload; do not summarize or reconstruct the handoff manually.

## Output boundaries

- Interpret the 0-100 score as historical technical strength under method `qta-1.0.0`, not as probability of profit.
- Treat entry, stop, and take-profit as calculated price levels, not execution or suitability advice.
- Keep brokerage fees, tax, FX, slippage, position sizing, account constraints, and order semantics outside this technical score unless a later version explicitly models them.
- Do not claim real-time monitoring. This version consumes completed end-of-day data.
- Keep account identifiers, intraday quotes, and broker mutations outside this
  skill. KIS app credentials may be resolved only by the read-only EOD
  collector through the existing secure environment or mode-0600 fallback;
  never persist or print them.
