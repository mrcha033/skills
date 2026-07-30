This release contains the versioned distribution packages declared in
`release/catalog.json`.

- Distribution 2.4.5 updates `quant-stock-technical` to 0.4.6. It adds
  read-only selected-candidate point-in-time outcomes and an explicitly
  survivor-biased 2-252-session Korean opening-hour walk-forward diagnostic.
  A flat initial true-range seed now initializes ADX at zero instead of making
  it permanently unavailable, and blocked calculations identify the exact
  missing feature. QTA2 remains research-only; the new diagnostics make no
  order calls and always report `live_enabled=false`.

- Distribution 2.4.4 updates `quant-stock-technical` to 0.4.5 and
  `quant-stock-polling-trader` to 0.5.4. It adds research-only QTA2 scoring
  from a complete 2026-07-28 first-hour universe diagnostic, turnover
  filtering, and a fresh nonnegative same-session benchmark gate. Screening
  is deterministic across serial and parallel execution, completes the
  measured 5,976-name universe in under a minute, and no longer prints the
  full screen to stdout. Authoritative strategy fills are persisted in an
  account/market position ledger and reconciled across session dates. QTA2
  remains shadow-only; broker API mutations and `live_enabled` remain zero and
  false.

- Distribution 2.4.3 updates `quant-stock-technical` to 0.4.4 and
  `quant-stock-polling-trader` to 0.5.3. The daily prepare stage now builds
  only KOSPI/KOSDAQ for a Korean session or NYSE/NASDAQ for a U.S. session.
  This removes the cross-market cutoff error that made a 01:00 Korean prepare
  depend on unfinished U.S. EOD, avoids duplicate market-data reads, and lets
  each job hold only its own market lock. Manifest v2 retains all four count
  keys with zero counts for inactive exchanges and enforces coverage across
  every active exchange. Shadow safety and optional ntfy behavior are
  unchanged.

- Distribution 2.4.2 updates `quant-stock-polling-trader` to 0.5.2. Daily
  preparation now starts at 01:00 in each market timezone, leaving eight hours
  before the Korean open and eight and a half hours before the U.S. open for
  the measured multi-hour four-exchange refresh. The systemd wrapper also
  supports optional ntfy completion/block notifications from a protected topic
  URL and token; it never logs either secret, never retries a worker, and never
  changes the worker return code. Broker API mutations remain zero and
  `live_enabled=false`.

- Distribution 2.4.1 updates `quant-stock-polling-trader` to 0.5.1. A KIS
  live/shadow U.S. snapshot now treats the broker's documented empty USD cash
  response as zero settled cash instead of blocking. The frozen FX field is
  explicitly not applicable only when both settled USD and U.S. positions are
  absent, so the deterministic planner emits no positive quantity and never
  counts automatic-exchange buying power. Conflicting cash or FX rows remain
  blocking, and all broker calls remain read-only.

- Distribution 2.4.0 updates `quant-stock-polling-trader` to 0.5.0. The
  default daily path is a direct prepare, snapshot/plan, entry, and
  reconciliation workflow. Approval-digest provenance, account-identity HMAC,
  trading-arm, and mandatory Toss/NH gates were removed; official calendars,
  completed EOD inputs, settled-cash sizing, deterministic plans,
  single-writer state, read-only shadow execution, and `live_enabled=false`
  remain.

- Distribution 2.3.6 updates `quant-stock-polling-trader` to 0.4.4. A repeated
  same-session prepare now verifies the stored provenance object and bound
  descriptor hash before reusing an existing receipt. Changed approved roots
  cause a fresh prepare while still `PREPARED`; changes after arming or session
  completion fail closed instead of silently reusing the prior receipt.

- Distribution 2.3.5 updates `quant-stock-polling-trader` to 0.4.3. The
  systemd execution wrapper now restricts every opened market lock to mode
  `0600` and verifies that mode before acquiring the lock, including lock
  files created by an older or manually invoked bundle.

- Distribution 2.3.4 updates `quant-stock-polling-trader` to 0.4.2. Prepare now
  verifies the immutable EOD receipt-to-build-spec hash, then derives a
  workflow-local build spec with the current stable coverage contract. An
  explicit coverage-policy change therefore takes effect without mutating or
  recollecting a completed EOD bundle.

- Distribution 2.3.3 updates `quant-stock-polling-trader` to 0.4.1. A new
  approved implementation may seed from a same-analysis-date EOD bundle only
  when its receipt is `READY`, date-matched, read-only, and non-symlinked.
  Incomplete and blocked candidates remain ineligible, preventing redundant
  same-day full-market refreshes without weakening provenance.

- Distribution 2.3.2 updates `quant-stock-technical` to 0.4.3. Complete KR and
  U.S. EOD cache files are no longer rewritten byte-for-byte during resumable
  runs. Changed, incomplete, trimmed, or invalid files still update
  atomically, and regression tests cover the no-op cache path.

- Distribution 2.3.1 updates `quant-stock-technical` to 0.4.2. Korean and
  U.S. EOD collectors now project normalized source descriptors back to the
  exact `qta-universe-build-spec/v2` input schema, so the derived `role`
  metadata used during validation cannot invalidate the collectors' own build
  spec. Both collectors have regression coverage for the exact round trip.

- Distribution 2.3.0 updates `quant-stock-polling-trader` to 0.4.0 and
  `quant-stock-technical` to 0.4.1. Recurring systemd jobs now use a stable
  daily config to produce the current official calendar, four-exchange EOD
  manifest/screen, KIS plus Toss/NH account snapshot, plan, account-bound
  shadow arm, first-hour session, and bounded reconciliation. The previous
  date-fixed EOD-only behavior is no longer presented as daily automation.
  KIS U.S. interior adjusted rows with invalid OHLC geometry are audited and
  excluded without interpolation; an invalid cutoff remains blocking. Daily
  entry remains nonpersistent, `live_enabled=false`, and API mutation count is
  fixed to zero.

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
