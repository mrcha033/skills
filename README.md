# MrCha Skills

A public, multi-topic collection of portable Agent Skills by `mrcha033`.

This repository contains skills, not plugins and not a plugin marketplace. Each directory under `skills/` is a self-contained skill built around `SKILL.md` and its supporting resources.

## Finance skills

### Quant Stock Technical

`quant-stock-technical` calculates reproducible technical opinions, risk, entry, stop, target, and a historical technical-strength score from completed daily adjusted OHLCV data. It excludes news, fundamentals, sentiment, analyst opinion, and discretionary model judgment.

### Stock Scenario Story

`stock-scenario-story` is its qualitative antithesis. It runs only after validating the complete result from `quant-stock-technical`, hides every upstream number, researches the surrounding company and market context, and tells a sourced, theatrical, numbers-free story purely for entertainment.

## Use in ChatGPT

Download one skill archive from the latest GitHub release. In an eligible ChatGPT workspace, open **Plugins → Skills → Create → Upload from your computer**, then upload the archive. GitHub repository URLs are not ChatGPT installation links.

## Use in Codex

Clone the repository and copy the desired skill directory into the local Codex skills directory:

```bash
git clone https://github.com/mrcha033/skills.git mrcha-skills
cp -R mrcha-skills/skills/quant-stock-technical ~/.codex/skills/
cp -R mrcha-skills/skills/stock-scenario-story ~/.codex/skills/
```

Start a new Codex task after copying. Invoke `$quant-stock-technical` first and pass its unmodified JSON result to `$stock-scenario-story`.

## Use in Claude Code

The core skill folders follow the Agent Skills layout. Copy a desired folder into `~/.claude/skills/` or a project's `.claude/skills/` directory. Platform-specific behavior may still require testing in the target product.

## Input

The deterministic skill requires finalized ticker and same-market benchmark CSV files with:

```text
date,open,high,low,close,adjusted_close,volume
```

Also provide the market, ticker, and valid market tick size. The skill requires at least 756 shared completed sessions and recommends 1,260.

## Validation

```bash
python3 -B skills/quant-stock-technical/scripts/analyze_stock.py --self-test
python3 -B skills/stock-scenario-story/scripts/validate_quant_handoff.py --self-test
python3 -B skills/stock-scenario-story/scripts/validate_story_text.py --self-test
python3 -B tests/test_handoff_integration.py
```

The score is historical technical strength, not a probability of profit. Calculated price levels do not account for suitability, position sizing, fees, taxes, FX, slippage, broker rules, or fill probability.

No software license has been granted yet. Public availability does not grant permission to copy, modify, or redistribute the contents.
