# Broker contracts

Checked against official sources on 2026-07-26. Re-run contract probes whenever
the broker changes an API version or order rule.

## Toss Securities Open API 1.2.4

Source of truth:
`https://openapi.tossinvest.com/openapi-docs/latest/openapi.json`.

Toss currently exposes one production REST server and no documented paper
server. Keep mutations disabled until the account's staged Open API access is
active.

Core paths:

```text
POST /oauth2/token
GET  /api/v1/accounts
GET  /api/v1/prices
GET  /api/v1/orderbook
GET  /api/v1/stocks
GET  /api/v1/holdings
GET  /api/v1/buying-power
GET  /api/v1/orders
POST /api/v1/orders
GET  /api/v1/orders/{orderId}
POST /api/v1/orders/{orderId}/modify
POST /api/v1/orders/{orderId}/cancel
POST /api/v1/conditional-orders
```

`GET /api/v1/stocks` accepts one to 200 requested symbols and returns market,
security type, common-share flag, listing status, currency, and Korean
liquidation/suspension details. It does not enumerate the full catalog. Once
this account has API access, query the frozen official-universe symbols in
deterministic batches, persist the complete responses as a dated snapshot, and
add a separate Toss-qualified universe contract. Until that snapshot path is
implemented and verified, never reuse the KIS-qualified v2 `broker_symbol` for
Toss.

Send `Authorization: Bearer ...`; account operations also require
`X-Tossinvest-Account: accountSeq`. Centralize token refresh because issuing a
new token invalidates the prior token for that client. If
`QTA_TOSS_ACCESS_TOKEN` supplies an external token, require its timezone-aware
ISO-8601 expiry in `QTA_TOSS_ACCESS_TOKEN_EXPIRES_AT`; block when exactly one
of the pair is present.

Keep `accountSeq` in the runtime credential environment. Do not copy it into a
plan, status file, or error message.

Bind every Toss mutation request hash to the uppercase HTTP method, exact API
path, account sequence header value, and canonical request body. An identical
body for another account or path must never share the same hash.

Pace `MARKET_DATA`, `ORDER`, and `ORDER_HISTORY` independently. The initial
client limits are 10, 3, and 5 TPS respectively; the conservative 3 TPS order
limit remains safe during the documented 09:00–09:10 KST peak. Only lower a
client limit when `X-RateLimit-Limit` reports a smaller current value. Do not
blindly retry an order mutation after a 429 or timeout.

After a mutation has been sent, an HTTP 5xx, malformed success body, or
success body without an order ID is ambiguous and must enter `UNKNOWN`.
Only an explicit structured Toss 4xx rejection is terminal `REJECTED`.

Both price and order-book source timestamps are nullable in the schema. Require
both during Toss polling, parse each as timezone-aware ISO-8601, and block the
entry when either is missing, invalid, future-dated beyond tolerance, or older
than the frozen quote-age limit. Select the lowest positive ask and highest
positive bid rather than trusting array position.

`clientOrderId` is optional on creates, limited to 36 safe characters, and
idempotent for only 10 minutes. A same-key/same-body retry returns the original
result; a changed body conflicts. Order queries cannot search or return that
client ID. Modify and cancel return new operation order IDs and have no
idempotency key. Never blindly retry them after a timeout.

Use decimal strings. KR orders are whole-share. U.S. fractional buys are
amount-based market orders and fractional sells are quantity-based market
orders during regular hours. Keep the deterministic price-capped path
whole-share and limit-only.

The current schema has a documented contradiction around
`GET /orders?status=CLOSED`. Treat both a successful page and
`closed-not-supported` as valid contract-probe outcomes; do not depend on
CLOSED history until the user's account confirms it.

Toss live status remains `BLOCKED`. Do not promote it until all three
mutation-lifecycle gaps are closed and fault-injected:

- recover an accepted-then-timeout create with the same `clientOrderId` and
  byte-equivalent body inside the 10-minute window, then reconcile it;
- verify the original order reaches a terminal state after a cancel operation,
  including cancel/fill races and rate limiting;
- register and continuously supervise the intended stop/take-profit protection
  after an entry fill.

## Korea Investment & Securities Open API

Official examples:
`https://github.com/koreainvestment/open-trading-api`.

```text
live  https://openapi.koreainvestment.com:9443
paper https://openapivts.koreainvestment.com:29443
POST  /oauth2/tokenP
```

KIS issues separate app keys and secrets for live and paper use. Resolve
process environment first, then an OS secret-store injection, then the local
`0600` `~/.config/mrcha-skills/secrets.env` fallback. Use the scoped
`QTA_KIS_PAPER_*` and `QTA_KIS_LIVE_*` names documented in `SKILL.md`; never
persist access tokens. The credential helper may prove token issuance, but it
must not submit an order or claim account/order readiness from OAuth alone.

Use `authorization`, `appkey`, `appsecret`, `tr_id`, and `custtype: P`.
Account requests contain `CANO` and `ACNT_PRDT_CD`.

Keep `CANO` and `ACNT_PRDT_CD` in the runtime credential environment. Do not
copy them into a plan, status file, or error message.

Core paths and current official TR IDs:

```text
KR quote       GET  /uapi/domestic-stock/v1/quotations/inquire-price
                    FHKST01010100
KR order book  GET  /uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn
                    FHKST01010200
KR balance     GET  /uapi/domestic-stock/v1/trading/inquire-balance
                    live TTTC8434R / paper VTTC8434R
KR open orders GET  /uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl
                    live TTTC0084R
KR fills       GET  /uapi/domestic-stock/v1/trading/inquire-daily-ccld
                    live TTTC0081R / paper VTTC0081R
KR buy         POST /uapi/domestic-stock/v1/trading/order-cash
                    live TTTC0012U / paper VTTC0012U
KR sell        POST same path
                    live TTTC0011U / paper VTTC0011U
KR modify/cancel POST /uapi/domestic-stock/v1/trading/order-rvsecncl
                    live TTTC0013U / paper VTTC0013U

US quote       GET  /uapi/overseas-price/v1/quotations/price
                    HHDFS00000300
US order book  GET  /uapi/overseas-price/v1/quotations/inquire-asking-price
                    live HHDFS76200100 / paper unsupported
US balance     GET  /uapi/overseas-stock/v1/trading/inquire-balance
                    live TTTS3012R / paper VTTS3012R
US present balance GET /uapi/overseas-stock/v1/trading/inquire-present-balance
                    live CTRP6504R / paper VTRP6504R
US fills       GET  /uapi/overseas-stock/v1/trading/inquire-ccnl
                    live TTTS3035R / paper VTTS3035R
US open orders GET  /uapi/overseas-stock/v1/trading/inquire-nccs
                    live TTTS3018R
US buy         POST /uapi/overseas-stock/v1/trading/order
                    live TTTT1002U / paper VTTT1002U
US sell        POST same path
                    live TTTT1006U / paper VTTT1001U
US modify/cancel POST /uapi/overseas-stock/v1/trading/order-rvsecncl
                    live TTTT1004U / paper VTTT1004U
```

KIS does not expose the local deterministic intent ID as a broker search key in
these order calls. Persist the request first. After an ambiguous submit,
reconcile orders, fills, and balances; do not automatically resubmit.
An HTTP 5xx, malformed success body, or successful response without `ODNO` is
ambiguous. Only a structured nonzero `rt_cd` with a broker `msg_cd` is an
authoritative rejection.

The two documented KIS domestic REST quote responses do not currently provide
a verified independent trading date, trade timestamp, and order-book
timestamp contract. The adapter refuses to synthesize the date from local
receipt time or reuse `aspr_acpt_hour` for both observations. Domestic KIS
polling therefore remains fail-closed unless a contract-probed response
supplies a consistent `stck_bsop_date`, `stck_cntg_hour`, and
`aspr_acpt_hour`, or a separately verified timestamp-bearing source is added.

Paper REST limits are lower than production limits and supported order types
can be narrower. The official U.S. paper examples support limit orders; use
whole-share limit orders for the initial contract.

The U.S. order-book TR `HHDFS76200100` is production-only. Do not combine a
production quote with a paper mutation in one automated session. Validate U.S.
trigger decisions through a mutation-free production shadow session, and test
the paper order request and response serialization separately with explicit
fixtures or a bounded contract probe. A U.S. KIS paper polling session must
return `BLOCKED` before fetching a quote.

For live U.S. shadow polling, use the one `HHDFS76200100` response only when it
contains last, bid, ask, `dymd`, and `dhms`. Assign its one source timestamp to
those values because they come from that same response. Do not fall back to the
separate price endpoint and label its value with the order-book timestamp.

Keep KIS live promotion disabled until all of these blockers are implemented
and fault-injected:

- paginate domestic and overseas order history using `tr_cont` and the
  `CTX_AREA_FK/NK100|200` continuation keys;
- recover accepted-then-timeout mutations by reconciling uniquely matched
  orders, fills, open orders, and balances without automatic resubmission;
- normalize domestic and overseas accepted, partial, filled, cancelled, and
  rejected records using the broker's full status fields;
- enforce a process-shared app-key/account rate limiter, including the
  temporary lower limit for newly registered production applications.

The read-only account collector currently supports only KIS live credentials
with shadow execution. It uses `TTTC8434R + TTTC0084R` for Korea and
`CTRP6504R + TTTS3018R` for the U.S. It follows documented continuation
headers and keys, freezes only after all reads complete, and never sends an
order, modify, or cancel request.
