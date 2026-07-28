# Daily shadow pipeline contract

`daily_pipeline.py` is the date-producing layer between recurring systemd
timers and the existing frozen execution artifacts. Use it for recurring
automation. Do not point a recurring timer directly at a date-stamped EOD job,
plan, arm, state directory, or receipt.

The only supported production pair is:

```text
broker=kis-live
mode=shadow
live_enabled=false
api_mutation_count=0
```

The three deterministic stages are:

1. `prepare` resolves the current date in the market timezone, freezes and
   hashes the official calendar, exits `MARKET_CLOSED` before KIS calls when
   appropriate, freezes KRX/Nasdaq Trader/KIS masters, updates resumable
   adjusted EOD, builds `qta-universe-manifest/v2`, and produces
   `qta-screen/v2`.
2. `snapshot` requires fresh Toss and NH components before KIS account calls,
   freezes the KIS account and cross-broker exposure, resolves the KIS-observed
   USD/KRW rate for the U.S. account, creates the deterministic plan, creates
   the account-bound shadow arm, and serializes every potential request.
3. `entry` validates provenance and state again, starts before the 30-second
   warm-up, runs the first-hour Python runner, and performs bounded read-only
   reconciliation. It never retries a mutation. Shadow mode has no order,
   correction, or cancellation mutation.

## Stable configuration

Supply one exact `qta-daily-shadow-config/v1` object at a stable absolute path:

```json
{
  "schema": "qta-daily-shadow-config/v1",
  "runtime_root": "/secure/qta",
  "broker": "kis-live",
  "mode": "shadow",
  "history_start_date": "2021-01-01",
  "minimum_sessions": 756,
  "request_interval_ms": 120,
  "catalog_coverage_contract": {
    "schema": "qta-catalog-coverage-contract/v1",
    "minimum_ratio_by_exchange": {
      "KOSPI": "1",
      "KOSDAQ": "1",
      "NYSE": "1",
      "NASDAQ": "1"
    },
    "minimum_screenable_ratio_by_exchange": {
      "KOSPI": "0.5",
      "KOSDAQ": "0.5",
      "NYSE": "0.5",
      "NASDAQ": "0.5"
    }
  },
  "selector": {
    "selector_version": "qta-screen-1.1.0",
    "min_total_score": "60",
    "eligible_setup_statuses": ["READY"],
    "top_k_by_exchange": {
      "KOSPI": 2,
      "KOSDAQ": 2,
      "NYSE": 2,
      "NASDAQ": 2
    },
    "min_selected_by_exchange": {
      "KOSPI": 0,
      "KOSDAQ": 0,
      "NYSE": 0,
      "NASDAQ": 0
    },
    "max_blocked_fraction": "0"
  },
  "risk_policy_path": "/secure/qta/config/risk-policy.json",
  "execution_policy": {
    "snapshot_max_age_seconds": 1800,
    "poll_interval_seconds": 2,
    "quote_max_age_seconds": 5,
    "max_spread_bps": "25",
    "max_gap_bps": "20",
    "trigger_mode": "CROSS_FROM_BELOW",
    "order_ttl_seconds": 30,
    "order_type": "LIMIT",
    "time_in_force": "DAY",
    "allow_partial_fill": true,
    "cancel_remainder_at_window_end": true
  },
  "manual_exposure_component_paths": [
    "/secure/qta/manual/{session_date}/toss.json",
    "/secure/qta/manual/{session_date}/nh.json"
  ],
  "required_manual_exposure_brokers": ["toss", "nh"],
  "manual_component_max_age_seconds": 86400,
  "account_aliases": {
    "KR": "kis-live-kr",
    "US": "kis-live-us"
  },
  "prepare_complete_seconds_before_open": 600,
  "approved_technical_version": "replace-with-installed-version",
  "approved_technical_tree_sha256": "replace-with-approved-lowercase-sha256",
  "approved_trader_version": "replace-with-installed-version",
  "approved_trader_tree_sha256": "replace-with-approved-lowercase-sha256"
}
```

Every field is required. The risk file must keep existing additions, borrowed
cash, margin, shorting, and automatic FX disabled. The U.S. FX rate is still
read from KIS present balance; this prohibition means the pipeline cannot
perform an FX conversion.

`{session_date}` is the only allowed path template. The files must be
`qta-exposure-component/v1` objects whose brokers are exactly `toss` and `nh`.
Missing, stale, duplicated, or unverified components block before the KIS
account snapshot. Never synthesize empty components merely to pass this gate.

## State and restart behavior

The only order-intent ledger directory is:

```text
RUNTIME_ROOT/<broker-account-identity-hash>/<market>/<session-date>
```

The implementation preserves an existing lower-case market directory created
by an earlier release and refuses simultaneous upper- and lower-case roots.
All other workflow, source, and EOD artifacts live outside that ledger path.
Before a new account snapshot, prior ledgers are inspected read-only. Existing
`MANUAL_BLOCK` stops entry. `UNKNOWN` and `CANCEL_PENDING` are reconciled at
15-second intervals for at most 20 attempts; an unresolved result is converted
to `MANUAL_BLOCK` with an emergency receipt.

The stable `current/kr.json` and `current/us.json` descriptors are replaced
atomically. They refer only to the current local session and contain hashes,
paths, and the HMAC account identity—not account numbers or credentials.
An existing current-session ledger is never entered a second time.

## Provenance

Before every stage, calculate the byte-ordered SHA-256 listing of the complete
skill tree. Ignore only `.pyc` files inside `__pycache__`; record them as
derived artifacts and never execute them. Reject symlinks, special files, and
bytecode outside `__pycache__`. Require the installed plugin name, version,
and source digest to match the stable config. Recheck source digests after
prepare, snapshot, and entry.

Run from an isolated, byte-identical staged plugin copy with:

```text
python3 -B -s
PYTHONDONTWRITEBYTECODE=1
PYTHONPATH unset
PYTHONHOME unset
```

The systemd generator enforces the Python flags and environment boundary.
The release/installation procedure is responsible for creating and verifying
the isolated staged plugin copy before generating the bundle.
