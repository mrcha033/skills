# MrCha Skills

A public, multi-topic collection of portable Agent Skills by `mrcha033`, packaged as four independently installable marketplace plugins for Codex and Claude Code.

Each directory under `skills/` remains a self-contained skill built around `SKILL.md`. Each marketplace plugin contains exactly one matching skill; none adds MCP servers, hooks, or external authentication.

## Install from the marketplace

### Codex

```bash
codex plugin marketplace add mrcha033/skills
codex plugin add advisor-review@mrcha-skills
codex plugin add build-lab-meeting-slides@mrcha-skills
codex plugin add quant-stock-technical@mrcha-skills
codex plugin add stock-scenario-story@mrcha-skills
```

Install only the plugins you want. Start a new Codex task after installation, then invoke the matching skill, such as `$advisor-review`.

### Claude Code

```bash
claude plugin marketplace add mrcha033/skills
claude plugin install advisor-review@mrcha-skills
claude plugin install build-lab-meeting-slides@mrcha-skills
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

## Presentation skills

### Build Lab Meeting Slides

`build-lab-meeting-slides` builds editable lab-meeting and research decks from a supplied PowerPoint template. It preserves the inherited template system, keeps HTML as a disposable prototype route, and adds visual-contract and SHA-pinned user-acceptance gates so structural QA is not mistaken for visual acceptance.

## Finance skills

### Quant Stock Technical

`quant-stock-technical` calculates reproducible technical opinions, risk, entry, stop, target, and a historical technical-strength score from completed daily adjusted OHLCV data. It excludes news, fundamentals, sentiment, analyst opinion, and discretionary model judgment.

### Stock Scenario Story

`stock-scenario-story` is its qualitative antithesis. It runs only after validating the complete result from `quant-stock-technical`, hides every upstream number, researches the surrounding company and market context, and tells a sourced, theatrical, numbers-free story purely for entertainment.

## Use in ChatGPT

Download one skill archive:

- [Quant Stock Technical](downloads/quant-stock-technical.zip)
- [Stock Scenario Story](downloads/stock-scenario-story.zip)
- [Advisor Review](downloads/advisor-review.zip)
- [Build Lab Meeting Slides](downloads/build-lab-meeting-slides.zip)

In an eligible ChatGPT workspace, open **Plugins → Skills → Create → Upload from your computer**, then upload the archive. GitHub repository URLs are not ChatGPT installation links.

`advisor-review` requires shell access to an authenticated local Codex CLI with GPT-5.6 Sol access. It needs no MCP server, separate API key, or native subagent primitive.

## Install as standalone Codex skills

If marketplace installation is unavailable, clone the repository and copy only the desired skill directory:

```bash
git clone https://github.com/mrcha033/skills.git mrcha-skills
cp -R mrcha-skills/skills/quant-stock-technical ~/.codex/skills/
cp -R mrcha-skills/skills/stock-scenario-story ~/.codex/skills/
cp -R mrcha-skills/skills/advisor-review ~/.codex/skills/
cp -R mrcha-skills/skills/build-lab-meeting-slides ~/.codex/skills/
```

Start a new Codex task after copying. Invoke `$advisor-review` explicitly when you want an independent review. For the paired finance workflow, invoke `$quant-stock-technical` first and pass its unmodified JSON result to `$stock-scenario-story`.

## Standalone Claude Code skills

The core skill folders also follow the Agent Skills layout. Copy a desired folder into `~/.claude/skills/` or a project's `.claude/skills/` directory when you do not want marketplace installation.

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
python3 -B skills/advisor-review/scripts/build_context_packet.py --self-test
python3 -B skills/advisor-review/scripts/validate_advice.py --self-test
python3 -B skills/advisor-review/scripts/run_advisor.py --self-test
python3 -B tests/test_handoff_integration.py
python3 -B tests/test_advisor_review.py
python3 -B tests/test_marketplace_packaging.py
claude plugin validate . --strict
```

The score is historical technical strength, not a probability of profit. Calculated price levels do not account for suitability, position sizing, fees, taxes, FX, slippage, broker rules, or fill probability.

No software license has been granted yet. Public availability does not grant permission to copy, modify, or redistribute the contents.
