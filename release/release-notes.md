This release contains the versioned distribution packages declared in
`release/catalog.json`.

- Distribution 1.1.0 adds `quant-stock-polling-trader` as a separately
  installable, fail-closed KIS/Toss execution plugin with local-only credential
  and token checks.
- `<name>-<version>.zip`: standalone Agent Skill upload for ChatGPT or Claude.
- `<name>-<version>.skill`: byte-identical standalone archive with the Claude
  skill extension.
- `<name>-plugin-<version>.zip`: dual-manifest Codex and Claude Code plugin.
- `release-manifest.json` and `SHA256SUMS`: runtime requirements, artifact
  sizes, and integrity hashes.

Package compatibility covers archive layout and integrity. Read each package's
runtime status and requirements in `release-manifest.json` before installation.
