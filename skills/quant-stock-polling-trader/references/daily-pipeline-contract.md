# Daily shadow pipeline

`daily_pipeline.py` turns one stable configuration into the current market
date's artifacts. It supports only:

```text
broker=kis-live
mode=shadow
live_enabled=false
api_mutation_count=0
```

The stages are:

1. `prepare`: freeze the official market calendar, stop normally on
   `MARKET_CLOSED`, refresh official membership/KIS masters/adjusted EOD and
   benchmarks, build the four-exchange universe, and screen it.
2. `snapshot`: read KIS settled cash, positions, and open orders; merge any
   explicitly configured additional exposure files; create the execution
   policy and deterministic plan; and preview request serialization.
3. `entry`: start the deterministic first-hour shadow runner, then perform
   bounded read-only reconciliation.

There is no source-approval digest, provenance receipt, account-identity HMAC,
or trading-arm artifact in this workflow. Those artifacts are not needed for
a no-mutation shadow session and must not be made prerequisites.

## Stable configuration

Use `qta-daily-shadow-config/v2`:

```json
{
  "schema": "qta-daily-shadow-config/v2",
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
    "max_blocked_fraction": "0.02"
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
  "account_aliases": {
    "KR": "kis-live-kr",
    "US": "kis-live-us"
  },
  "prepare_complete_seconds_before_open": 600
}
```

Only the fields shown above are required. Two optional fields may be added:

```json
{
  "exposure_component_paths": [
    "/secure/qta/manual/{session_date}/toss.json",
    "/secure/qta/manual/{session_date}/nh.json"
  ],
  "exposure_component_max_age_seconds": 86400
}
```

If a path is configured, it must exist and pass validation. If no path is
configured, the pipeline uses the KIS account view and continues. It never
creates fake empty exposure files.

## State and restart behavior

The intent ledger is:

```text
RUNTIME_ROOT/state/<account-alias>/<market>/<session-date>
```

The workflow descriptor is
`RUNTIME_ROOT/current/<market>.json`, and date-specific artifacts are under
`RUNTIME_ROOT/workflows/<market>/<session-date>`.

The stages move through `PREPARED`, `PLANNED`, and `READY`. A same-date stage
may reuse its completed output. Entry writes an attempt marker before polling
and refuses a second entry for the same session.

The runner still keeps a single-writer ledger, treats ambiguous paper
mutations as `UNKNOWN`, and reconciles nonterminal states. These rules protect
paper-mode compatibility; daily shadow mode itself sends zero mutations.

## Status

Each stage writes one readable JSON status containing its stage, status,
market, session date, reason, details, timestamp, `live_enabled`, and
`api_mutation_count`. Status files are operational records, not signatures;
they do not contain receipt hashes.
