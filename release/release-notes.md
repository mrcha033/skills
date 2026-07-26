This release contains the versioned distribution packages declared in
`release/catalog.json`.

- Distribution 1.1.0 adds `quant-stock-polling-trader` as a separately
  installable, fail-closed KIS/Toss execution plugin with local-only credential
  and token checks.
- Distribution 1.2.0 updates `quant-stock-technical` to 0.3.0 with a resumable,
  read-only KIS Korean adjusted-EOD collector, KRX/KIS source snapshots,
  KOSPI/KOSDAQ benchmarks, effective tick catalog generation, and immutable
  bundle receipts.
- Distribution 1.3.0 adds `secuway-vpn` with native Windows amd64/arm64
  assets, pinned official OpenVPN setup, one-time UAC provisioning, DPAPI
  profile protection, and explicit live-tunnel evidence gates.
- `quant-stock-polling-trader` remains a separately installable, fail-closed
  KIS/Toss execution plugin with local-only credential and token checks.
- `<name>-<version>.zip`: standalone Agent Skill upload for ChatGPT or Claude.
- `<name>-<version>.skill`: byte-identical standalone archive with the Claude
  skill extension.
- `<name>-plugin-<version>.zip`: dual-manifest Codex and Claude Code plugin.
- `release-manifest.json` and `SHA256SUMS`: runtime requirements, artifact
  sizes, and integrity hashes.

Package compatibility covers archive layout and integrity. Read each package's
runtime status and requirements in `release-manifest.json` before installation.
