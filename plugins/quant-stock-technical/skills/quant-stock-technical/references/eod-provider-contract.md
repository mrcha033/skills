# KIS Korean adjusted-EOD provider contract

`fetch_kis_kr_eod.py` fills the operational input gap between a frozen Korean
market session and the offline universe builder. It is a read-only, pre-open
data stage. It never belongs in the first-hour polling loop and never calls a
broker order, account, balance, fill, correction, or cancellation endpoint.

## Source contract

Freeze dated source masters first:

```bash
python3 scripts/fetch_kis_kr_eod.py snapshot-sources \
  --as-of 2026-07-27 \
  --output-directory /secure/qta/sources/2026-07-27/kr
```

The command downloads the KRX KIND corporate-list HTML export and the official
KIS KOSPI/KOSDAQ master ZIPs, verifies their expected format, extracts only the
named `.mst` members, hashes every file, and emits `source-snapshot.json`.
Review the provider URLs when they change; never silently replace a failed
official snapshot with a scraped or synthetic list.

## EOD job

Create one exact `qta-kis-kr-eod-job/v1` JSON object:

```json
{
  "schema": "qta-kis-kr-eod-job/v1",
  "as_of": "2026-07-27",
  "analysis_date": "2026-07-24",
  "environment": "live",
  "output_directory": "/secure/qta/eod/kr",
  "history_start_date": "2021-01-01",
  "minimum_sessions": 756,
  "request_interval_ms": 120,
  "official_sources": [],
  "broker_sources": [],
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
  "base_eod_catalog": ""
}
```

Copy the exact KOSPI/KOSDAQ descriptors from `source-snapshot.json` into the
two source arrays. Other exchange descriptors and a previously frozen
cross-market catalog may be included when producing the complete four-exchange
build spec. Relative paths resolve from the job file.

Run:

```bash
python3 scripts/fetch_kis_kr_eod.py collect --job kr-eod-job.json
```

The KIS domestic daily endpoint is queried with
`FID_ORG_ADJ_PRC=0`, which the KIS interface defines as adjusted price. Its
adjusted OHLC is written to the calculator schema with `adjusted_close` equal
to the returned adjusted `close`. The same cutoff is enforced for KOSPI index
code `0001` and KOSDAQ index code `1001`.

## Cache and completeness

- The first run walks backward using the broker's bounded page size. The stock
  endpoint currently returns up to 100 rows and the index endpoint may return
  50; completion is determined by the date cursor, never by assuming a short
  page is final.
- Each successful symbol CSV is written atomically and survives an interrupted
  bootstrap.
- Later runs reuse complete history and request only a missing cutoff.
- The exact `analysis_date` must exist in every admitted stock and benchmark.
- At least 756 sessions are required; fewer rows are a per-symbol failure, not
  permission to synthesize observations.
- A conflicting duplicate date, invalid OHLC geometry, malformed response,
  persistent rate limit, or missing completed candle is `BLOCKED`.
- The output catalog still records every official Korean member. Unsupported
  instruments and missing KIS matches remain explicit exclusions.

An individual new listing with fewer than 756 sessions does not make the
collector itself fail. Its catalog row remains present but has no admissible
EOD path or tick contract. The downstream hashed screenable-coverage policy,
not the collector, decides whether the complete market scope is `READY`.

The output directory contains:

```text
benchmarks/
stocks/KOSPI/
stocks/KOSDAQ/
eod-catalog.csv
universe-build-spec.json
eod-bundle-receipt.json
```

If only Korean source descriptors are supplied, the emitted build spec is a
Korean component and the existing four-exchange v2 builder will correctly
remain blocked until the NYSE/NASDAQ descriptors and catalog rows are merged.
Do not lower coverage thresholds to hide that fact. A Korean scheduled task may
consume only a separately approved Korean-scoped screen contract; do not forge
a four-exchange `READY` manifest.

## Credentials and mutation boundary

The collector resolves only KIS app key and secret from existing
`QTA_KIS_*` environment variables or the user-owned mode-0600 fallback already
used by the polling skill. An optional access token may be injected through
`QTA_KIS_ACCESS_TOKEN` or its scoped form; tokens are never written to disk or
receipts. When no token is injected, one token is requested for the collector
process. KIS limits token issuance to once per minute. Do not run a separate
token-only `auth-check` immediately before a collector process. Prefer using
the collector's first successful market-data response as the authentication
gate, or inject the same in-memory supervisor token through the process
environment. If KIS returns `EGW00133`, the collector waits 61 seconds and
retries token issuance exactly once.

Every receipt fixes `api_mutation_count` to zero. Authentication and quotation
reads do not arm live trading.

## Tick contract

The collector resolves the current KRX equity quotation unit from the final
completed adjusted close and records an effective, source-observed rule ID,
reference price, and tick size for every admitted symbol. KOSPI and KOSDAQ
remain distinct ladders. ETF, ETN, ELW, preferred, SPAC, right, warrant,
abnormal, halted, liquidation, and administrative issues are not promoted into
the common-stock screen.
