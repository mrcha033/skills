# Stock Analysis Marketplace

A public Codex marketplace for paired quantitative and qualitative stock-analysis workflows.

## Plugins

### Quant Stock Technical

The `quant-stock-technical` skill calculates:

- analysis date, market, and ticker
- short-, medium-, and long-horizon scores and mechanical labels
- a one-line quantitative risk counterpoint
- entry, stop, and take-profit levels
- a 0-100 historical technical-strength score

The model does not use news, fundamentals, analyst opinions, sentiment, or discretionary LLM judgment. Version `qta-1.0.0` uses completed end-of-day data and does not provide real-time monitoring or order execution.

### Stock Scenario Story

The `stock-scenario-story` skill is the deliberate qualitative antithesis. It runs only after validating the complete JSON result from `quant-stock-technical`, hides every upstream number, researches point-in-time company and market context, and performs a theatrical, sourced, numbers-free stock tale purely for entertainment.

## Install

Clone this repository, then register its repository marketplace and install the plugin:

```bash
git clone https://github.com/mrcha033/stock-analysis-marketplace.git
codex plugin marketplace add /absolute/path/to/stock-analysis-marketplace
codex plugin add quant-stock-technical@stock-analysis-marketplace
codex plugin add stock-scenario-story@stock-analysis-marketplace
```

Start a new Codex task and invoke `$quant-stock-technical`.

Invoke `$stock-scenario-story` only with the unmodified JSON output from the deterministic skill.

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
python3 -B plugins/stock-scenario-story/skills/stock-scenario-story/scripts/validate_quant_handoff.py --self-test
python3 -B plugins/stock-scenario-story/skills/stock-scenario-story/scripts/validate_story_text.py --self-test
```

## Boundaries

The 0-100 result is a ticker-relative historical technical-strength score, not a probability of profit. Calculated price levels do not account for suitability, position sizing, fees, taxes, FX, slippage, broker rules, or fill probability.

No software license has been granted yet. Public availability of this repository does not grant permission to copy, modify, or redistribute its contents.
