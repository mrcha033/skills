---
name: quant-stock-technical
description: Calculate reproducible, numbers-only stock technical analysis and deterministic KOSPI, KOSDAQ, NYSE, and NASDAQ target-universe screening from completed daily adjusted OHLCV and benchmark data. Use when the user wants an as-of-date market/ticker report, short-, medium-, and long-horizon opinions, a quantitative risk counterpoint, entry/stop/take-profit levels, a 0-100 technical-strength score, or a frozen four-exchange screening artifact for a separate execution system. Do not use news, fundamentals, analyst views, narrative judgment, intraday execution, or live-order claims.
---

# Quant Stock Technical

Produce technical-analysis outputs only through the bundled deterministic calculator. Do not estimate indicators, prices, labels, or scores in prose.

## Required inputs

Obtain two finalized daily CSV files:

- ticker OHLCV with `date,open,high,low,close,adjusted_close,volume`
- benchmark OHLCV with the same columns

Also obtain `market`, `ticker`, and the market's valid `tick_size`. Treat the last shared CSV date as the default analysis date. Use `--analysis-date` for historical cutoffs. Require at least 756 shared completed sessions; recommend 1,260.

Never treat an unfinished daily candle as completed. Never silently substitute unadjusted data, another ticker, another benchmark, a guessed tick size, or synthetic observations.

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

For a four-exchange target universe, first read
`references/universe-contract.md`, build `qta-universe-manifest/v2` with
`scripts/build_universe_manifest.py`, then read `references/screen-contract.md`
and run `scripts/screen_universe.py`. Use `qta-universe-manifest/v1` only for
legacy, explicitly curated KR/US inputs. The selector is a separate
cross-sectional strategy assumption; it does not change `qta-1.0.0` or turn
its score into a probability. Pass the complete frozen screen artifact to
`$quant-stock-polling-trader`; never submit orders from this skill.

After changing universe or screening code, run:

```bash
python3 scripts/build_universe_manifest.py --self-test
python3 scripts/screen_universe.py --self-test
```

When `$stock-scenario-story` will run next, save the unmodified JSON output to a file. That downstream skill requires `source_skill`, `result_schema`, `method_version`, and the complete calculator payload; do not summarize or reconstruct the handoff manually.

## Output boundaries

- Interpret the 0-100 score as historical technical strength under method `qta-1.0.0`, not as probability of profit.
- Treat entry, stop, and take-profit as calculated price levels, not execution or suitability advice.
- Keep brokerage fees, tax, FX, slippage, position sizing, account constraints, and order semantics outside this technical score unless a later version explicitly models them.
- Do not claim real-time monitoring. This version consumes completed end-of-day data.
- Keep API credentials, account identifiers, intraday quotes, and broker mutations outside this skill.
