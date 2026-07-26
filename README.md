# MrCha Skills

A public, multi-topic collection of portable Agent Skills by `mrcha033`, packaged as three independently installable marketplace plugins for Codex and Claude Code.

Each directory under `skills/` remains a self-contained skill built around `SKILL.md`. Each marketplace plugin contains exactly one matching skill; none adds MCP servers, hooks, or external authentication.

## Install from the marketplace

### Codex

```bash
codex plugin marketplace add mrcha033/skills
codex plugin add advisor-review@mrcha-skills
codex plugin add quant-stock-technical@mrcha-skills
codex plugin add stock-scenario-story@mrcha-skills
```

Install only the plugins you want. Start a new Codex task after installation, then invoke the matching skill, such as `$advisor-review`.

### Claude Code

```bash
claude plugin marketplace add mrcha033/skills
claude plugin install advisor-review@mrcha-skills
claude plugin install quant-stock-technical@mrcha-skills
claude plugin install stock-scenario-story@mrcha-skills
```

Install only the plugins you want. Run `/reload-plugins` in an active Claude Code session. Skills use their plugin namespace, such as `/advisor-review:advisor-review`.

### Migrate from the former bundle

First refresh the marketplace and install the individual plugins you want. Then remove the former all-in-one `mrcha-skills` plugin:

```bash
# Codex
codex plugin marketplace upgrade mrcha-skills
codex plugin add advisor-review@mrcha-skills
codex plugin remove mrcha-skills@mrcha-skills

# Claude Code
claude plugin marketplace update mrcha-skills
claude plugin install advisor-review@mrcha-skills
claude plugin uninstall mrcha-skills@mrcha-skills
```

Replace `advisor-review` with either stock plugin name, or install multiple individual plugins before removing the bundle.

## Agent workflow skills

### Advisor Review

`advisor-review` asks an independent GPT-5.6 Sol reviewer to challenge a plan, diagnose a stalled approach, assess a pivot, or audit completed work. It always runs an isolated, ephemeral `codex exec` process, defaults to `high` reasoning, and selects only `high`, `xhigh`, or `max` effort from the request.

## Finance skills

### Quant Stock Technical

`quant-stock-technical` calculates reproducible technical opinions, risk, entry, stop, target, and a historical technical-strength score from completed daily adjusted OHLCV data. It can also build and screen a deterministic, source-hashed intersection of KOSPI, KOSDAQ, NYSE, and NASDAQ listings without changing `qta-1.0.0`. It excludes news, fundamentals, sentiment, analyst opinion, discretionary model judgment, and broker mutations.

`quant-stock-polling-trader` is currently a development-only local skill. It consumes the frozen exchange-aware screen, routes broker symbols and venues, plans whole-share orders, and implements fail-closed Toss/KIS polling, ledger, and reconciliation contracts. It is intentionally not yet a marketplace plugin or download.
Its first-hour runner precomputes the slow path before the open, warms
authentication 30 seconds early, admits only broker-rate-feasible candidate
counts, polls on absolute rank-ordered cycles, and records cycle/quote/submit
latency. Live mutation remains disabled.
The four-exchange v2 universe is currently qualified from KIS stock masters, so
v2 planning is KIS-only; Toss remains available only through the legacy
contract until a Toss-qualified universe snapshot is implemented and verified.
V2 planning also requires a local SHA-256-bound market-session snapshot,
the latest completed session date, and fresh same-instant account/exposure
snapshots.

### Stock Scenario Story

`stock-scenario-story` is its qualitative antithesis. It runs only after validating the complete result from `quant-stock-technical`, hides every upstream number, researches the surrounding company and market context, and tells a sourced, theatrical, numbers-free story purely for entertainment.

## Use in ChatGPT

Download one skill archive:

- [Quant Stock Technical](downloads/quant-stock-technical.zip)
- [Stock Scenario Story](downloads/stock-scenario-story.zip)
- [Advisor Review](downloads/advisor-review.zip)

In an eligible ChatGPT workspace, open **Plugins → Skills → Create → Upload from your computer**, then upload the archive. GitHub repository URLs are not ChatGPT installation links.

`advisor-review` requires shell access to an authenticated local Codex CLI with GPT-5.6 Sol access. It needs no MCP server, separate API key, or native subagent primitive.

## Install as standalone Codex skills

If marketplace installation is unavailable, clone the repository and copy only the desired skill directory:

```bash
git clone https://github.com/mrcha033/skills.git mrcha-skills
cp -R mrcha-skills/skills/quant-stock-technical ~/.codex/skills/
cp -R mrcha-skills/skills/quant-stock-polling-trader ~/.codex/skills/
cp -R mrcha-skills/skills/stock-scenario-story ~/.codex/skills/
cp -R mrcha-skills/skills/advisor-review ~/.codex/skills/
```

Start a new Codex task after copying. Invoke `$advisor-review` explicitly when you want an independent review. For the paired finance workflow, invoke `$quant-stock-technical` first and pass its unmodified JSON result to `$stock-scenario-story`.

## Standalone Claude Code skills

The core skill folders also follow the Agent Skills layout. Copy a desired folder into `~/.claude/skills/` or a project's `.claude/skills/` directory when you do not want marketplace installation.

## Input

The deterministic calculator requires finalized ticker and same-exchange
benchmark CSV files with:

```text
date,open,high,low,close,adjusted_close,volume
```

Also provide the market, ticker, and valid effective tick size. The calculator
requires at least 756 shared completed sessions and recommends 1,260.

The four-exchange builder additionally requires dated official listing and
broker-master snapshots, explicit adjusted-EOD file mappings, benchmark
mappings, effective-dated tick contracts, and a hashed per-exchange catalog
coverage minimum. Missing data is recorded as an exclusion; it is never
fetched or guessed during the entry window.

## Validation

```bash
python3 -B skills/quant-stock-technical/scripts/analyze_stock.py --self-test
python3 -B skills/quant-stock-technical/scripts/build_universe_manifest.py --self-test
python3 -B skills/quant-stock-technical/scripts/screen_universe.py --self-test
python3 -B skills/quant-stock-polling-trader/scripts/execution_core.py --self-test
python3 -B skills/quant-stock-polling-trader/scripts/broker_adapters.py --self-test
python3 -B skills/quant-stock-polling-trader/scripts/plan_orders.py --self-test
python3 -B skills/quant-stock-polling-trader/scripts/run_session.py self-test
python3 -B skills/quant-stock-polling-trader/scripts/reconcile.py --self-test
python3 -B skills/stock-scenario-story/scripts/validate_quant_handoff.py --self-test
python3 -B skills/stock-scenario-story/scripts/validate_story_text.py --self-test
python3 -B skills/advisor-review/scripts/build_context_packet.py --self-test
python3 -B skills/advisor-review/scripts/validate_advice.py --self-test
python3 -B skills/advisor-review/scripts/run_advisor.py --self-test
python3 -B tests/test_handoff_integration.py
python3 -B tests/test_advisor_review.py
python3 -B tests/test_universe_builder.py
python3 -B tests/test_screen_universe.py
python3 -B tests/test_quant_execution.py
python3 -B tests/test_marketplace_packaging.py
claude plugin validate . --strict
```

The score is historical technical strength, not a probability of profit. Calculated price levels do not account for suitability, position sizing, fees, taxes, FX, slippage, broker rules, or fill probability.

No software license has been granted yet. Public availability does not grant permission to copy, modify, or redistribute the contents.
