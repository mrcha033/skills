# MrCha Skills

A public, multi-topic collection of portable Agent Skills by `mrcha033`, packaged as six independently installable marketplace plugins for Codex and Claude Code.

Each directory under `skills/` remains a self-contained skill built around `SKILL.md`. Each marketplace plugin contains exactly one matching skill; none adds MCP servers or hooks. Broker credentials stay local and are never packaged.

## Install from the marketplace

### Codex

```bash
codex plugin marketplace add mrcha033/skills
codex plugin add advisor-review@mrcha-skills
codex plugin add agent-finish-line@mrcha-skills
codex plugin add yonsei-central-student-governance-counsel@mrcha-skills
codex plugin add learnus-course-copilot@mrcha-skills
codex plugin add katok-reply-reuse@mrcha-skills
codex plugin add stock-scenario-story@mrcha-skills
```

Install only the plugins you want. Start a new Codex task after installation, then invoke the matching skill, such as `$advisor-review`.

### Claude Code

```bash
claude plugin marketplace add mrcha033/skills
claude plugin install advisor-review@mrcha-skills
claude plugin install agent-finish-line@mrcha-skills
claude plugin install yonsei-central-student-governance-counsel@mrcha-skills
claude plugin install learnus-course-copilot@mrcha-skills
claude plugin install katok-reply-reuse@mrcha-skills
claude plugin install stock-scenario-story@mrcha-skills
```

Install only the plugins you want. Run `/reload-plugins` in an active Claude Code session. Skills use their plugin namespace, such as `/advisor-review:advisor-review`.

### OpenCode

This repository is also an npm-compatible OpenCode plugin. Add the GitHub
package to `~/.config/opencode/opencode.jsonc`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "plugin": ["github:mrcha033/skills"]
}
```

OpenCode installs the package and registers its bundled `skills/` directory.
Restart OpenCode after changing the configuration. Once published to npm, the
same entry can use `"mrcha-skills"` instead.

### Migrate Secuway VPN to its standalone marketplace

`secuway-vpn` moved to the dedicated
[`mrcha033/secuway-vpn`](https://github.com/mrcha033/secuway-vpn)
repository and marketplace. Install and verify the standalone plugin before
removing the former `secuway-vpn@mrcha-skills` entry:

```bash
# Codex
codex plugin marketplace add mrcha033/secuway-vpn --ref v0.4.0
codex plugin add secuway-vpn@secuway-vpn
codex plugin list
codex plugin remove secuway-vpn@mrcha-skills

# Claude Code
claude plugin marketplace add mrcha033/secuway-vpn --scope user
claude plugin install secuway-vpn@secuway-vpn --scope user
claude plugin list
claude plugin uninstall secuway-vpn@mrcha-skills --scope user --keep-data
```

The standalone package retains the existing protected profile-storage names so
an in-place cache can be reused. Do not run `secuway forget` or a native runtime
uninstall during this marketplace migration; those are separate destructive
actions. Moving repositories does not bypass app OTP: first enrollment still
requires successful server-approved authentication.

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

Replace `advisor-review` with any plugin name above, or install multiple individual plugins before removing the bundle.

## Agent workflow skills

### Advisor Review

`advisor-review` asks an independent GPT-5.6 Sol route to challenge a plan, diagnose stalled work, assess a pivot, or audit completion. Version 0.2 adds same-task activation for explicit advisor language, source-anchored evidence and bounded artifacts, evidence-linked diagnostic output, and a required adopt/reject/defer decision record before the parent agent acts. The receipt distinguishes requested model/effort from serving-side identity, which the CLI currently leaves unverified.

### Agent Finish Line

`agent-finish-line` is a lightweight execution closer, not another verifier or task-management harness. It keeps the requested endpoint in focus, changes strategy after repeated unchanged failures, and ends with a shipped result or one concrete external blocker. Invoking it does not create contracts, receipts, gate ledgers, backlogs, or attempt-tracking files.

## Yonsei and personal workflow skills

### Yonsei Central Student Governance Counsel

`yonsei-central-student-governance-counsel` covers Yonsei University's Sinchon and International Campus undergraduate General Student Council central bodies and General Student Club Union central bodies. It rejects explicitly out-of-scope institutions, campuses, and graduate bodies; requires body-specific rules for subordinate or autonomous bodies; traces authority-controlled publication paths with deterministic public access and Insane Search fallback; keeps the two rule hierarchies separate; and turns a user's desired position into a cited meeting brief, motion, objection, or amendment.

The General Student Council corpus is sourced from the public Drive linked by the official Legislation Committee Instagram account. The packaged corpus contains the 2025-09-11 General Student Council Rules and six current published bylaws; the combined two-domain index contains 824 provisions.

### LearnUs Course Copilot

`learnus-course-copilot` uses an existing authenticated browser session instead of command-line credentials. It detects login boundaries, structures authorized course pages into materials, assignments, visible dates, and VOD entries, and refuses access-control or DRM bypass.

### Kakao Reply Reuse

`katok-reply-reuse` searches authorized user-authored KakaoTalk exemplars and returns exact reuse or explicit placeholder substitution with provenance. It abstains when no strong past reply exists and never sends automatically.

## Finance skills

### QTA migration

`quant-stock-technical` and `quant-stock-polling-trader` are no longer
distributed as marketplace skills. The QTA implementation and its automation
now live in a dedicated standalone project, where long-running market-data,
execution, ledger, and calibration jobs can be managed as one application.
Existing users should remove those two marketplace plugins after migrating
their local runtime; this repository does not publish a replacement plugin.

### Stock Scenario Story

`stock-scenario-story` runs only after validating a complete external QTA
result matching the legacy `quant-stock-technical/v1` contract, hides every
upstream number, researches the surrounding company and market context, and
tells a sourced, theatrical, numbers-free story purely for entertainment.

## Versioned release packages

Every marketplace skill is built from `skills/<name>/` into three versioned
assets on the [latest GitHub Release](https://github.com/mrcha033/skills/releases/latest):

| Asset | Intended use | Guaranteed layout |
| --- | --- | --- |
| `<name>-<version>.zip` | ChatGPT or Claude skill upload | One top-level `<name>/` directory containing `SKILL.md` and the complete skill |
| `<name>-<version>.skill` | Claude skill upload where the `.skill` extension is accepted | Byte-identical to the standalone ZIP |
| `<name>-plugin-<version>.zip` | Codex custom plugin or Claude Code plugin | Both plugin manifests at archive root plus `skills/<name>/` |

The layouts follow the current
[OpenAI Agent Skills upload guidance](https://help.openai.com/en/articles/20001066),
[OpenAI plugin package specification](https://developers.openai.com/plugins/build/plugins),
and [Claude custom skill guidance](https://support.claude.com/en/articles/12512198-how-to-create-custom-skills).

`release-manifest.json` records the runtime status, requirements, size, and
SHA-256 of every asset. `SHA256SUMS` provides an independently checkable digest
list. The old unversioned files under `downloads/` remain only as historical
backward-compatible snapshots; CI releases are the distribution source of
truth.

The compatibility guarantee is deliberately limited to package layout and byte
integrity. It does not invent a tool or login that the target runtime lacks.
For example, `advisor-review` needs a local authenticated Codex CLI,
`katok-reply-reuse` needs the authorized macOS KakaoTalk archive, and
`learnus-course-copilot` needs the user's authenticated local browser session.
Those three packages can be structurally inspected by a web uploader but cannot
complete their workflows in a web-only sandbox. See
[`release/catalog.json`](release/catalog.json) for every skill's declared
Codex, Claude Code, ChatGPT web, and Claude web status.

### Use in ChatGPT

Open the latest release, download the desired
`<name>-<version>.zip`, then use **Plugins → Skills → Create → Upload from your
computer** in an eligible ChatGPT workspace. A GitHub repository URL is not a
ChatGPT installation link.

### Upload to Claude

Download either `<name>-<version>.skill` or the byte-identical ZIP according to
the Claude upload surface you are using. Claude Code users should normally use
the marketplace commands above; the plugin ZIP is the portable dual-manifest
source bundle.

## Install as standalone Codex skills

If marketplace installation is unavailable, clone the repository and copy only the desired skill directory:

```bash
git clone https://github.com/mrcha033/skills.git mrcha-skills
cp -R mrcha-skills/skills/stock-scenario-story ~/.codex/skills/
cp -R mrcha-skills/skills/advisor-review ~/.codex/skills/
cp -R mrcha-skills/skills/agent-finish-line ~/.codex/skills/
cp -R mrcha-skills/skills/yonsei-central-student-governance-counsel ~/.codex/skills/
cp -R mrcha-skills/skills/learnus-course-copilot ~/.codex/skills/
cp -R mrcha-skills/skills/katok-reply-reuse ~/.codex/skills/
```

Start a new Codex task after copying. Invoke `$advisor-review` explicitly when
you want an independent review. For the finance story workflow, pass an
unmodified external QTA result matching `quant-stock-technical/v1` to
`$stock-scenario-story`.

## Standalone Claude Code skills

The core skill folders also follow the Agent Skills layout. Copy a desired folder into `~/.claude/skills/` or a project's `.claude/skills/` directory when you do not want marketplace installation.

## Publish an update

The checked-in skill under `skills/<name>/` is the canonical source. A release
update changes that source and only one version file:

1. Edit `skills/<name>/`, then bump that skill's `version` and the top-level
   `distributionVersion` in `release/catalog.json`.
2. Run
   `python3 -B scripts/sync_distribution.py --write --skill <name>`.
   This refreshes the self-contained plugin copy and derives both plugin
   manifest versions plus the Claude marketplace version from the catalog.
3. Run `python3 -B tests/test_release_packaging.py`, or let pull-request CI
   build and retain a fourteen-day preview containing every format.
4. After the change reaches `main`, create and push the exact tag
   `skills-v<distributionVersion>`. The release workflow rejects a mismatched
   tag, rebuilds all assets deterministically, and refuses to overwrite an
   existing versioned GitHub Release.

Users installed through the Codex or Claude Code marketplace can refresh the
marketplace and update normally. ChatGPT and Claude web uploads remain copied
artifacts, so those users must upload the newly versioned file; the platform
does not currently turn a local upload into a repository subscription.

## Validation

```bash
python3 -B skills/stock-scenario-story/scripts/validate_quant_handoff.py --self-test
python3 -B skills/stock-scenario-story/scripts/validate_story_text.py --self-test
python3 -B skills/advisor-review/scripts/build_context_packet.py --self-test
python3 -B skills/advisor-review/scripts/validate_advice.py --self-test
python3 -B skills/advisor-review/scripts/run_advisor.py --self-test
python3 -B skills/advisor-review/scripts/validate_decision.py --self-test
python3 -B skills/advisor-review/scripts/evaluate_advisor_behavior.py --self-test
python3 -B tests/test_agent_finish_line.py
python3 -B skills/learnus-course-copilot/scripts/self_test.py
python3 -B skills/katok-reply-reuse/scripts/rank_replies.py --self-test
python3 -B skills/yonsei-central-student-governance-counsel/scripts/self_test.py
python3 -B tests/test_handoff_integration.py
python3 -B tests/test_advisor_review.py
python3 -B scripts/sync_distribution.py --check
python3 -B tests/test_marketplace_packaging.py
python3 -B tests/test_release_packaging.py
claude plugin validate . --strict
```

No software license has been granted yet. Public availability does not grant permission to copy, modify, or redistribute the contents.
