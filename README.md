# Quant Stock Technical

A Codex plugin for deterministic, numbers-only daily stock technical analysis.

The bundled `quant-stock-technical` skill calculates:

- analysis date, market, and ticker
- short-, medium-, and long-horizon scores and mechanical labels
- a one-line quantitative risk counterpoint
- entry, stop, and take-profit levels
- a 0-100 historical technical-strength score

The model does not use news, fundamentals, analyst opinions, sentiment, or discretionary LLM judgment. Version `qta-1.0.0` uses completed end-of-day data and does not provide real-time monitoring or order execution.

## Install

Clone this repository, then register its repository marketplace and install the plugin:

```bash
git clone https://github.com/mrcha033/quant-stock-technical.git
codex plugin marketplace add /absolute/path/to/quant-stock-technical
codex plugin add quant-stock-technical@quant-stock-technical
```

Start a new Codex task and invoke `$quant-stock-technical`.

## Input

Provide finalized ticker and same-market benchmark CSV files with these columns:

```text
date,open,high,low,close,adjusted_close,volume
```

At least 756 shared completed sessions are required; 1,260 are recommended. Also provide the market, ticker, and valid market tick size.

Example calculator call:

```bash
python3 plugins/quant-stock-technical/skills/quant-stock-technical/scripts/analyze_stock.py \
  --ticker-csv arm.csv \
  --benchmark-csv soxx.csv \
  --market NASDAQ \
  --ticker ARM \
  --tick-size 0.01 \
  --source-name licensed-eod-export \
  --format markdown
```

## Validation

```bash
python3 -B plugins/quant-stock-technical/skills/quant-stock-technical/scripts/analyze_stock.py --self-test
```

## Boundaries

The 0-100 result is a ticker-relative historical technical-strength score, not a probability of profit. Calculated price levels do not account for suitability, position sizing, fees, taxes, FX, slippage, broker rules, or fill probability.

No software license has been granted yet. Public availability of this repository does not grant permission to copy, modify, or redistribute its contents.
