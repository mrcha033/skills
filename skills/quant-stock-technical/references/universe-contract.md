# Four-exchange universe contract

`qta-universe-manifest/v2` freezes a reproducible, screen-ready intersection
across KOSPI, KOSDAQ, NYSE, and NASDAQ. It is not a live quote feed and it does
not make every listed security eligible.

## Source boundary

Build each exchange from dated local snapshots:

- an official listing or symbol-directory snapshot;
- a broker symbol-master snapshot;
- explicit symbol-to-adjusted-EOD-CSV mappings;
- an explicit same-exchange benchmark CSV mapping;
- explicit, effective-dated tick contracts.

Hash every input file before parsing it. Keep the raw files immutable with the
result. Never query thousands of symbols one by one during the entry window.
Refresh and screen from completed T-1 data before the session, then pass only
the selected candidates to the polling trader.

Run the offline builder with:

```bash
python3 scripts/build_universe_manifest.py \
  --build-spec universe-build-spec.json \
  --output universe-manifest.json
```

The build spec has exactly:

```text
schema, as_of, analysis_date, official_sources, broker_sources, eod_catalog,
catalog_coverage_contract
```

`schema` is `qta-universe-build-spec/v1`. Supply exactly one official source
and one KIS broker source for each exchange. Every source descriptor has
exactly:

```text
source_id, provider, exchange, as_of, path, format, encoding,
delimiter, skip_rows, columns, normal_status_values
```

All inputs are local snapshot files. The built-in direct formats are:

| role | exchange | provider | format |
| --- | --- | --- | --- |
| official | KOSPI/KOSDAQ | `KRX` | `KRX_KIND_HTML` |
| official | NASDAQ | `NASDAQ_TRADER` | `NASDAQ_LISTED` |
| official | NYSE | `NASDAQ_TRADER` | `NASDAQ_OTHER` |
| broker | KOSPI/KOSDAQ | `KIS` | `KIS_KRX_MASTER` |
| broker | NASDAQ/NYSE | `KIS` | `KIS_OVERSEAS_MASTER` |

`KRX_KIND_HTML` consumes the CP949 KIND corporate-list HTML-table export.
`KIS_KRX_MASTER` consumes the raw domestic `.mst` layout, including its
exchange-specific trailing flag block. `KIS_OVERSEAS_MASTER` consumes the raw,
headerless 24-column tab-separated `.COD` file. Set `columns` to `{}` for
these built-ins. Generic normalized inputs may instead use `KRX_CSV`,
`KIS_CSV`, or `KIS_FIXED_WIDTH` with explicit logical-column mappings. Never
apply decoded-character fixed offsets copied from one raw KIS market to
another.

Acquire and timestamp snapshots outside the builder. Current official source
locations are:

```text
KRX KIND:
https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13

Nasdaq Trader:
https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt
https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt

KIS:
https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip
https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip
https://new.real.download.dws.co.kr/common/master/nasmst.cod.zip
https://new.real.download.dws.co.kr/common/master/nysmst.cod.zip
```

Check the provider's current documentation before automating downloads. The
builder deliberately performs no network access.

`eod_catalog` has exactly `source_id, provider, as_of, path, encoding`. Its CSV
header is exactly, in this order:

```text
exchange,canonical_symbol,data_symbol,broker_symbol,instrument_type,
benchmark_id,ticker_csv,benchmark_csv,tick_rule_id,tick_effective_date,
tick_reference_price,resolved_tick_size,source_name
```

The catalog is an explicit join contract, not a security master. A listed
symbol absent from it is excluded with `missing_eod_mapping`. Blank, invalid,
or nonexistent data and tick fields become per-symbol exclusion reasons rather
than guessed values.

`catalog_coverage_contract` is a mandatory readiness gate with exactly:

```json
{
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
}
```

Each minimum is a decimal greater than zero and at most one. The builder
canonicalizes it to a decimal string. Both ratios use the frozen official-row
count as their denominator and are evaluated independently for every exchange:

- `minimum_ratio_by_exchange` gates `catalog_mapped / official`. A value of
  `1` requires an EOD-catalog row for every official member.
- `minimum_screenable_ratio_by_exchange` gates `included / official`, after
  instrument classification, broker tradability, completed adjusted EOD,
  benchmark, file-hash, and tick-contract checks.

Both gates must pass. A catalog row alone never makes a symbol screenable, and
a high screenable ratio never excuses missing catalog mappings.
`screen_universe.py` independently recomputes both ratios from the hashed
manifest counts and rejects a forged `READY` manifest that misses either
minimum.

The normalized contract is copied into the manifest before `manifest_hash` is
computed. This makes lowering or changing a threshold visible in the manifest
hash. For a full-universe build, use `1` for all four catalog-mapping
minimums. Choose screenable minimums from an explicit coverage policy that
accounts for intentionally excluded security classes, and do not lower either
set of values merely to turn a sparse proof-of-concept catalog `READY`.

The canonical exchange mapping is:

| exchange | market | venue | currency | benchmark_id |
| --- | --- | --- | --- | --- |
| `KOSPI` | `KR` | `KRX` | `KRW` | `KOSPI_COMPOSITE` |
| `KOSDAQ` | `KR` | `KRX` | `KRW` | `KOSDAQ_COMPOSITE` |
| `NYSE` | `US` | `NYSE` | `USD` | `NYSE_COMPOSITE` |
| `NASDAQ` | `US` | `NASD` | `USD` | `NASDAQ_COMPOSITE` |

## Eligible instruments

Include only `COMMON`, `ADR`, and `REIT`; `ADR` is U.S.-only. Exclude ETFs,
ETNs, preferred shares, SPACs, units, rights, warrants, test issues, abnormal
listing-status issues, and any row that cannot be classified deterministically.

An otherwise eligible listing is still excluded when it lacks:

- an unambiguous broker-master match;
- confirmed broker tradability;
- a ticker CSV and benchmark CSV whose last shared completed date reaches the
  requested analysis date;
- file hashes and valid adjusted-OHLCV columns;
- an effective tick contract.

Every exclusion must carry sorted, explicit reason codes. Never silently shrink
the universe.

## Exact manifest

The top-level object has exactly:

```text
schema, build_status, as_of, analysis_date, source_hashes,
catalog_coverage_contract, instruments, exclusions, counts, manifest_hash
```

`schema` is `qta-universe-manifest/v2`. `build_status` is `READY` only when
every one of the four exchanges has at least one included instrument and meets
both hashed catalog-mapping and screenable-coverage minimums; otherwise it is
`BLOCKED`.

Each instrument has exactly:

```text
market, exchange, canonical_symbol, data_symbol, broker_symbol,
instrument_type, benchmark_id, currency, venue, ticker_csv, benchmark_csv,
tick_contract, source_name, ticker_csv_sha256, benchmark_csv_sha256,
broker_tradability_verified, official_source_id, broker_source_id
```

All paths are absolute in the emitted manifest. Symbols have separate
canonical, data-provider, and broker representations; never assume they are
identical.

The required tick object has exactly:

```json
{
  "schema": "qta-tick-contract/v1",
  "kind": "RESOLVED_PRICE_LADDER",
  "rule_id": "provider-rule-identifier",
  "effective_date": "2026-07-25",
  "reference_price": "123.45",
  "resolved_tick_size": "0.005"
}
```

The two numeric values are positive canonical decimal strings. Do not hard-code
one U.S. tick or one Korean tick for an entire exchange.

`source_hashes` is sorted by `source_id`. Each entry has exactly
`source_id, role, provider, exchange, as_of, path, sha256`; `exchange` may be
`GLOBAL` for a cross-exchange mapping. Hashes are lowercase SHA-256 values.

Each exclusion has exactly:

```text
exchange, canonical_symbol, broker_symbol, name, reasons,
official_source_id, broker_source_id
```

`broker_symbol` and `broker_source_id` may be null. `reasons` is a non-empty,
sorted, unique list.

`counts` has exact totals `official_rows, broker_rows, catalog_mapped_rows,
included, excluded` and a `by_exchange` object with all four exchanges. Each
exchange has exact `official, broker, catalog_mapped, included, excluded`
counts; totals must equal their sums. `catalog_mapped` counts official symbols
with a catalog row, regardless of whether that row later passes EOD, tick, or
broker validation. `excluded` means exclusion-record count. It can include an
EOD-catalog row that is no longer an official member, so it is not defined as
`official - included`.

`manifest_hash` is SHA-256 over UTF-8 canonical JSON for the entire object
excluding `manifest_hash`, using sorted keys, compact separators, and
non-ASCII characters unescaped.

## Reproducibility and history

Treat `as_of` as the membership/master snapshot date and `analysis_date` as the
completed EOD cutoff. Keep both dates and all hashes. Re-running from identical
snapshots must produce byte-identical JSON.

A current listing snapshot cannot reconstruct a historical universe. For
historical or walk-forward testing, use the membership and broker-master
snapshots that were actually frozen for each historical date; otherwise label
the result as survivorship-biased and do not use it as promotion evidence.
