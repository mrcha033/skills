#!/usr/bin/env python3
"""Freeze KIS account state and merge verified cross-broker exposures."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from broker_adapters import HttpResponse, KisBroker, QueueTransport, kis_result
from broker_credentials import (
    load_kis_credentials,
    require_kis_runtime_credentials,
)
from execution_core import (
    BlockedError,
    canonical_json,
    normalized_account_snapshot,
    normalized_exposure_snapshot,
    parse_aware_datetime,
    sha256_file,
    sha256_json,
)

JOB_SCHEMA = "qta-account-snapshot-job/v2"
COMPONENT_SCHEMA = "qta-exposure-component/v1"
STATUS_SCHEMA = "qta-account-snapshot-status/v2"
MANIFEST_SCHEMA = "qta-universe-manifest/v2"
ACCOUNT_SCHEMA = "qta-account-snapshot/v2"
EXPOSURE_SCHEMA = "qta-exposure-snapshot/v2"

EXCHANGE_MARKET = {
    "KOSPI": "KR",
    "KOSDAQ": "KR",
    "NYSE": "US",
    "NASDAQ": "US",
}
US_EXCHANGE_CODES = {
    "NAS": "NASDAQ",
    "NASD": "NASDAQ",
    "NASDAQ": "NASDAQ",
    "NYS": "NYSE",
    "NYSE": "NYSE",
}


def exact_fields(value: dict[str, Any], required: set[str], label: str) -> None:
    missing = required - set(value)
    extra = set(value) - required
    if missing or extra:
        raise BlockedError(
            f"{label} fields mismatch: missing={sorted(missing)}, extra={sorted(extra)}"
        )


def decimal_value(value: Any, label: str) -> Decimal:
    if isinstance(value, bool):
        raise BlockedError(f"{label} must be a decimal")
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError) as exc:
        raise BlockedError(f"{label} must be a decimal") from exc
    if not result.is_finite():
        raise BlockedError(f"{label} must be finite")
    return result


def nonnegative_decimal(value: Any, label: str) -> Decimal:
    result = decimal_value(value, label)
    if result < 0:
        raise BlockedError(f"{label} must be nonnegative")
    return result


def positive_decimal(value: Any, label: str) -> Decimal:
    result = decimal_value(value, label)
    if result <= 0:
        raise BlockedError(f"{label} must be positive")
    return result


def read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BlockedError(f"{label} must be readable JSON: {path}") from exc
    if not isinstance(value, dict):
        raise BlockedError(f"{label} must be a JSON object")
    return value


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def validate_absolute_path(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise BlockedError(f"{label} must be a non-empty path")
    path = Path(value)
    if not path.is_absolute():
        raise BlockedError(f"{label} must be absolute")
    return path


def load_job(path: Path) -> dict[str, Any]:
    value = read_json_object(path, "account snapshot job")
    required = {
        "schema",
        "broker",
        "mode",
        "market",
        "account_alias",
        "universe_manifest",
        "fx_to_krw",
        "manual_exposure_components",
        "manual_component_max_age_seconds",
        "output_account_path",
        "output_exposure_path",
        "output_status_path",
    }
    exact_fields(value, required, "account snapshot job")
    if value["schema"] != JOB_SCHEMA:
        raise BlockedError(f"unsupported account snapshot job schema: {value['schema']!r}")
    if value["broker"] != "kis-live" or value["mode"] != "shadow":
        raise BlockedError("account collector currently requires kis-live/shadow")
    market = str(value["market"]).upper()
    if market not in {"KR", "US"}:
        raise BlockedError("job market must be KR or US")
    alias = value["account_alias"]
    if not isinstance(alias, str) or not alias.strip() or alias != alias.strip():
        raise BlockedError("account_alias must be a non-empty trimmed string")
    manifest = validate_absolute_path(value["universe_manifest"], "universe_manifest")
    raw_fx = value["fx_to_krw"]
    if market == "US" and raw_fx == "KIS_PRESENT_BALANCE":
        fx: Decimal | None = None
    else:
        fx = positive_decimal(raw_fx, "fx_to_krw")
    if market == "KR" and fx != 1:
        raise BlockedError("KR fx_to_krw must be 1")
    components = value["manual_exposure_components"]
    if not isinstance(components, list):
        raise BlockedError("manual_exposure_components must be an array")
    component_paths = [
        validate_absolute_path(item, f"manual_exposure_components[{index}]")
        for index, item in enumerate(components)
    ]
    max_age = value["manual_component_max_age_seconds"]
    if isinstance(max_age, bool) or not isinstance(max_age, int) or max_age <= 0:
        raise BlockedError("manual_component_max_age_seconds must be a positive integer")
    if max_age > 86400:
        raise BlockedError("manual_component_max_age_seconds must not exceed 86400")
    output_paths = {
        key: validate_absolute_path(value[key], key)
        for key in (
            "output_account_path",
            "output_exposure_path",
            "output_status_path",
        )
    }
    if len(set(output_paths.values())) != len(output_paths):
        raise BlockedError("account, exposure, and status output paths must differ")
    return {
        **value,
        "market": market,
        "universe_manifest": manifest,
        "fx_to_krw": fx,
        "manual_exposure_components": component_paths,
        "manual_component_max_age_seconds": max_age,
        **output_paths,
    }


def load_manifest_map(path: Path) -> tuple[dict[str, str], str]:
    manifest = read_json_object(path, "universe manifest")
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise BlockedError(f"universe manifest must use {MANIFEST_SCHEMA}")
    if manifest.get("build_status") != "READY":
        raise BlockedError("universe manifest build_status must be READY")
    supplied_hash = manifest.get("manifest_hash")
    if (
        not isinstance(supplied_hash, str)
        or len(supplied_hash) != 64
        or supplied_hash != sha256_json(
            {key: item for key, item in manifest.items() if key != "manifest_hash"}
        )
    ):
        raise BlockedError("universe manifest hash is invalid")
    instruments = manifest.get("instruments")
    if not isinstance(instruments, list):
        raise BlockedError("universe manifest instruments must be an array")
    mapping: dict[str, str] = {}
    for index, instrument in enumerate(instruments):
        if not isinstance(instrument, dict):
            raise BlockedError(f"manifest instruments[{index}] must be an object")
        symbol = str(instrument.get("broker_symbol", "")).strip().upper()
        exchange = str(instrument.get("exchange", "")).strip().upper()
        market = str(instrument.get("market", "")).strip().upper()
        if not symbol or exchange not in EXCHANGE_MARKET:
            raise BlockedError(f"manifest instruments[{index}] has invalid identity")
        if EXCHANGE_MARKET[exchange] != market:
            raise BlockedError(f"manifest instruments[{index}] exchange/market mismatch")
        key = f"{market}:{symbol}"
        previous = mapping.setdefault(key, exchange)
        if previous != exchange:
            raise BlockedError(f"broker symbol maps to multiple exchanges: {key}")
    return mapping, supplied_hash


def normalize_component(
    value: dict[str, Any],
    *,
    frozen_at: datetime,
    max_age_seconds: int,
    path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    required = {"schema", "broker", "source_kind", "source_as_of", "positions"}
    exact_fields(value, required, f"exposure component {path}")
    if value["schema"] != COMPONENT_SCHEMA:
        raise BlockedError(f"unsupported exposure component schema in {path}")
    broker = value["broker"]
    if broker not in {"toss", "nh"}:
        raise BlockedError(f"manual exposure component broker must be toss or nh: {path}")
    if value["source_kind"] not in {"broker-export", "verified-manual"}:
        raise BlockedError(f"unsupported manual source_kind in {path}")
    source_as_of = parse_aware_datetime(value["source_as_of"], f"{path} source_as_of")
    age = (frozen_at.astimezone(timezone.utc) - source_as_of.astimezone(timezone.utc)).total_seconds()
    if age < 0:
        raise BlockedError(f"manual exposure component is future-dated: {path}")
    if age > max_age_seconds:
        raise BlockedError(f"manual exposure component is stale: {path}")
    raw_positions = value["positions"]
    if not isinstance(raw_positions, list):
        raise BlockedError(f"manual exposure component positions must be an array: {path}")
    positions: list[dict[str, Any]] = []
    for index, item in enumerate(raw_positions):
        if not isinstance(item, dict):
            raise BlockedError(f"{path} positions[{index}] must be an object")
        exact_fields(
            item,
            {"market", "exchange", "symbol", "quantity", "market_value_krw"},
            f"{path} positions[{index}]",
        )
        exchange = str(item["exchange"]).upper()
        market = str(item["market"]).upper()
        if exchange not in EXCHANGE_MARKET or EXCHANGE_MARKET[exchange] != market:
            raise BlockedError(f"{path} positions[{index}] exchange/market mismatch")
        symbol = str(item["symbol"]).strip().upper()
        if not symbol:
            raise BlockedError(f"{path} positions[{index}] symbol is empty")
        quantity = item["quantity"]
        if quantity is not None:
            quantity = format(
                nonnegative_decimal(quantity, f"{path} positions[{index}].quantity"),
                "f",
            )
        market_value = format(
            nonnegative_decimal(
                item["market_value_krw"],
                f"{path} positions[{index}].market_value_krw",
            ),
            "f",
        )
        positions.append(
            {
                "broker": broker,
                "market": market,
                "exchange": exchange,
                "symbol": symbol,
                "quantity": quantity,
                "market_value_krw": market_value,
            }
        )
    return positions, {
        "broker": broker,
        "path": str(path),
        "sha256": sha256_file(path),
        "source_as_of": source_as_of.isoformat(),
        "position_count": len(positions),
    }


def response_list(body: dict[str, Any], field: str, label: str) -> list[dict[str, Any]]:
    raw = body.get(field, [])
    if raw in (None, ""):
        return []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise BlockedError(f"KIS {label} {field} must be an object array")
    return raw


def request_pages(
    broker: KisBroker,
    *,
    path: str,
    tr_id: str,
    query: dict[str, str],
    output_fields: tuple[str, ...],
    context_suffix: str | None,
    label: str,
) -> dict[str, list[dict[str, Any]]]:
    collected = {field: [] for field in output_fields}
    current_query = dict(query)
    continuation = ""
    for page in range(1, 51):
        headers = broker.headers(tr_id)
        if continuation:
            headers["tr_cont"] = "N"
        response = broker.transport.request(
            "GET",
            f"{broker.base_url}{path}",
            headers=headers,
            query=current_query,
            timeout_seconds=10,
        )
        body = kis_result(response, label)
        for field in output_fields:
            collected[field].extend(response_list(body, field, label))
        continuation = str(response.headers.get("tr_cont", "")).strip().upper()
        if continuation not in {"M", "F"}:
            return collected
        if context_suffix is None:
            raise BlockedError(f"KIS {label} requested unsupported continuation")
        if context_suffix == "":
            continue
        fk = str(body.get(f"ctx_area_fk{context_suffix}", "")).strip()
        nk = str(body.get(f"ctx_area_nk{context_suffix}", "")).strip()
        if not fk or not nk:
            raise BlockedError(f"KIS {label} continuation keys are missing")
        current_query[f"CTX_AREA_FK{context_suffix}"] = fk
        current_query[f"CTX_AREA_NK{context_suffix}"] = nk
    raise BlockedError(f"KIS {label} exceeded 50 continuation pages")


def manifest_exchange(
    mapping: dict[str, str],
    market: str,
    symbol: str,
    *,
    broker_exchange: Any = None,
) -> str:
    normalized_symbol = symbol.strip().upper()
    exchange = mapping.get(f"{market}:{normalized_symbol}")
    if exchange is None and market == "US":
        exchange = US_EXCHANGE_CODES.get(str(broker_exchange or "").strip().upper())
    if exchange is None:
        raise BlockedError(
            f"held or working symbol is absent from the selected-market map: "
            f"{market}:{normalized_symbol}"
        )
    return exchange


def side_from_kis(value: Any, label: str) -> str:
    code = str(value or "").strip()
    if code == "01":
        return "SELL"
    if code == "02":
        return "BUY"
    raise BlockedError(f"KIS {label} has unsupported side code: {code!r}")


def aggregate_positions(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals: dict[tuple[str, str, str], tuple[Decimal, Decimal]] = {}
    for item in positions:
        key = (item["market"], item["exchange"], item["symbol"])
        quantity, market_value = totals.get(key, (Decimal(0), Decimal(0)))
        totals[key] = (
            quantity + nonnegative_decimal(item["quantity"], "position quantity"),
            market_value
            + nonnegative_decimal(item["market_value_krw"], "position market value"),
        )
    return [
        {
            "market": market,
            "exchange": exchange,
            "symbol": symbol,
            "quantity": format(totals[(market, exchange, symbol)][0], "f"),
            "market_value_krw": format(totals[(market, exchange, symbol)][1], "f"),
        }
        for market, exchange, symbol in sorted(totals)
    ]


def minimum_nonnegative(rows: list[tuple[Any, str]]) -> Decimal:
    return min(max(decimal_value(value, label), Decimal(0)) for value, label in rows)


def collect_kr(
    broker: KisBroker,
    mapping: dict[str, str],
) -> tuple[Decimal, Decimal, list[dict[str, Any]], list[dict[str, Any]]]:
    balance = request_pages(
        broker,
        path="/uapi/domestic-stock/v1/trading/inquire-balance",
        tr_id="TTTC8434R",
        query={
            "CANO": broker.account_prefix,
            "ACNT_PRDT_CD": broker.account_product,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        },
        output_fields=("output1", "output2"),
        context_suffix="100",
        label="KR balance",
    )
    if not balance["output2"]:
        raise BlockedError("KIS KR balance summary is missing")
    summary = balance["output2"][-1]
    settled_cash = minimum_nonnegative(
        [
            (summary.get("dnca_tot_amt"), "KR dnca_tot_amt"),
            (summary.get("nxdy_excc_amt"), "KR nxdy_excc_amt"),
            (summary.get("prvs_rcdl_excc_amt"), "KR prvs_rcdl_excc_amt"),
        ]
    )
    borrowed = nonnegative_decimal(summary.get("tot_loan_amt", "0"), "KR tot_loan_amt")
    positions: list[dict[str, Any]] = []
    for row in balance["output1"]:
        quantity = nonnegative_decimal(row.get("hldg_qty"), "KR hldg_qty")
        if quantity == 0:
            continue
        symbol = str(row.get("pdno", "")).strip().upper()
        exchange = manifest_exchange(mapping, "KR", symbol)
        positions.append(
            {
                "market": "KR",
                "exchange": exchange,
                "symbol": symbol,
                "quantity": format(quantity, "f"),
                "market_value_krw": format(
                    nonnegative_decimal(row.get("evlu_amt"), "KR evlu_amt"), "f"
                ),
            }
        )
    orders = request_pages(
        broker,
        path="/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl",
        tr_id="TTTC0084R",
        query={
            "CANO": broker.account_prefix,
            "ACNT_PRDT_CD": broker.account_product,
            "INQR_DVSN_1": "1",
            "INQR_DVSN_2": "0",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        },
        output_fields=("output",),
        context_suffix="100",
        label="KR open orders",
    )
    open_orders: list[dict[str, Any]] = []
    for row in orders["output"]:
        remaining = nonnegative_decimal(row.get("psbl_qty"), "KR psbl_qty")
        if remaining == 0:
            continue
        symbol = str(row.get("pdno", "")).strip().upper()
        open_orders.append(
            {
                "side": side_from_kis(row.get("sll_buy_dvsn_cd"), "KR open order"),
                "market": "KR",
                "exchange": manifest_exchange(mapping, "KR", symbol),
                "symbol": symbol,
            }
        )
    return settled_cash, borrowed, positions, open_orders


def collect_us(
    broker: KisBroker,
    mapping: dict[str, str],
    fx_to_krw: Decimal | None,
) -> tuple[
    Decimal,
    Decimal,
    list[dict[str, Any]],
    list[dict[str, Any]],
    Decimal | None,
    str,
]:
    present = request_pages(
        broker,
        path="/uapi/overseas-stock/v1/trading/inquire-present-balance",
        tr_id="CTRP6504R",
        query={
            "CANO": broker.account_prefix,
            "ACNT_PRDT_CD": broker.account_product,
            "WCRC_FRCR_DVSN_CD": "02",
            "NATN_CD": "840",
            "TR_MKET_CD": "00",
            "INQR_DVSN_CD": "00",
        },
        output_fields=("output1", "output2", "output3"),
        context_suffix="",
        label="US present balance",
    )
    usd_cash_rows = [
        row
        for row in present["output2"]
        if str(row.get("crcy_cd", "")).strip().upper() == "USD"
    ]
    cash_snapshots: set[tuple[Decimal, Decimal, Decimal]] = set()
    observed_cash_fx: set[Decimal] = set()
    for index, cash in enumerate(usd_cash_rows):
        cash_snapshots.add(
            (
                nonnegative_decimal(
                    cash.get("frcr_dncl_amt_2"),
                    f"US output2[{index}].frcr_dncl_amt_2",
                ),
                nonnegative_decimal(
                    cash.get("frcr_drwg_psbl_amt_1"),
                    f"US output2[{index}].frcr_drwg_psbl_amt_1",
                ),
                nonnegative_decimal(
                    cash.get("nxdy_frcr_drwg_psbl_amt"),
                    f"US output2[{index}].nxdy_frcr_drwg_psbl_amt",
                ),
            )
        )
        if fx_to_krw is None:
            observed_cash_fx.add(
                positive_decimal(
                    cash.get("frst_bltn_exrt"),
                    f"US output2[{index}].frst_bltn_exrt",
                )
            )
    if len(cash_snapshots) > 1:
        raise BlockedError("KIS US present balance returned conflicting USD cash rows")
    if len(observed_cash_fx) > 1:
        raise BlockedError("KIS US present balance returned conflicting USD FX rates")
    settled_cash = min(next(iter(cash_snapshots))) if cash_snapshots else Decimal(0)

    position_rows: list[tuple[dict[str, Any], Decimal]] = []
    observed_position_fx: set[Decimal] = set()
    borrowed = Decimal(0)
    for index, row in enumerate(present["output1"]):
        quantity = nonnegative_decimal(
            row.get("cblc_qty13"), f"US output1[{index}].cblc_qty13"
        )
        borrowed += nonnegative_decimal(
            row.get("loan_rmnd", "0"), f"US output1[{index}].loan_rmnd"
        )
        if quantity == 0:
            continue
        position_rows.append((row, quantity))
        if fx_to_krw is None and not observed_cash_fx:
            observed_position_fx.add(
                positive_decimal(
                    row.get("bass_exrt"),
                    f"US output1[{index}].bass_exrt",
                )
            )
    if len(observed_position_fx) > 1:
        raise BlockedError(
            "KIS US present balance returned conflicting position FX rates"
        )

    if fx_to_krw is not None:
        effective_fx: Decimal | None = fx_to_krw
        fx_source = "JOB_INPUT"
    elif observed_cash_fx:
        effective_fx = next(iter(observed_cash_fx))
        fx_source = "KIS_PRESENT_BALANCE:frst_bltn_exrt"
    elif observed_position_fx:
        effective_fx = next(iter(observed_position_fx))
        fx_source = "KIS_PRESENT_BALANCE:bass_exrt"
    else:
        effective_fx = None
        fx_source = "NOT_APPLICABLE:NO_SETTLED_USD_OR_POSITION"

    positions: list[dict[str, Any]] = []
    for row, quantity in position_rows:
        if effective_fx is None:
            raise BlockedError("KIS US positions require a positive FX rate")
        symbol = str(row.get("pdno", "")).strip().upper()
        exchange = manifest_exchange(
            mapping,
            "US",
            symbol,
            broker_exchange=row.get("ovrs_excg_cd"),
        )
        market_value_usd = nonnegative_decimal(
            row.get("frcr_evlu_amt2"), "US frcr_evlu_amt2"
        )
        positions.append(
            {
                "market": "US",
                "exchange": exchange,
                "symbol": symbol,
                "quantity": format(quantity, "f"),
                "market_value_krw": format(market_value_usd * effective_fx, "f"),
            }
        )
    orders = request_pages(
        broker,
        path="/uapi/overseas-stock/v1/trading/inquire-nccs",
        tr_id="TTTS3018R",
        query={
            "CANO": broker.account_prefix,
            "ACNT_PRDT_CD": broker.account_product,
            "OVRS_EXCG_CD": "NASD",
            "SORT_SQN": "DS",
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        },
        output_fields=("output",),
        context_suffix="200",
        label="US open orders",
    )
    open_orders: list[dict[str, Any]] = []
    for row in orders["output"]:
        remaining = nonnegative_decimal(row.get("nccs_qty"), "US nccs_qty")
        if remaining == 0:
            continue
        symbol = str(row.get("pdno", "")).strip().upper()
        open_orders.append(
            {
                "side": side_from_kis(row.get("sll_buy_dvsn_cd"), "US open order"),
                "market": "US",
                "exchange": manifest_exchange(
                    mapping,
                    "US",
                    symbol,
                    broker_exchange=row.get("ovrs_excg_cd"),
                ),
                "symbol": symbol,
            }
        )
    return (
        settled_cash,
        borrowed,
        positions,
        open_orders,
        effective_fx,
        fx_source,
    )


def collect(
    job: dict[str, Any],
    *,
    transport: Any | None = None,
    frozen_at: datetime | None = None,
) -> dict[str, Any]:
    credential_status = load_kis_credentials("live")
    require_kis_runtime_credentials(credential_status)
    mapping, manifest_hash = load_manifest_map(job["universe_manifest"])
    broker = KisBroker(
        app_key=os.environ["QTA_KIS_APP_KEY"],
        app_secret=os.environ["QTA_KIS_APP_SECRET"],
        account_prefix=os.environ["QTA_KIS_ACCOUNT_PREFIX"],
        account_product=os.environ["QTA_KIS_ACCOUNT_PRODUCT"],
        environment="live",
        transport=transport,
    )
    if job["market"] == "KR":
        settled, borrowed, positions, open_orders = collect_kr(broker, mapping)
        effective_fx = Decimal(1)
        fx_source = "FIXED_KRW"
    else:
        (
            settled,
            borrowed,
            positions,
            open_orders,
            effective_fx,
            fx_source,
        ) = collect_us(
            broker,
            mapping,
            job["fx_to_krw"],
        )
    positions = aggregate_positions(positions)
    freeze_time = frozen_at or datetime.now(timezone.utc)
    if freeze_time.tzinfo is None:
        raise BlockedError("frozen_at must include a timezone")
    freeze_time = freeze_time.astimezone(timezone.utc)
    as_of = freeze_time.isoformat()
    account_positions = [
        {
            "market": item["market"],
            "exchange": item["exchange"],
            "symbol": item["symbol"],
            "quantity": item["quantity"],
            "market_value_krw": item["market_value_krw"],
        }
        for item in positions
    ]
    account = normalized_account_snapshot(
        {
            "schema": ACCOUNT_SCHEMA,
            "broker": "kis",
            "environment": "shadow",
            "account_alias": job["account_alias"],
            "market": job["market"],
            "currency": "KRW" if job["market"] == "KR" else "USD",
            "as_of": as_of,
            "settled_cash": format(settled, "f"),
            "borrowed_buying_power": format(borrowed, "f"),
            "fx_to_krw": (
                format(effective_fx, "f") if effective_fx is not None else None
            ),
            "positions": account_positions,
            "open_orders": open_orders,
        },
        screen_schema="qta-screen/v2",
    )
    exposure_positions = [
        {
            "broker": "kis",
            "market": item["market"],
            "exchange": item["exchange"],
            "symbol": item["symbol"],
            "quantity": item["quantity"],
            "market_value_krw": item["market_value_krw"],
        }
        for item in positions
    ]
    component_receipts: list[dict[str, Any]] = []
    component_brokers: set[str] = set()
    for path in job["manual_exposure_components"]:
        component = read_json_object(path, "manual exposure component")
        component_positions, component_receipt = normalize_component(
            component,
            frozen_at=freeze_time,
            max_age_seconds=job["manual_component_max_age_seconds"],
            path=path,
        )
        component_broker = component_receipt["broker"]
        if component_broker in component_brokers:
            raise BlockedError(
                f"manual exposure broker appears more than once: {component_broker}"
            )
        component_brokers.add(component_broker)
        exposure_positions.extend(component_positions)
        component_receipts.append(component_receipt)
    exposure_keys: set[tuple[str, str, str]] = set()
    for item in exposure_positions:
        key = (item["broker"], item["exchange"], item["symbol"])
        if key in exposure_keys:
            raise BlockedError(
                "duplicate cross-broker exposure position: " + ":".join(key)
            )
        exposure_keys.add(key)
    exposure = normalized_exposure_snapshot(
        {
            "schema": EXPOSURE_SCHEMA,
            "as_of": as_of,
            "positions": exposure_positions,
        }
    )
    atomic_write_json(job["output_account_path"], account)
    atomic_write_json(job["output_exposure_path"], exposure)
    receipt = {
        "schema": STATUS_SCHEMA,
        "status": "READY",
        "broker": "kis",
        "mode": "shadow",
        "market": job["market"],
        "as_of": as_of,
        "universe_manifest_hash": manifest_hash,
        "account_path": str(job["output_account_path"]),
        "account_sha256": sha256_file(job["output_account_path"]),
        "exposure_path": str(job["output_exposure_path"]),
        "exposure_sha256": sha256_file(job["output_exposure_path"]),
        "manual_components": component_receipts,
        "fx_to_krw": account["fx_to_krw"],
        "fx_source": fx_source,
        "settled_cash": account["settled_cash"],
        "borrowed_buying_power_excluded": True,
        "position_count": len(account["positions"]),
        "open_order_count": len(account["open_orders"]),
        "cross_broker_position_count": len(exposure["positions"]),
        "api_mutation_count": 0,
    }
    atomic_write_json(job["output_status_path"], receipt)
    return receipt


def manual_template(broker: str, source_as_of: str) -> dict[str, Any]:
    if broker not in {"toss", "nh"}:
        raise BlockedError("manual template broker must be toss or nh")
    parsed = parse_aware_datetime(source_as_of, "source_as_of")
    return {
        "schema": COMPONENT_SCHEMA,
        "broker": broker,
        "source_kind": "verified-manual",
        "source_as_of": parsed.isoformat(),
        "positions": [],
    }


def self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory_name:
        directory = Path(directory_name)
        now = datetime(2026, 7, 27, 0, 50, tzinfo=timezone.utc)
        manifest_without_hash = {
            "schema": MANIFEST_SCHEMA,
            "build_status": "READY",
            "instruments": [
                {"market": "KR", "exchange": "KOSPI", "broker_symbol": "005930"},
                {"market": "KR", "exchange": "KOSPI", "broker_symbol": "000660"},
                {"market": "US", "exchange": "NASDAQ", "broker_symbol": "AAPL"},
                {"market": "US", "exchange": "NYSE", "broker_symbol": "IBM"},
            ],
        }
        manifest = {
            **manifest_without_hash,
            "manifest_hash": sha256_json(manifest_without_hash),
        }
        manifest_path = directory / "manifest.json"
        atomic_write_json(manifest_path, manifest)
        manual_path = directory / "toss.json"
        atomic_write_json(
            manual_path,
            {
                "schema": COMPONENT_SCHEMA,
                "broker": "toss",
                "source_kind": "verified-manual",
                "source_as_of": "2026-07-27T09:45:00+09:00",
                "positions": [
                    {
                        "market": "US",
                        "exchange": "NASDAQ",
                        "symbol": "AAPL",
                        "quantity": "0.5",
                        "market_value_krw": "100000",
                    }
                ],
            },
        )
        job_raw = {
            "schema": JOB_SCHEMA,
            "broker": "kis-live",
            "mode": "shadow",
            "market": "KR",
            "account_alias": "kis-live-kr",
            "universe_manifest": str(manifest_path),
            "fx_to_krw": "1",
            "manual_exposure_components": [str(manual_path)],
            "manual_component_max_age_seconds": 3600,
            "output_account_path": str(directory / "account.json"),
            "output_exposure_path": str(directory / "exposure.json"),
            "output_status_path": str(directory / "status.json"),
        }
        job_path = directory / "job.json"
        atomic_write_json(job_path, job_raw)
        job = load_job(job_path)
        responses = [
            HttpResponse(
                200,
                {},
                {
                    "access_token": "fixture-token",
                    "token_type": "Bearer",
                    "expires_in": 86400,
                },
            ),
            HttpResponse(
                200,
                {},
                {
                    "rt_cd": "0",
                    "output1": [
                        {
                            "pdno": "005930",
                            "hldg_qty": "2",
                            "evlu_amt": "150000",
                        }
                    ],
                    "output2": [
                        {
                            "dnca_tot_amt": "2500000",
                            "nxdy_excc_amt": "2100000",
                            "prvs_rcdl_excc_amt": "2000000",
                            "tot_loan_amt": "5000000",
                        }
                    ],
                },
            ),
            HttpResponse(
                200,
                {},
                {
                    "rt_cd": "0",
                    "output": [
                        {
                            "pdno": "000660",
                            "psbl_qty": "1",
                            "sll_buy_dvsn_cd": "02",
                        }
                    ],
                },
            ),
        ]
        previous = {
            name: os.environ.get(name)
            for name in (
                "QTA_KIS_APP_KEY",
                "QTA_KIS_APP_SECRET",
                "QTA_KIS_ACCOUNT_PREFIX",
                "QTA_KIS_ACCOUNT_PRODUCT",
            )
        }
        os.environ.update(
            {
                "QTA_KIS_APP_KEY": "test-key",
                "QTA_KIS_APP_SECRET": "test-secret",
                "QTA_KIS_ACCOUNT_PREFIX": "12345678",
                "QTA_KIS_ACCOUNT_PRODUCT": "01",
            }
        )
        try:
            receipt = collect(
                job,
                transport=QueueTransport(responses),
                frozen_at=now,
            )
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        account = read_json_object(job["output_account_path"], "test account")
        exposure = read_json_object(job["output_exposure_path"], "test exposure")
        assert receipt["status"] == "READY"
        assert receipt["api_mutation_count"] == 0
        assert account["settled_cash"] == "2000000"
        assert account["borrowed_buying_power"] == "5000000"
        assert account["positions"][0]["exchange"] == "KOSPI"
        assert account["open_orders"][0]["side"] == "BUY"
        assert account["as_of"] == exposure["as_of"]
        assert len(exposure["positions"]) == 2

        us_job_raw = {
            **job_raw,
            "market": "US",
            "account_alias": "kis-live-us",
            "fx_to_krw": "KIS_PRESENT_BALANCE",
            "output_account_path": str(directory / "account-us.json"),
            "output_exposure_path": str(directory / "exposure-us.json"),
            "output_status_path": str(directory / "status-us.json"),
        }
        us_job_path = directory / "job-us.json"
        atomic_write_json(us_job_path, us_job_raw)
        us_responses = [
            HttpResponse(
                200,
                {},
                {
                    "access_token": "fixture-token",
                    "token_type": "Bearer",
                    "expires_in": 86400,
                },
            ),
            HttpResponse(
                200,
                {},
                {
                    "rt_cd": "0",
                    "output1": [
                        {
                            "pdno": "AAPL",
                            "ovrs_excg_cd": "NASD",
                            "cblc_qty13": "1.25",
                            "frcr_evlu_amt2": "250",
                            "loan_rmnd": "0",
                        }
                    ],
                    "output2": [
                        {
                            "crcy_cd": "USD",
                            "frcr_dncl_amt_2": "1500",
                            "frcr_drwg_psbl_amt_1": "1200",
                            "nxdy_frcr_drwg_psbl_amt": "1100",
                            "frst_bltn_exrt": "1400",
                        }
                    ],
                    "output3": [{}],
                },
            ),
            HttpResponse(
                200,
                {},
                {
                    "rt_cd": "0",
                    "output": [
                        {
                            "pdno": "IBM",
                            "ovrs_excg_cd": "NYSE",
                            "nccs_qty": "2",
                            "sll_buy_dvsn_cd": "01",
                        }
                    ],
                },
            ),
        ]
        previous_us = {
            name: os.environ.get(name)
            for name in (
                "QTA_KIS_APP_KEY",
                "QTA_KIS_APP_SECRET",
                "QTA_KIS_ACCOUNT_PREFIX",
                "QTA_KIS_ACCOUNT_PRODUCT",
            )
        }
        os.environ.update(
            {
                "QTA_KIS_APP_KEY": "test-key",
                "QTA_KIS_APP_SECRET": "test-secret",
                "QTA_KIS_ACCOUNT_PREFIX": "12345678",
                "QTA_KIS_ACCOUNT_PRODUCT": "01",
            }
        )
        try:
            us_receipt = collect(
                load_job(us_job_path),
                transport=QueueTransport(us_responses),
                frozen_at=now,
            )
        finally:
            for name, value in previous_us.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        us_account = read_json_object(Path(us_job_raw["output_account_path"]), "US account")
        assert us_receipt["status"] == "READY"
        assert us_account["settled_cash"] == "1100"
        assert us_account["positions"][0]["market_value_krw"] == "350000"
        assert us_account["fx_to_krw"] == "1400"
        assert us_receipt["fx_source"] == "KIS_PRESENT_BALANCE:frst_bltn_exrt"
        assert us_account["open_orders"][0]["exchange"] == "NYSE"
        assert us_account["open_orders"][0]["side"] == "SELL"

        empty_us_job_raw = {
            **us_job_raw,
            "manual_exposure_components": [],
            "output_account_path": str(directory / "account-us-empty.json"),
            "output_exposure_path": str(directory / "exposure-us-empty.json"),
            "output_status_path": str(directory / "status-us-empty.json"),
        }
        empty_us_job_path = directory / "job-us-empty.json"
        atomic_write_json(empty_us_job_path, empty_us_job_raw)
        empty_us_responses = [
            HttpResponse(
                200,
                {},
                {
                    "access_token": "fixture-token",
                    "token_type": "Bearer",
                    "expires_in": 86400,
                },
            ),
            HttpResponse(
                200,
                {},
                {
                    "rt_cd": "0",
                    "output1": [],
                    "output2": [],
                    "output3": [{"tot_frcr_cblc_smtl": "0"}],
                },
            ),
            HttpResponse(200, {}, {"rt_cd": "0", "output": []}),
        ]
        previous_empty_us = {
            name: os.environ.get(name)
            for name in (
                "QTA_KIS_APP_KEY",
                "QTA_KIS_APP_SECRET",
                "QTA_KIS_ACCOUNT_PREFIX",
                "QTA_KIS_ACCOUNT_PRODUCT",
            )
        }
        os.environ.update(
            {
                "QTA_KIS_APP_KEY": "test-key",
                "QTA_KIS_APP_SECRET": "test-secret",
                "QTA_KIS_ACCOUNT_PREFIX": "12345678",
                "QTA_KIS_ACCOUNT_PRODUCT": "01",
            }
        )
        try:
            empty_us_receipt = collect(
                load_job(empty_us_job_path),
                transport=QueueTransport(empty_us_responses),
                frozen_at=now,
            )
        finally:
            for name, value in previous_empty_us.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        empty_us_account = read_json_object(
            Path(empty_us_job_raw["output_account_path"]),
            "empty US account",
        )
        assert empty_us_receipt["status"] == "READY"
        assert empty_us_receipt["fx_source"] == (
            "NOT_APPLICABLE:NO_SETTLED_USD_OR_POSITION"
        )
        assert empty_us_account["settled_cash"] == "0"
        assert empty_us_account["fx_to_krw"] is None
        assert empty_us_account["positions"] == []
        assert empty_us_account["open_orders"] == []

        stale = read_json_object(manual_path, "manual")
        stale["source_as_of"] = "2026-07-25T09:00:00+09:00"
        try:
            normalize_component(
                stale,
                frozen_at=now,
                max_age_seconds=3600,
                path=manual_path,
            )
        except BlockedError:
            pass
        else:
            raise AssertionError("stale manual exposure component was accepted")
    return {
        "schema": "qta-account-snapshot-self-test/v1",
        "status": "PASS",
        "self_test": "PASS",
        "api_mutation_count": 0,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument("--job", type=Path, required=True)
    template_parser = subparsers.add_parser("manual-template")
    template_parser.add_argument("--broker", choices=("toss", "nh"), required=True)
    template_parser.add_argument("--source-as-of", required=True)
    template_parser.add_argument("--output", type=Path, required=True)
    subparsers.add_parser("self-test")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "collect":
            output = collect(load_job(args.job))
        elif args.command == "manual-template":
            if not args.output.is_absolute():
                raise BlockedError("--output must be absolute")
            output = manual_template(args.broker, args.source_as_of)
            atomic_write_json(args.output, output)
        else:
            output = self_test()
    except BlockedError as exc:
        print(
            canonical_json(
                {
                    "schema": "qta-account-snapshot-error/v1",
                    "status": "BLOCKED",
                    "reason": str(exc),
                    "api_mutation_count": 0,
                }
            )
        )
        return 2
    print(canonical_json(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
