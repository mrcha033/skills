# KIS U.S. adjusted-EOD provider contract

`fetch_kis_us_eod.py` produces the NYSE/NASDAQ half of the frozen four-exchange
catalog before the U.S. session. It performs authentication and quotation
reads only and fixes `api_mutation_count` to zero.

## Frozen membership and tradability

Run `snapshot-sources` with the source date. The command freezes and hashes:

- Nasdaq Trader `nasdaqlisted.txt` for official NASDAQ membership;
- Nasdaq Trader `otherlisted.txt`, filtered to exchange `N`, for official NYSE
  membership;
- KIS `nasmst.cod` and `nysmst.cod` for broker symbol qualification.

The admitted universe is the deterministic intersection after the existing
common-stock, ADR, REIT, ETF, test-issue, abnormal-status, and broker
tradability rules. Never substitute AMEX, ARCA, OTC, a search result, or an
unqualified symbol.

## Adjusted history

KIS overseas daily history uses
`/uapi/overseas-price/v1/quotations/dailyprice`, TR `HHDFS76240000`, daily
`GUBN=0`, and adjusted-price `MODP=1`. NYSE uses `EXCD=NYS`; NASDAQ uses
`EXCD=NAS`. The cursor walks backward from the completed cutoff and writes
atomic, resumable CSV files. Every admitted ticker and benchmark requires the
exact cutoff and at least 756 sessions.

KIS can return an interior adjusted row whose reported open, high, low, and
close do not form valid OHLC geometry. Exclude that exact provider row without
interpolation, record its exchange, symbol, date, normalized values, size, and
SHA-256 in `invalid-eod-rows.json`, and continue only if the remaining real
rows still meet the complete-session contract. An invalid row on the requested
cutoff remains `BLOCKED`; never invent the latest completed candle. The
receipt binds the audit file and reports policy
`EXCLUDED_WITHOUT_INTERPOLATION`.

The existing v2 universe contract names `NYSE_COMPOSITE` and
`NASDAQ_COMPOSITE`. Their public benchmark symbols are `^NYA` and `^IXIC`.
Benchmark downloads are source-labeled and hashed; a malformed, incomplete, or
rate-limited response blocks the benchmark rather than silently substituting
S&P 500, an ETF, or another index.

U.S. order-price resolution uses a conservative valid ladder: `0.0001` below
USD 1 and `0.01` at or above USD 1. A later version may consume an
effective-dated Regulation NMS variable tick table for finer price increments,
but it must not infer one from price alone.

## Cross-market completion

The U.S. job may include all four official and broker descriptors and set
`base_eod_catalog` to the completed Korean catalog. In that form the emitted
`universe-build-spec.json` is a complete four-exchange input. If the Korean
descriptors or catalog are absent, the output remains a U.S. component and
must not be represented as a complete v2 manifest.

The initial bootstrap belongs outside both first-hour entry windows. Completed
symbol files survive interruption; later runs fetch only the missing completed
cutoff.
