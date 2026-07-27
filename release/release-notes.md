This release contains the versioned distribution packages declared in
`release/catalog.json`.

- Distribution 2.2.1 updates `quant-stock-polling-trader` to 0.3.1. Generated
  systemd bundles now round-trip through the same strict execution reader,
  derived timer fields are no longer serialized into job inputs, Python path
  overrides are cleared before wrapper startup, stale output directories and
  symlink lock paths fail closed, and an explicit user request for activation
  must be completed through verified install/enable/start state rather than
  stopping at unit generation.

- Distribution 2.2.0 updates `agent-finish-line` to 0.2.0 as an
  instruction-only execution closer. Invocation no longer creates or requires
  contract, receipt, gate, backlog, state-signature, or attempt-tracking
  artifacts; formal records are produced only when explicitly requested or
  required by the target system.

- Distribution 2.1.0 updates `quant-stock-polling-trader` to 0.3.0 with a
  read-only KIS live/shadow account collector, freshness-checked Toss/NH
  exposure component merging, and an effectively uncapped 2,000,000 KRW
  shadow risk policy requested by the user.

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
- Distribution 1.4.0 updates `quant-stock-technical` to 0.4.0 with NYSE/NASDAQ
  adjusted-EOD acquisition and updates `quant-stock-polling-trader` to 0.2.0
  with hardened, inactive systemd user-unit generation for KR/US EOD and entry
  jobs.
- Distribution 1.4.1 updates `quant-stock-polling-trader` to 0.2.1 so generated
  systemd path directives use unquoted C-escaped absolute paths accepted by
  `systemd-analyze`, including paths with whitespace or percent specifiers.
- Distribution 2.0.0 removes `secuway-vpn` from this aggregate marketplace
  after moving it to the dedicated `mrcha033/secuway-vpn` repository. Install
  `secuway-vpn@secuway-vpn` and verify it before removing
  `secuway-vpn@mrcha-skills`; keep protected enrollment data during the
  marketplace migration.
- `<name>-<version>.zip`: standalone Agent Skill upload for ChatGPT or Claude.
- `<name>-<version>.skill`: byte-identical standalone archive with the Claude
  skill extension.
- `<name>-plugin-<version>.zip`: dual-manifest Codex and Claude Code plugin.
- `release-manifest.json` and `SHA256SUMS`: runtime requirements, artifact
  sizes, and integrity hashes.

Package compatibility covers archive layout and integrity. Read each package's
runtime status and requirements in `release-manifest.json` before installation.
