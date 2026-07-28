# Account snapshot collector contract

Use `scripts/account_snapshot.py` only for KIS live credentials in shadow mode.
It sends read-only requests and emits one `qta-account-snapshot/v2`, one
`qta-exposure-snapshot/v2`, and one readable status with
`api_mutation_count: 0`.

## Inputs

The job is nonsecret JSON. Every path must be absolute:

```json
{
  "schema": "qta-account-snapshot-job/v2",
  "broker": "kis-live",
  "mode": "shadow",
  "market": "KR",
  "account_alias": "kis-live-kr",
  "universe_manifest": "/secure/runtime/universe-manifest.json",
  "fx_to_krw": "1",
  "manual_exposure_components": [
    "/secure/runtime/exposure/toss.json",
    "/secure/runtime/exposure/nh.json"
  ],
  "manual_component_max_age_seconds": 1800,
  "output_account_path": "/secure/runtime/account-kr.json",
  "output_exposure_path": "/secure/runtime/exposure-kr.json",
  "output_status_path": "/secure/runtime/account-kr-status.json"
}
```

For a U.S. job, set `fx_to_krw` to `KIS_PRESENT_BALANCE` so the same
`CTRP6504R` snapshot supplies its positive `frst_bltn_exrt` value. A caller may
instead bind an exact observed positive numeric value, in which case the
receipt records `fx_source: JOB_INPUT`. Never insert a placeholder or silently
reuse an old rate. The collector validates the manifest hash and uses its
`broker_symbol -> exchange` mapping. Anything held or working outside KOSPI,
KOSDAQ, NYSE, and NASDAQ is `BLOCKED`; extend the exposure schema instead of
assigning a false exchange.

KIS credentials follow the resolution rules in `SKILL.md`. Account routing
values and secrets are never written. The nonsecret `account_alias` identifies
the local account view.

## Optional additional exposure

Toss and NH files are optional. Use them only when the user wants those
holdings included and has a fresh export or verified account view:

```bash
python3 scripts/account_snapshot.py manual-template \
  --broker toss \
  --source-as-of 2026-07-27T08:45:00+09:00 \
  --output /secure/runtime/exposure/toss.json
```

Each configured component has exactly:

```json
{
  "schema": "qta-exposure-component/v1",
  "broker": "toss",
  "source_kind": "verified-manual",
  "source_as_of": "2026-07-27T08:45:00+09:00",
  "positions": [
    {
      "market": "US",
      "exchange": "NASDAQ",
      "symbol": "AAPL",
      "quantity": "0.182408",
      "market_value_krw": "89096"
    }
  ]
}
```

`quantity` may be `null` only when the source genuinely omits it. Include
fractional holdings. The collector rejects invalid configured components, but
an empty `manual_exposure_components` list is valid and means “use KIS only.”
It does not scrape a browser, infer a symbol from a Korean display name, or
fabricate a source time.

## Cash derivation

The account contract requires settled, unborrowed cash:

- KR uses the minimum nonnegative value among KIS `dnca_tot_amt`,
  `nxdy_excc_amt`, and `prvs_rcdl_excc_amt`.
- US uses the minimum nonnegative USD value among `frcr_dncl_amt_2`,
  `frcr_drwg_psbl_amt_1`, and `nxdy_frcr_drwg_psbl_amt`.
- Loan amounts are preserved separately as `borrowed_buying_power` and never
  added to `settled_cash`.

This is a settlement-safe derivation. A new broker field or account product
must be contract-probed before changing it.

## Collection

```bash
python3 scripts/account_snapshot.py collect \
  --job /secure/runtime/account-snapshot-kr-job.json
```

The collector freezes one common `as_of` after the KIS reads and any configured
component validation complete. The account and exposure objects therefore
meet the planner's exact timestamp equality requirement. Missing or stale
configured inputs return `BLOCKED`; unconfigured external brokers do not.
