#!/usr/bin/env python3
"""Toss and KIS REST adapters with explicit request contracts."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from execution_core import (
    BlockedError,
    canonical_json,
    decimal_value,
    normalize_symbol,
    redact,
    sha256_json,
)


class TransportFailure(RuntimeError):
    """Network failure with no authoritative broker response."""


class AmbiguousMutationError(TransportFailure):
    """Mutation may have reached the broker; caller must reconcile."""


class AuthoritativeMutationRejection(BlockedError):
    """Broker explicitly rejected a mutation before returning an order ID."""


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: Any
    request_started_at: str | None = None
    received_at: str | None = None


class UrlLibTransport:
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        query: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        form_body: dict[str, Any] | None = None,
        timeout_seconds: float = 10.0,
        mutation: bool = False,
    ) -> HttpResponse:
        if query:
            url = f"{url}?{urlencode(query, doseq=True)}"
        request_headers = dict(headers or {})
        data: bytes | None = None
        if json_body is not None and form_body is not None:
            raise ValueError("json_body and form_body are mutually exclusive")
        if json_body is not None:
            data = canonical_json(json_body).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        elif form_body is not None:
            data = urlencode(form_body).encode("utf-8")
            request_headers.setdefault(
                "Content-Type", "application/x-www-form-urlencoded"
            )
        request = Request(url, data=data, headers=request_headers, method=method)
        request_started_at = datetime.now(timezone.utc).isoformat()
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read()
                body = json.loads(raw) if raw else None
                return HttpResponse(
                    status=response.status,
                    headers={
                        key.lower(): value for key, value in response.headers.items()
                    },
                    body=body,
                    request_started_at=request_started_at,
                    received_at=datetime.now(timezone.utc).isoformat(),
                )
        except HTTPError as exc:
            raw = exc.read()
            try:
                body = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                body = {"raw": raw.decode("utf-8", errors="replace")}
            return HttpResponse(
                status=exc.code,
                headers={key.lower(): value for key, value in exc.headers.items()},
                body=body,
                request_started_at=request_started_at,
                received_at=datetime.now(timezone.utc).isoformat(),
            )
        except (URLError, TimeoutError) as exc:
            if mutation:
                raise AmbiguousMutationError(str(exc)) from exc
            raise TransportFailure(str(exc)) from exc


class QueueTransport:
    """Deterministic fixture transport used only by self-tests."""

    def __init__(self, responses: list[HttpResponse | BaseException]):
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> HttpResponse:
        self.requests.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError("fixture transport exhausted")
        request_started_at = datetime.now(timezone.utc).isoformat()
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return replace(
            response,
            request_started_at=response.request_started_at or request_started_at,
            received_at=response.received_at or datetime.now(timezone.utc).isoformat(),
        )


class PacedTransport:
    """Serialize requests with a deterministic minimum interval."""

    def __init__(self, delegate: Any, minimum_interval_seconds: float):
        self.delegate = delegate
        self.minimum_interval_seconds = minimum_interval_seconds
        self.last_started: float | None = None

    def request(self, method: str, url: str, **kwargs: Any) -> HttpResponse:
        deadline_at = kwargs.pop("deadline_at", None)
        deadline = normalize_deadline(deadline_at)
        now = time.monotonic()
        remaining = 0.0
        if self.last_started is not None:
            remaining = self.minimum_interval_seconds - (now - self.last_started)
        if deadline is not None:
            projected_start = datetime.now(timezone.utc) + timedelta(
                seconds=max(remaining, 0.0)
            )
            if projected_start >= deadline:
                raise BlockedError(
                    "mutation pacing would cross the absolute submit deadline"
                )
        if remaining > 0:
            time.sleep(remaining)
        if deadline is not None and datetime.now(timezone.utc) >= deadline:
            raise BlockedError("absolute submit deadline elapsed before HTTP send")
        self.last_started = time.monotonic()
        request_started_at = datetime.now(timezone.utc).isoformat()
        response = self.delegate.request(method, url, **kwargs)
        return replace(
            response,
            request_started_at=response.request_started_at or request_started_at,
            received_at=response.received_at or datetime.now(timezone.utc).isoformat(),
        )


class MinimumIntervalRateLimiter:
    """Conservative single-process pacing for one broker rate-limit group."""

    def __init__(
        self,
        requests_per_second: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        if isinstance(requests_per_second, bool) or requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        self.minimum_interval_seconds = 1.0 / float(requests_per_second)
        self.monotonic = monotonic
        self.sleeper = sleeper
        self.last_started: float | None = None

    def acquire(self, *, deadline_at: datetime | None = None) -> None:
        deadline = normalize_deadline(deadline_at)
        now = self.monotonic()
        remaining = 0.0
        if self.last_started is not None:
            remaining = self.minimum_interval_seconds - (now - self.last_started)
        if deadline is not None:
            projected_start = datetime.now(timezone.utc) + timedelta(
                seconds=max(remaining, 0.0)
            )
            if projected_start >= deadline:
                raise BlockedError(
                    "mutation pacing would cross the absolute submit deadline"
                )
        if remaining > 0:
            self.sleeper(remaining)
        if deadline is not None and datetime.now(timezone.utc) >= deadline:
            raise BlockedError("absolute submit deadline elapsed before HTTP send")
        self.last_started = self.monotonic()

    def observe_headers(self, headers: dict[str, str]) -> None:
        normalized = {str(key).lower(): value for key, value in headers.items()}
        raw_limit = normalized.get("x-ratelimit-limit")
        if raw_limit is None:
            return
        try:
            observed_limit = float(raw_limit)
        except (TypeError, ValueError):
            return
        if observed_limit > 0:
            self.minimum_interval_seconds = max(
                self.minimum_interval_seconds,
                1.0 / observed_limit,
            )


def require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BlockedError(f"{label} must be an object")
    return value


def normalize_deadline(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise BlockedError("absolute submit deadline must be timezone-aware")
    return value.astimezone(timezone.utc)


def toss_result(response: HttpResponse, operation: str) -> Any:
    body = require_mapping(response.body, f"Toss {operation} response")
    if response.status < 200 or response.status >= 300:
        error = body.get("error")
        code = error.get("code") if isinstance(error, dict) else "unknown"
        raise BlockedError(f"Toss {operation} failed: HTTP {response.status} {code}")
    if "result" not in body:
        raise BlockedError(f"Toss {operation} response missing result")
    return body["result"]


def toss_mutation_result(
    response: HttpResponse,
    operation: str,
) -> dict[str, Any]:
    """Return an acknowledged Toss mutation or classify it fail-closed."""
    if response.status >= 500:
        raise AmbiguousMutationError(
            f"Toss {operation} may have been accepted: HTTP {response.status}"
        )
    if not isinstance(response.body, dict):
        raise AmbiguousMutationError(
            f"Toss {operation} returned a non-object mutation response"
        )
    body = response.body
    if response.status < 200 or response.status >= 300:
        error = body.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        if 400 <= response.status < 500 and str(code or "").strip():
            raise AuthoritativeMutationRejection(
                f"Toss {operation} rejected: HTTP {response.status} {code}"
            )
        raise AmbiguousMutationError(
            f"Toss {operation} returned no authoritative rejection: "
            f"HTTP {response.status}"
        )
    result = body.get("result")
    if not isinstance(result, dict):
        raise AmbiguousMutationError(
            f"Toss {operation} success response missing result object"
        )
    return result


def kis_result(response: HttpResponse, operation: str) -> dict[str, Any]:
    body = require_mapping(response.body, f"KIS {operation} response")
    if response.status != 200:
        raise BlockedError(f"KIS {operation} failed: HTTP {response.status}")
    if str(body.get("rt_cd")) != "0":
        raise BlockedError(
            f"KIS {operation} failed: {body.get('msg_cd', 'unknown')} "
            f"{body.get('msg1', '')}".strip()
        )
    return body


def kis_mutation_result(
    response: HttpResponse,
    operation: str,
) -> dict[str, Any]:
    """Return an acknowledged KIS mutation or classify it fail-closed."""
    if response.status >= 500:
        raise AmbiguousMutationError(
            f"KIS {operation} may have been accepted: HTTP {response.status}"
        )
    if not isinstance(response.body, dict):
        raise AmbiguousMutationError(
            f"KIS {operation} returned a non-object mutation response"
        )
    body = response.body
    if response.status != 200:
        code = body.get("error_code", body.get("msg_cd"))
        if 400 <= response.status < 500 and str(code or "").strip():
            raise AuthoritativeMutationRejection(
                f"KIS {operation} rejected: HTTP {response.status} {code}"
            )
        raise AmbiguousMutationError(
            f"KIS {operation} returned no authoritative rejection: "
            f"HTTP {response.status}"
        )
    result_code = str(body.get("rt_cd", "")).strip()
    if result_code != "0":
        message_code = str(body.get("msg_cd", "")).strip()
        if result_code and message_code:
            raise AuthoritativeMutationRejection(
                f"KIS {operation} rejected: {message_code} "
                f"{body.get('msg1', '')}".strip()
            )
        raise AmbiguousMutationError(
            f"KIS {operation} response missing an authoritative result code"
        )
    output = body.get("output")
    if not isinstance(output, dict):
        raise AmbiguousMutationError(
            f"KIS {operation} success response missing output object"
        )
    return output


def mutation_order_id(value: Any, label: str) -> str:
    """Require the acknowledgement identifier needed for reconciliation."""
    if isinstance(value, bool):
        raise AmbiguousMutationError(f"{label} is missing")
    order_id = str(value or "").strip()
    if not order_id:
        raise AmbiguousMutationError(f"{label} is missing")
    return order_id


def decimal_string(value: Any, field: str) -> str:
    return format(decimal_value(value, field), "f")


def parse_expiration_timestamp(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise BlockedError(f"{field} must be an ISO-8601 timestamp") from exc
    else:
        raise BlockedError(f"{field} must be an ISO-8601 timestamp")
    if parsed.tzinfo is None:
        raise BlockedError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def positive_level_prices(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise BlockedError(f"{label} must be a non-empty array")
    prices = []
    for index, level in enumerate(value):
        if not isinstance(level, dict) or "price" not in level:
            raise BlockedError(f"{label}[{index}] must contain price")
        price = decimal_value(level["price"], f"{label}[{index}].price")
        if price > 0:
            prices.append(price)
    if not prices:
        raise BlockedError(f"{label} has no positive price level")
    return prices


def kis_market_timestamp(
    *,
    date_value: Any,
    time_value: Any,
    timezone_name: str,
) -> str | None:
    """Parse a KIS market-local YYYYMMDD/HHMMSS timestamp, or fail closed."""
    compact_time = str(time_value or "").strip()
    if len(compact_time) != 6 or not compact_time.isdigit():
        return None
    compact_date = str(date_value or "").strip()
    zone = ZoneInfo(timezone_name)
    if len(compact_date) != 8 or not compact_date.isdigit():
        return None
    try:
        parsed = datetime.strptime(compact_date + compact_time, "%Y%m%d%H%M%S").replace(
            tzinfo=zone
        )
    except ValueError:
        return None
    return parsed.isoformat()


class TossBroker:
    LIVE_BASE_URL = "https://openapi.tossinvest.com"
    CREATE_ORDER_PATH = "/api/v1/orders"
    MARKET_DATA_TPS = 10
    ORDER_TPS = 3
    ORDER_HISTORY_TPS = 5

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        account_seq: int,
        transport: Any | None = None,
        access_token: str | None = None,
        access_token_expires_at: datetime | str | None = None,
        market_data_limiter: MinimumIntervalRateLimiter | None = None,
        order_limiter: MinimumIntervalRateLimiter | None = None,
        order_history_limiter: MinimumIntervalRateLimiter | None = None,
    ):
        if not client_id or not client_secret:
            raise BlockedError("Toss client_id and client_secret are required")
        if isinstance(account_seq, bool) or not isinstance(account_seq, int):
            raise BlockedError("Toss account_seq must be an integer")
        self.client_id = client_id
        self.client_secret = client_secret
        self.account_seq = account_seq
        self.transport = transport or UrlLibTransport()
        if access_token is not None and (
            not isinstance(access_token, str) or not access_token
        ):
            raise BlockedError("Toss access_token must be a non-empty string")
        if (access_token is None) != (access_token_expires_at is None):
            raise BlockedError(
                "Toss external access token and expiration timestamp "
                "must be provided together"
            )
        self.access_token = access_token
        self.token_expires_at = (
            None
            if access_token_expires_at is None
            else parse_expiration_timestamp(
                access_token_expires_at,
                "Toss access token expiration",
            )
        )
        self.market_data_limiter = market_data_limiter or MinimumIntervalRateLimiter(
            self.MARKET_DATA_TPS
        )
        self.order_limiter = order_limiter or MinimumIntervalRateLimiter(self.ORDER_TPS)
        self.order_history_limiter = (
            order_history_limiter or MinimumIntervalRateLimiter(self.ORDER_HISTORY_TPS)
        )

    @staticmethod
    def capabilities() -> dict[str, Any]:
        return {
            "broker": "toss",
            "supported_markets": ["KR", "US"],
            "paper": False,
            "quote_batch_max": 200,
            "batch_bid_ask": False,
            "order_types": ["LIMIT", "MARKET"],
            "initial_contract_order_types": ["LIMIT"],
            "client_order_id": True,
            "client_order_id_ttl_seconds": 600,
            "query_by_client_order_id": False,
            "modify_returns_new_id": True,
            "cancel_returns_new_id": True,
            "individual_fills": False,
            "conditional_types": ["SINGLE", "OCO", "OTO"],
            "client_rate_limits_tps": {
                "MARKET_DATA": TossBroker.MARKET_DATA_TPS,
                "ORDER": TossBroker.ORDER_TPS,
                "ORDER_HISTORY": TossBroker.ORDER_HISTORY_TPS,
            },
            "order_rate_limit_policy": "peak_safe",
        }

    def token(self) -> str:
        if (
            self.access_token
            and self.token_expires_at is not None
            and datetime.now(timezone.utc) + timedelta(seconds=60)
            < self.token_expires_at
        ):
            return self.access_token
        response = self.transport.request(
            "POST",
            f"{self.LIVE_BASE_URL}/oauth2/token",
            form_body={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout_seconds=10,
            mutation=False,
        )
        body = require_mapping(response.body, "Toss token response")
        if response.status != 200 or "access_token" not in body:
            raise BlockedError(
                f"Toss token request failed: HTTP {response.status} "
                f"{body.get('error', 'unknown')}"
            )
        expires_in = int(body.get("expires_in", 0))
        if expires_in <= 0:
            raise BlockedError("Toss token response has invalid expires_in")
        self.access_token = str(body["access_token"])
        self.token_expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=expires_in
        )
        return self.access_token

    def headers(self, *, account: bool) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.token()}"}
        if account:
            headers["X-Tossinvest-Account"] = str(self.account_seq)
        return headers

    def limited_request(
        self,
        limiter: MinimumIntervalRateLimiter,
        method: str,
        url: str,
        *,
        deadline_at: datetime | None = None,
        **kwargs: Any,
    ) -> HttpResponse:
        limiter.acquire(deadline_at=deadline_at)
        request_started_at = datetime.now(timezone.utc).isoformat()
        response = self.transport.request(method, url, **kwargs)
        limiter.observe_headers(response.headers)
        return replace(
            response,
            request_started_at=response.request_started_at or request_started_at,
            received_at=response.received_at or datetime.now(timezone.utc).isoformat(),
        )

    def mutation_request_hash(
        self,
        method: str,
        path: str,
        body: dict[str, Any],
    ) -> str:
        return sha256_json(
            {
                "method": method.upper(),
                "path": path,
                "account_seq": str(self.account_seq),
                "body": body,
            }
        )

    @staticmethod
    def build_order_body(intent: dict[str, Any]) -> dict[str, Any]:
        if intent.get("side") not in {"BUY", "SELL"}:
            raise BlockedError("Toss intent side must be BUY or SELL")
        if intent.get("order_type") != "LIMIT":
            raise BlockedError("initial Toss contract requires LIMIT")
        quantity = decimal_value(intent.get("quantity"), "quantity")
        if quantity <= 0 or quantity != quantity.to_integral_value():
            raise BlockedError("initial Toss contract requires positive whole quantity")
        client_order_id = str(intent.get("client_order_id", ""))
        if (
            not client_order_id
            or len(client_order_id) > 36
            or any(
                character
                not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
                for character in client_order_id
            )
        ):
            raise BlockedError("invalid Toss client_order_id")
        return {
            "clientOrderId": client_order_id,
            "symbol": normalize_symbol(str(intent["market"]), str(intent["symbol"])),
            "side": intent["side"],
            "orderType": "LIMIT",
            "timeInForce": "DAY",
            "quantity": format(quantity, "f"),
            "price": decimal_string(intent["limit_price"], "limit_price"),
            "confirmHighValueOrder": False,
        }

    @staticmethod
    def build_oco_body(intent: dict[str, Any], expire_date: str) -> dict[str, Any]:
        quantity = decimal_value(intent["quantity"], "quantity")
        if quantity != quantity.to_integral_value():
            raise BlockedError("Toss OCO quantity must be a whole number")
        take_profit = decimal_string(intent["take_profit_price"], "take_profit_price")
        stop = decimal_string(intent["stop_price"], "stop_price")
        return {
            "symbol": normalize_symbol(str(intent["market"]), str(intent["symbol"])),
            "type": "OCO",
            "quantity": format(quantity, "f"),
            "orderType": "LIMIT",
            "clientOrderId": f"oco-{str(intent['intent_id'])[:28]}",
            "expireDate": expire_date,
            "first": {
                "orderSide": "SELL",
                "triggerPrice": take_profit,
                "orderPrice": take_profit,
            },
            "second": {
                "orderSide": "SELL",
                "triggerPrice": stop,
                "orderPrice": stop,
            },
            "confirmHighValueOrder": False,
        }

    def preview_submit(self, intent: dict[str, Any]) -> dict[str, Any]:
        body = self.build_order_body(intent)
        request_hash = self.mutation_request_hash(
            "POST",
            self.CREATE_ORDER_PATH,
            body,
        )
        return {
            "method": "POST",
            "path": self.CREATE_ORDER_PATH,
            "headers": {
                "Authorization": "[REDACTED]",
                "X-Tossinvest-Account": "[REDACTED]",
            },
            "body": body,
            "request_hash": request_hash,
            "timeout_policy": "retry same clientOrderId and byte-equivalent body only within 600 seconds",
        }

    def submit(
        self,
        intent: dict[str, Any],
        *,
        deadline_at: datetime | None = None,
    ) -> dict[str, Any]:
        body = self.build_order_body(intent)
        request_hash = self.mutation_request_hash(
            "POST",
            self.CREATE_ORDER_PATH,
            body,
        )
        response = self.limited_request(
            self.order_limiter,
            "POST",
            f"{self.LIVE_BASE_URL}{self.CREATE_ORDER_PATH}",
            headers=self.headers(account=True),
            json_body=body,
            timeout_seconds=10,
            mutation=True,
            deadline_at=deadline_at,
        )
        result = toss_mutation_result(response, "submit")
        order_id = mutation_order_id(
            result.get("orderId"),
            "Toss submit result orderId",
        )
        return {
            "broker_order_id": order_id,
            "client_order_id": result.get("clientOrderId"),
            "request_hash": request_hash,
            "submit_started_at": response.request_started_at,
            "ack_received_at": response.received_at,
            "raw": redact(result),
        }

    def quote(self, market: str, symbol: str) -> dict[str, Any]:
        normalized = normalize_symbol(market, symbol)
        headers = self.headers(account=False)
        price_response = self.limited_request(
            self.market_data_limiter,
            "GET",
            f"{self.LIVE_BASE_URL}/api/v1/prices",
            headers=headers,
            query={"symbols": normalized},
            timeout_seconds=5,
        )
        prices = toss_result(price_response, "price")
        if not isinstance(prices, list):
            raise BlockedError("Toss price result must be an array")
        matching = [
            item
            for item in prices
            if isinstance(item, dict) and item.get("symbol") == normalized
        ]
        if len(matching) != 1:
            raise BlockedError(f"Toss price missing or duplicated: {normalized}")
        orderbook_response = self.limited_request(
            self.market_data_limiter,
            "GET",
            f"{self.LIVE_BASE_URL}/api/v1/orderbook",
            headers=headers,
            query={"symbol": normalized},
            timeout_seconds=5,
        )
        orderbook = require_mapping(
            toss_result(orderbook_response, "orderbook"), "Toss orderbook result"
        )
        asks = positive_level_prices(orderbook.get("asks"), "Toss asks")
        bids = positive_level_prices(orderbook.get("bids"), "Toss bids")
        return {
            "market": market,
            "symbol": normalized,
            "currency": matching[0].get("currency"),
            "last_price": decimal_string(matching[0].get("lastPrice"), "lastPrice"),
            "best_ask": format(min(asks), "f"),
            "best_bid": format(max(bids), "f"),
            "trade_timestamp": matching[0].get("timestamp"),
            "book_timestamp": orderbook.get("timestamp"),
            "received_at": datetime.now(timezone.utc).isoformat(),
            "raw_status": "OK",
        }

    def get_order(self, broker_order_id: str) -> dict[str, Any]:
        response = self.limited_request(
            self.order_history_limiter,
            "GET",
            f"{self.LIVE_BASE_URL}/api/v1/orders/{broker_order_id}",
            headers=self.headers(account=True),
            timeout_seconds=10,
        )
        return require_mapping(toss_result(response, "get order"), "Toss order result")

    def cancel(self, broker_order_id: str) -> dict[str, Any]:
        path = f"/api/v1/orders/{broker_order_id}/cancel"
        body: dict[str, Any] = {}
        request_hash = self.mutation_request_hash("POST", path, body)
        response = self.limited_request(
            self.order_limiter,
            "POST",
            f"{self.LIVE_BASE_URL}{path}",
            headers=self.headers(account=True),
            json_body=body,
            timeout_seconds=10,
            mutation=True,
        )
        result = toss_mutation_result(response, "cancel")
        operation_order_id = mutation_order_id(
            result.get("orderId"),
            "Toss cancel result operation orderId",
        )
        return {
            "original_order_id": broker_order_id,
            "operation_order_id": operation_order_id,
            "request_hash": request_hash,
            "raw": redact(result),
        }


class KisBroker:
    BASE_URLS: ClassVar[dict[str, str]] = {
        "live": "https://openapi.koreainvestment.com:9443",
        "paper": "https://openapivts.koreainvestment.com:29443",
    }
    KR_QUOTE_VENUE: ClassVar[dict[str, str]] = {
        "KRX": "J",
        "NXT": "NX",
        "SOR": "UN",
    }
    US_ORDER_VENUES: ClassVar[set[str]] = {"NASD", "NYSE", "AMEX"}
    US_QUOTE_VENUE: ClassVar[dict[str, str]] = {
        "NASD": "NAS",
        "NYSE": "NYS",
        "AMEX": "AMS",
    }

    def __init__(
        self,
        *,
        app_key: str,
        app_secret: str,
        account_prefix: str,
        account_product: str,
        environment: str,
        transport: Any | None = None,
        access_token: str | None = None,
    ):
        if environment not in self.BASE_URLS:
            raise BlockedError("KIS environment must be live or paper")
        if not app_key or not app_secret:
            raise BlockedError("KIS app_key and app_secret are required")
        if len(account_prefix) != 8 or not account_prefix.isdigit():
            raise BlockedError("KIS account_prefix must be 8 digits")
        if len(account_product) != 2 or not account_product.isdigit():
            raise BlockedError("KIS account_product must be 2 digits")
        self.app_key = app_key
        self.app_secret = app_secret
        self.account_prefix = account_prefix
        self.account_product = account_product
        self.environment = environment
        raw_transport = transport or UrlLibTransport()
        interval = (
            0.0 if transport is not None else (1.05 if environment == "paper" else 0.12)
        )
        self.transport = PacedTransport(raw_transport, interval)
        self.access_token = access_token

    @staticmethod
    def capabilities(environment: str) -> dict[str, Any]:
        if environment not in {"live", "paper"}:
            raise BlockedError("KIS environment must be live or paper")
        return {
            "broker": "kis",
            "environment": environment,
            "supported_markets": ["KR", "US"],
            "paper": True,
            "polling_order_book_markets": (
                ["KR"] if environment == "paper" else ["KR", "US"]
            ),
            "us_paper_polling_session": False,
            "quote_batch_max": 1,
            "batch_bid_ask": False,
            "order_types": ["LIMIT"],
            "client_order_id": False,
            "query_by_client_order_id": False,
            "modify_returns_new_id": False,
            "cancel_returns_new_id": False,
            "individual_fills": False,
            "server_oco": False,
            "whole_shares_only": True,
            "minimum_request_interval_ms": 1000 if environment == "paper" else 100,
        }

    @property
    def base_url(self) -> str:
        return self.BASE_URLS[self.environment]

    def token(self) -> str:
        if self.access_token:
            return self.access_token
        response = self.transport.request(
            "POST",
            f"{self.base_url}/oauth2/tokenP",
            json_body={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            },
            timeout_seconds=10,
        )
        body = require_mapping(response.body, "KIS token response")
        if response.status != 200 or not body.get("access_token"):
            raise BlockedError(
                f"KIS token request failed: HTTP {response.status} "
                f"{body.get('error_code', body.get('msg_cd', 'unknown'))}"
            )
        self.access_token = str(body["access_token"])
        return self.access_token

    def headers(self, tr_id: str) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self.token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
            "Content-Type": "application/json",
        }

    def order_tr_id(self, market: str, side: str) -> str:
        market = market.upper()
        side = side.upper()
        if market == "KR":
            live = "TTTC0012U" if side == "BUY" else "TTTC0011U"
            return live if self.environment == "live" else f"V{live[1:]}"
        if market == "US":
            if side == "BUY":
                return "TTTT1002U" if self.environment == "live" else "VTTT1002U"
            if side == "SELL":
                return "TTTT1006U" if self.environment == "live" else "VTTT1001U"
        raise BlockedError(f"unsupported KIS order market/side: {market}/{side}")

    def kr_quote_venue(self, venue: str) -> str:
        venue = venue.upper()
        if venue not in self.KR_QUOTE_VENUE:
            raise BlockedError("KR venue must be KRX, NXT, or SOR")
        if self.environment == "paper" and venue != "KRX":
            raise BlockedError("KIS paper KR quotes require KRX")
        return self.KR_QUOTE_VENUE[venue]

    def build_order_request(
        self, intent: dict[str, Any], *, venue: str
    ) -> dict[str, Any]:
        market = str(intent["market"]).upper()
        side = str(intent["side"]).upper()
        if side not in {"BUY", "SELL"}:
            raise BlockedError("KIS side must be BUY or SELL")
        if intent.get("order_type") != "LIMIT":
            raise BlockedError("initial KIS contract requires LIMIT")
        quantity = decimal_value(intent["quantity"], "quantity")
        if quantity <= 0 or quantity != quantity.to_integral_value():
            raise BlockedError("KIS stock quantity must be a positive whole number")
        symbol = normalize_symbol(market, str(intent["symbol"]))
        price = decimal_string(intent["limit_price"], "limit_price")
        tr_id = self.order_tr_id(market, side)
        if market == "KR":
            if venue not in {"KRX", "NXT", "SOR"}:
                raise BlockedError("KR venue must be KRX, NXT, or SOR")
            if self.environment == "paper" and venue != "KRX":
                raise BlockedError("KIS paper KR orders require KRX")
            body = {
                "CANO": self.account_prefix,
                "ACNT_PRDT_CD": self.account_product,
                "PDNO": symbol,
                "ORD_DVSN": "00",
                "ORD_QTY": format(quantity, "f"),
                "ORD_UNPR": price,
                "EXCG_ID_DVSN_CD": venue,
                "SLL_TYPE": "01" if side == "SELL" else "",
                "CNDT_PRIC": "",
            }
            path = "/uapi/domestic-stock/v1/trading/order-cash"
        else:
            if venue not in self.US_ORDER_VENUES:
                raise BlockedError("US venue must be NASD, NYSE, or AMEX")
            body = {
                "CANO": self.account_prefix,
                "ACNT_PRDT_CD": self.account_product,
                "OVRS_EXCG_CD": venue,
                "PDNO": symbol,
                "ORD_QTY": format(quantity, "f"),
                "OVRS_ORD_UNPR": price,
                "CTAC_TLNO": "",
                "MGCO_APTM_ODNO": "",
                "SLL_TYPE": "00" if side == "SELL" else "",
                "ORD_SVR_DVSN_CD": "0",
                "ORD_DVSN": "00",
            }
            path = "/uapi/overseas-stock/v1/trading/order"
        return {
            "method": "POST",
            "path": path,
            "tr_id": tr_id,
            "body": body,
            "request_hash": sha256_json({"tr_id": tr_id, "body": body}),
        }

    def preview_submit(self, intent: dict[str, Any], *, venue: str) -> dict[str, Any]:
        request = self.build_order_request(intent, venue=venue)
        return {
            **request,
            "headers": {
                "authorization": "[REDACTED]",
                "appkey": "[REDACTED]",
                "appsecret": "[REDACTED]",
                "tr_id": request["tr_id"],
                "custtype": "P",
            },
            "body": redact(request["body"]),
            "timeout_policy": "never resubmit automatically; reconcile orders, fills, and balances",
        }

    def submit(
        self,
        intent: dict[str, Any],
        *,
        venue: str,
        deadline_at: datetime | None = None,
    ) -> dict[str, Any]:
        request = self.build_order_request(intent, venue=venue)
        response = self.transport.request(
            "POST",
            f"{self.base_url}{request['path']}",
            headers=self.headers(request["tr_id"]),
            json_body=request["body"],
            timeout_seconds=10,
            mutation=True,
            deadline_at=deadline_at,
        )
        output = kis_mutation_result(response, "submit")
        order_id = mutation_order_id(
            output.get("ODNO"),
            "KIS submit output ODNO",
        )
        return {
            "broker_order_id": order_id,
            "request_hash": request["request_hash"],
            "broker_time": output.get("ORD_TMD"),
            "organization": output.get("KRX_FWDG_ORD_ORGNO"),
            "submit_started_at": response.request_started_at,
            "ack_received_at": response.received_at,
            "raw": redact(output),
        }

    def quote(self, market: str, symbol: str, *, venue: str) -> dict[str, Any]:
        market = market.upper()
        symbol = normalize_symbol(market, symbol)
        if market == "KR":
            quote_venue = self.kr_quote_venue(venue)
            tr_id = "FHKST01010100"
            price_response = self.transport.request(
                "GET",
                f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price",
                headers=self.headers(tr_id),
                query={
                    "FID_COND_MRKT_DIV_CODE": quote_venue,
                    "FID_INPUT_ISCD": symbol,
                },
                timeout_seconds=5,
            )
            price_body = kis_result(price_response, "KR price")
            output = require_mapping(price_body.get("output"), "KIS KR price output")
            book_tr = "FHKST01010200"
            book_response = self.transport.request(
                "GET",
                f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
                headers=self.headers(book_tr),
                query={
                    "FID_COND_MRKT_DIV_CODE": quote_venue,
                    "FID_INPUT_ISCD": symbol,
                },
                timeout_seconds=5,
            )
            book_body = kis_result(book_response, "KR orderbook")
            book = require_mapping(book_body.get("output1"), "KIS KR orderbook output")
            received_at = datetime.now(timezone.utc)
            price_date = str(output.get("stck_bsop_date") or "").strip()
            book_date = str(book.get("stck_bsop_date") or "").strip()
            if any(
                len(value) != 8 or not value.isdigit()
                for value in (price_date, book_date)
            ):
                raise BlockedError(
                    "KIS KR price and orderbook responses each require a valid "
                    "broker-supplied stck_bsop_date"
                )
            if price_date != book_date:
                raise BlockedError(
                    "KIS KR price and orderbook stck_bsop_date values must match"
                )
            trade_timestamp = kis_market_timestamp(
                date_value=price_date,
                time_value=output.get("stck_cntg_hour"),
                timezone_name="Asia/Seoul",
            )
            book_timestamp = kis_market_timestamp(
                date_value=book_date,
                time_value=book.get("aspr_acpt_hour"),
                timezone_name="Asia/Seoul",
            )
            if trade_timestamp is None or book_timestamp is None:
                raise BlockedError(
                    "KIS KR quote response requires independent broker-supplied "
                    "stck_cntg_hour and aspr_acpt_hour timestamps"
                )
            last = output.get("stck_prpr")
            if last is None:
                raise BlockedError("KIS KR price response missing last price")
            return {
                "market": "KR",
                "symbol": symbol,
                "currency": "KRW",
                "last_price": decimal_string(last, "stck_prpr"),
                "best_ask": decimal_string(book.get("askp1"), "askp1"),
                "best_bid": decimal_string(book.get("bidp1"), "bidp1"),
                "trade_timestamp": trade_timestamp,
                "book_timestamp": book_timestamp,
                "received_at": received_at.isoformat(),
                "source_timestamp_raw": {
                    "trade": {
                        "stck_bsop_date": price_date,
                        "stck_cntg_hour": output.get("stck_cntg_hour"),
                    },
                    "book": {
                        "stck_bsop_date": book_date,
                        "aspr_acpt_hour": book.get("aspr_acpt_hour"),
                    },
                },
                "raw_status": "OK",
            }
        if self.environment == "paper":
            raise BlockedError(
                "KIS U.S. paper order book HHDFS76200100 is unsupported; "
                "use production shadow quotes and separate paper serialization tests"
            )
        if venue not in self.US_ORDER_VENUES:
            raise BlockedError("US venue must be NASD, NYSE, or AMEX")
        quote_venue = self.US_QUOTE_VENUE[venue]
        book_tr = "HHDFS76200100"
        book_response = self.transport.request(
            "GET",
            f"{self.base_url}/uapi/overseas-price/v1/quotations/inquire-asking-price",
            headers=self.headers(book_tr),
            query={"AUTH": "", "EXCD": quote_venue, "SYMB": symbol},
            timeout_seconds=5,
        )
        book_body = kis_result(book_response, "US orderbook")
        book = require_mapping(book_body.get("output1"), "KIS US orderbook output")
        last = book.get("last")
        ask = book.get("pask1") or book.get("pask")
        bid = book.get("pbid1") or book.get("pbid")
        if last is None or ask is None or bid is None:
            raise BlockedError(
                "KIS US orderbook response missing same-source last/bid/ask"
            )
        received_at = datetime.now(timezone.utc)
        source_timestamp = kis_market_timestamp(
            date_value=book.get("dymd"),
            time_value=book.get("dhms"),
            timezone_name="America/New_York",
        )
        if source_timestamp is None:
            raise BlockedError(
                "KIS US orderbook response requires broker-supplied dymd/dhms"
            )
        return {
            "market": "US",
            "symbol": symbol,
            "currency": "USD",
            "last_price": decimal_string(last, "last"),
            "best_ask": decimal_string(ask, "best ask"),
            "best_bid": decimal_string(bid, "best bid"),
            "trade_timestamp": source_timestamp,
            "book_timestamp": source_timestamp,
            "received_at": received_at.isoformat(),
            "source_timestamp_raw": {
                "dymd": book.get("dymd"),
                "dhms": book.get("dhms"),
            },
            "raw_status": "OK",
        }

    def build_cancel_request(
        self,
        *,
        market: str,
        broker_order_id: str,
        symbol: str,
        quantity: str,
        price: str,
        venue: str,
        organization: str = "",
    ) -> dict[str, Any]:
        market = market.upper()
        quantity = decimal_string(quantity, "cancel quantity")
        price = decimal_string(price, "cancel price")
        if market == "KR":
            if venue not in {"KRX", "NXT", "SOR"}:
                raise BlockedError("KR venue must be KRX, NXT, or SOR")
            tr_id = "TTTC0013U" if self.environment == "live" else "VTTC0013U"
            path = "/uapi/domestic-stock/v1/trading/order-rvsecncl"
            body = {
                "CANO": self.account_prefix,
                "ACNT_PRDT_CD": self.account_product,
                "KRX_FWDG_ORD_ORGNO": organization,
                "ORGN_ODNO": broker_order_id,
                "ORD_DVSN": "00",
                "RVSE_CNCL_DVSN_CD": "02",
                "ORD_QTY": quantity,
                "ORD_UNPR": price,
                "QTY_ALL_ORD_YN": "Y",
                "EXCG_ID_DVSN_CD": venue,
            }
        elif market == "US":
            if venue not in self.US_ORDER_VENUES:
                raise BlockedError("US venue must be NASD, NYSE, or AMEX")
            tr_id = "TTTT1004U" if self.environment == "live" else "VTTT1004U"
            path = "/uapi/overseas-stock/v1/trading/order-rvsecncl"
            body = {
                "CANO": self.account_prefix,
                "ACNT_PRDT_CD": self.account_product,
                "OVRS_EXCG_CD": venue,
                "PDNO": normalize_symbol("US", symbol),
                "ORGN_ODNO": broker_order_id,
                "RVSE_CNCL_DVSN_CD": "02",
                "ORD_QTY": quantity,
                "OVRS_ORD_UNPR": "0",
                "MGCO_APTM_ODNO": "",
                "ORD_SVR_DVSN_CD": "0",
            }
        else:
            raise BlockedError(f"unsupported market: {market}")
        return {
            "method": "POST",
            "path": path,
            "tr_id": tr_id,
            "body": body,
            "request_hash": sha256_json({"tr_id": tr_id, "body": body}),
        }

    def cancel(self, **kwargs: Any) -> dict[str, Any]:
        request = self.build_cancel_request(**kwargs)
        response = self.transport.request(
            "POST",
            f"{self.base_url}{request['path']}",
            headers=self.headers(request["tr_id"]),
            json_body=request["body"],
            timeout_seconds=10,
            mutation=True,
        )
        output = kis_mutation_result(response, "cancel")
        operation_order_id = mutation_order_id(
            output.get("ODNO"),
            "KIS cancel output ODNO",
        )
        return {
            "original_order_id": kwargs["broker_order_id"],
            "operation_order_id": operation_order_id,
            "request_hash": request["request_hash"],
            "raw": redact(output),
        }

    @staticmethod
    def _case_value(record: dict[str, Any], *names: str) -> Any:
        lowered = {str(key).lower(): value for key, value in record.items()}
        for name in names:
            if name.lower() in lowered:
                return lowered[name.lower()]
        return None

    @staticmethod
    def _same_order_id(left: Any, right: Any) -> bool:
        a = str(left or "").strip()
        b = str(right or "").strip()
        if not a or not b:
            return False
        return a == b or a.lstrip("0") == b.lstrip("0")

    def order_history(
        self,
        *,
        market: str,
        trading_date: str,
        symbol: str,
        venue: str,
    ) -> list[dict[str, Any]]:
        market = market.upper()
        compact_date = trading_date.replace("-", "")
        if len(compact_date) != 8 or not compact_date.isdigit():
            raise BlockedError("trading_date must be YYYY-MM-DD")
        if market == "KR":
            tr_id = "TTTC0081R" if self.environment == "live" else "VTTC0081R"
            response = self.transport.request(
                "GET",
                f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
                headers=self.headers(tr_id),
                query={
                    "CANO": self.account_prefix,
                    "ACNT_PRDT_CD": self.account_product,
                    "INQR_STRT_DT": compact_date,
                    "INQR_END_DT": compact_date,
                    "SLL_BUY_DVSN_CD": "00",
                    "PDNO": normalize_symbol("KR", symbol),
                    "CCLD_DVSN": "00",
                    "INQR_DVSN": "00",
                    "INQR_DVSN_3": "00",
                    "ORD_GNO_BRNO": "",
                    "ODNO": "",
                    "INQR_DVSN_1": "",
                    "CTX_AREA_FK100": "",
                    "CTX_AREA_NK100": "",
                    "EXCG_ID_DVSN_CD": venue,
                },
                timeout_seconds=10,
            )
            body = kis_result(response, "KR order history")
            records = body.get("output1", [])
        elif market == "US":
            tr_id = "TTTS3035R" if self.environment == "live" else "VTTS3035R"
            response = self.transport.request(
                "GET",
                f"{self.base_url}/uapi/overseas-stock/v1/trading/inquire-ccnl",
                headers=self.headers(tr_id),
                query={
                    "CANO": self.account_prefix,
                    "ACNT_PRDT_CD": self.account_product,
                    "PDNO": "" if self.environment == "paper" else "%",
                    "ORD_STRT_DT": compact_date,
                    "ORD_END_DT": compact_date,
                    "SLL_BUY_DVSN": "00",
                    "CCLD_NCCS_DVSN": "00",
                    "OVRS_EXCG_CD": "" if self.environment == "paper" else "NASD",
                    "SORT_SQN": "DS",
                    "ORD_DT": "",
                    "ORD_GNO_BRNO": "",
                    "ODNO": "",
                    "CTX_AREA_NK200": "",
                    "CTX_AREA_FK200": "",
                },
                timeout_seconds=10,
            )
            body = kis_result(response, "US order history")
            records = body.get("output", [])
        else:
            raise BlockedError(f"unsupported KIS history market: {market}")
        if isinstance(records, dict):
            records = [records]
        if not isinstance(records, list) or any(
            not isinstance(item, dict) for item in records
        ):
            raise BlockedError("KIS order history output must be an object array")
        return records

    def get_order(
        self,
        *,
        market: str,
        trading_date: str,
        symbol: str,
        venue: str,
        broker_order_id: str,
    ) -> dict[str, Any]:
        records = self.order_history(
            market=market,
            trading_date=trading_date,
            symbol=symbol,
            venue=venue,
        )
        matches = [
            record
            for record in records
            if self._same_order_id(
                self._case_value(record, "ODNO", "odno"), broker_order_id
            )
        ]
        if len(matches) != 1:
            raise BlockedError(
                f"KIS order {broker_order_id} match count is {len(matches)}"
            )
        record = matches[0]
        ordered = self._case_value(
            record, "ORD_QTY", "ord_qty", "FT_ORD_QTY", "ft_ord_qty"
        )
        filled = self._case_value(
            record,
            "TOT_CCLD_QTY",
            "tot_ccld_qty",
            "FT_CCLD_QTY",
            "ft_ccld_qty",
        )
        remaining = self._case_value(
            record, "RMN_QTY", "rmn_qty", "NCCS_QTY", "nccs_qty"
        )
        ordered_decimal = (
            decimal_value(ordered, "ordered quantity")
            if ordered not in (None, "")
            else None
        )
        filled_decimal = (
            decimal_value(filled, "filled quantity")
            if filled not in (None, "")
            else None
        )
        remaining_decimal = (
            decimal_value(remaining, "remaining quantity")
            if remaining not in (None, "")
            else None
        )
        cancel_value = str(
            self._case_value(record, "CNCL_YN", "cncl_yn", "CANCEL_YN") or ""
        ).upper()
        if (
            ordered_decimal is not None
            and filled_decimal is not None
            and ordered_decimal > 0
            and filled_decimal >= ordered_decimal
        ):
            normalized_status = "FILLED"
        elif (
            filled_decimal is not None
            and filled_decimal > 0
            and (remaining_decimal is None or remaining_decimal > 0)
        ):
            normalized_status = "PARTIALLY_FILLED"
        elif cancel_value == "Y":
            normalized_status = "CANCELLED"
        elif remaining_decimal is not None and remaining_decimal > 0:
            normalized_status = "ACKNOWLEDGED"
        else:
            normalized_status = "UNKNOWN"
        return {
            "broker_order_id": broker_order_id,
            "normalized_status": normalized_status,
            "ordered_quantity": None
            if ordered_decimal is None
            else format(ordered_decimal, "f"),
            "filled_quantity": None
            if filled_decimal is None
            else format(filled_decimal, "f"),
            "remaining_quantity": None
            if remaining_decimal is None
            else format(remaining_decimal, "f"),
            "raw": record,
        }


def self_test() -> None:
    intent = {
        "intent_id": "1" * 32,
        "client_order_id": "qta-" + "1" * 28,
        "market": "US",
        "symbol": "AAPL",
        "side": "BUY",
        "order_type": "LIMIT",
        "time_in_force": "DAY",
        "quantity": "1",
        "limit_price": "200.00",
        "stop_price": "180.00",
        "take_profit_price": "240.00",
    }
    now = datetime.now(timezone.utc)
    fake_clock = [0.0]

    def fake_monotonic() -> float:
        return fake_clock[0]

    def fake_sleep(seconds: float) -> None:
        fake_clock[0] += seconds

    market_data_limiter = MinimumIntervalRateLimiter(
        10,
        monotonic=fake_monotonic,
        sleeper=fake_sleep,
    )
    toss_transport = QueueTransport(
        [
            HttpResponse(
                200,
                {},
                {
                    "result": {
                        "orderId": "toss-order-1",
                        "clientOrderId": intent["client_order_id"],
                    }
                },
            ),
            HttpResponse(
                200,
                {"X-RateLimit-Limit": "10"},
                {
                    "result": [
                        {
                            "symbol": "AAPL",
                            "timestamp": now.isoformat(),
                            "lastPrice": "200.50",
                            "currency": "USD",
                        }
                    ]
                },
            ),
            HttpResponse(
                200,
                {"x-ratelimit-limit": "5"},
                {
                    "result": {
                        "timestamp": now.isoformat(),
                        "asks": [
                            {"price": "202.00"},
                            {"price": "0"},
                            {"price": "201.00"},
                        ],
                        "bids": [
                            {"price": "199.00"},
                            {"price": "0"},
                            {"price": "200.00"},
                        ],
                    }
                },
            ),
        ]
    )
    toss = TossBroker(
        client_id="test-client",
        client_secret="test-secret",
        account_seq=1,
        transport=toss_transport,
        access_token="test-token",
        access_token_expires_at=now + timedelta(minutes=5),
        market_data_limiter=market_data_limiter,
    )
    preview = toss.preview_submit(intent)
    assert preview["body"]["quantity"] == "1"
    assert preview["body"]["price"] == "200.00"
    assert preview["request_hash"] == toss.mutation_request_hash(
        "POST",
        "/api/v1/orders",
        preview["body"],
    )
    account_two = TossBroker(
        client_id="test-client",
        client_secret="test-secret",
        account_seq=2,
        transport=QueueTransport([]),
    )
    assert account_two.preview_submit(intent)["request_hash"] != preview["request_hash"]
    assert (
        toss.mutation_request_hash("PUT", "/api/v1/orders", preview["body"])
        != preview["request_hash"]
    )
    assert (
        toss.mutation_request_hash("POST", "/api/v1/orders/other", preview["body"])
        != preview["request_hash"]
    )
    changed_body = {**preview["body"], "price": "201.00"}
    assert (
        toss.mutation_request_hash("POST", "/api/v1/orders", changed_body)
        != preview["request_hash"]
    )
    ack = toss.submit(intent)
    assert ack["broker_order_id"] == "toss-order-1"
    assert ack["request_hash"] == preview["request_hash"]
    assert ack["submit_started_at"]
    assert ack["ack_received_at"]
    quote = toss.quote("US", "AAPL")
    assert quote["best_ask"] == "201.00"
    assert quote["best_bid"] == "200.00"
    assert fake_clock[0] == 0.1
    assert market_data_limiter.minimum_interval_seconds == 0.2
    assert toss.capabilities()["client_order_id_ttl_seconds"] == 600
    assert toss.capabilities()["client_rate_limits_tps"]["ORDER"] == 3

    try:
        TossBroker(
            client_id="test-client",
            client_secret="test-secret",
            account_seq=1,
            access_token="test-token",
        )
    except BlockedError:
        pass
    else:
        raise AssertionError("Toss external token without expiration must be blocked")

    def toss_with_response(response: HttpResponse) -> TossBroker:
        return TossBroker(
            client_id="test-client",
            client_secret="test-secret",
            account_seq=1,
            transport=QueueTransport([response]),
            access_token="test-token",
            access_token_expires_at=now + timedelta(minutes=5),
        )

    for ambiguous_response in (
        HttpResponse(503, {}, None),
        HttpResponse(200, {}, {"result": {}}),
    ):
        try:
            toss_with_response(ambiguous_response).submit(intent)
        except AmbiguousMutationError:
            pass
        else:
            raise AssertionError("Toss 5xx/missing-order-id mutation must be ambiguous")
    try:
        toss_with_response(
            HttpResponse(
                400,
                {},
                {"error": {"code": "INVALID_ORDER", "message": "rejected"}},
            )
        ).submit(intent)
    except AuthoritativeMutationRejection:
        pass
    else:
        raise AssertionError("explicit Toss rejection must be authoritative")
    try:
        toss_with_response(HttpResponse(200, {}, {"result": {}})).cancel(
            "existing-order"
        )
    except AmbiguousMutationError:
        pass
    else:
        raise AssertionError("Toss cancel missing operation ID must be ambiguous")
    malformed_toss_quote = TossBroker(
        client_id="test-client",
        client_secret="test-secret",
        account_seq=1,
        transport=QueueTransport(
            [
                HttpResponse(
                    200,
                    {},
                    {
                        "result": [
                            {
                                "symbol": "AAPL",
                                "timestamp": now.isoformat(),
                                "currency": "USD",
                            }
                        ]
                    },
                ),
                HttpResponse(
                    200,
                    {},
                    {
                        "result": {
                            "timestamp": now.isoformat(),
                            "asks": [{"price": "201"}],
                            "bids": [{"price": "200"}],
                        }
                    },
                ),
            ]
        ),
        access_token="test-token",
        access_token_expires_at=now + timedelta(minutes=5),
    )
    try:
        malformed_toss_quote.quote("US", "AAPL")
    except BlockedError as exc:
        assert "lastPrice must be" in str(exc)
    else:
        raise AssertionError("missing Toss lastPrice must fail as BlockedError")

    kis_transport = QueueTransport(
        [
            HttpResponse(
                200,
                {},
                {
                    "rt_cd": "0",
                    "msg_cd": "0",
                    "msg1": "OK",
                    "output": {"ODNO": "12345", "ORD_TMD": "093001"},
                },
            )
        ]
    )
    kis = KisBroker(
        app_key="test-key",
        app_secret="test-secret",
        account_prefix="12345678",
        account_product="01",
        environment="paper",
        transport=kis_transport,
        access_token="test-token",
    )
    kis_preview = kis.preview_submit(intent, venue="NASD")
    assert kis_preview["tr_id"] == "VTTT1002U"
    assert kis_preview["body"]["CANO"] == "[REDACTED]"
    assert kis_preview["body"]["ACNT_PRDT_CD"] == "[REDACTED]"
    kis_ack = kis.submit(intent, venue="NASD")
    assert kis_ack["broker_order_id"] == "12345"
    assert kis_ack["submit_started_at"]
    assert kis_ack["ack_received_at"]

    def kis_with_response(response: HttpResponse) -> KisBroker:
        return KisBroker(
            app_key="test-key",
            app_secret="test-secret",
            account_prefix="12345678",
            account_product="01",
            environment="paper",
            transport=QueueTransport([response]),
            access_token="test-token",
        )

    for ambiguous_response in (
        HttpResponse(503, {}, {"rt_cd": "1", "msg_cd": "SERVER_ERROR"}),
        HttpResponse(
            200,
            {},
            {"rt_cd": "0", "msg_cd": "0", "msg1": "OK", "output": {}},
        ),
    ):
        try:
            kis_with_response(ambiguous_response).submit(intent, venue="NASD")
        except AmbiguousMutationError:
            pass
        else:
            raise AssertionError("KIS 5xx/missing-order-id mutation must be ambiguous")
    try:
        kis_with_response(
            HttpResponse(
                200,
                {},
                {
                    "rt_cd": "1",
                    "msg_cd": "ORDER_REJECTED",
                    "msg1": "rejected",
                },
            )
        ).submit(intent, venue="NASD")
    except AuthoritativeMutationRejection:
        pass
    else:
        raise AssertionError("explicit KIS rejection must be authoritative")
    try:
        kis_with_response(
            HttpResponse(
                200,
                {},
                {"rt_cd": "0", "msg_cd": "0", "msg1": "OK", "output": {}},
            )
        ).cancel(
            market="US",
            broker_order_id="12345",
            symbol="AAPL",
            quantity="1",
            price="200",
            venue="NASD",
        )
    except AmbiguousMutationError:
        pass
    else:
        raise AssertionError("KIS cancel missing operation ID must be ambiguous")

    deadline_delegate = QueueTransport(
        [
            HttpResponse(
                200,
                {},
                {
                    "rt_cd": "0",
                    "msg_cd": "0",
                    "msg1": "OK",
                    "output": {"ODNO": "late"},
                },
            )
        ]
    )
    paced = PacedTransport(deadline_delegate, minimum_interval_seconds=10)
    paced.last_started = time.monotonic()
    try:
        paced.request(
            "POST",
            "https://example.invalid/order",
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=1),
        )
    except BlockedError as exc:
        assert "deadline" in str(exc)
    else:
        raise AssertionError("pacing past the submit deadline must be blocked")
    assert deadline_delegate.requests == []

    sell_intent = {**intent, "side": "SELL"}
    assert kis.build_order_request(sell_intent, venue="NASD")["tr_id"] == "VTTT1001U"
    kr_intent = {
        **intent,
        "market": "KR",
        "symbol": "5930",
        "limit_price": "70000",
    }
    kr_request = kis.build_order_request(kr_intent, venue="KRX")
    assert kr_request["tr_id"] == "VTTC0012U"
    assert kr_request["body"]["PDNO"] == "005930"
    assert kis.kr_quote_venue("KRX") == "J"
    try:
        kis.kr_quote_venue("NXT")
    except BlockedError:
        pass
    else:
        raise AssertionError("KIS paper NXT quote must be blocked")

    live_kis = KisBroker(
        app_key="test-key",
        app_secret="test-secret",
        account_prefix="12345678",
        account_product="01",
        environment="live",
        transport=QueueTransport([]),
        access_token="test-token",
    )
    assert live_kis.kr_quote_venue("KRX") == "J"
    assert live_kis.kr_quote_venue("NXT") == "NX"
    assert live_kis.kr_quote_venue("SOR") == "UN"

    quote_transport = QueueTransport(
        [
            HttpResponse(
                200,
                {},
                {
                    "rt_cd": "0",
                    "msg_cd": "0",
                    "msg1": "OK",
                    "output": {
                        "stck_prpr": "70000",
                        "stck_bsop_date": "20260727",
                        "stck_cntg_hour": "093000",
                    },
                },
            ),
            HttpResponse(
                200,
                {},
                {
                    "rt_cd": "0",
                    "msg_cd": "0",
                    "msg1": "OK",
                    "output1": {
                        "askp1": "70100",
                        "bidp1": "69900",
                        "stck_bsop_date": "20260727",
                        "aspr_acpt_hour": "093001",
                    },
                    "output2": {"stck_prpr": "70000"},
                },
            ),
        ]
    )
    quote_kis = KisBroker(
        app_key="test-key",
        app_secret="test-secret",
        account_prefix="12345678",
        account_product="01",
        environment="live",
        transport=quote_transport,
        access_token="test-token",
    )
    kr_quote = quote_kis.quote("KR", "005930", venue="NXT")
    assert kr_quote["trade_timestamp"].endswith("T09:30:00+09:00")
    assert kr_quote["book_timestamp"].endswith("T09:30:01+09:00")
    assert kr_quote["trade_timestamp"] != kr_quote["book_timestamp"]
    assert all(
        request["query"]["FID_COND_MRKT_DIV_CODE"] == "NX"
        for request in quote_transport.requests
    )

    malformed_kr_quote = KisBroker(
        app_key="test-key",
        app_secret="test-secret",
        account_prefix="12345678",
        account_product="01",
        environment="live",
        transport=QueueTransport(
            [
                HttpResponse(
                    200,
                    {},
                    {
                        "rt_cd": "0",
                        "msg_cd": "0",
                        "msg1": "OK",
                        "output": {
                            "stck_prpr": "70000",
                            "stck_bsop_date": "20260727",
                            "stck_cntg_hour": "093000",
                        },
                    },
                ),
                HttpResponse(
                    200,
                    {},
                    {
                        "rt_cd": "0",
                        "msg_cd": "0",
                        "msg1": "OK",
                        "output1": {
                            "bidp1": "69900",
                            "stck_bsop_date": "20260727",
                            "aspr_acpt_hour": "093001",
                        },
                    },
                ),
            ]
        ),
        access_token="test-token",
    )
    try:
        malformed_kr_quote.quote("KR", "005930", venue="KRX")
    except BlockedError as exc:
        assert "askp1 must be" in str(exc)
    else:
        raise AssertionError("missing KIS askp1 must fail as BlockedError")

    undated_quote_kis = KisBroker(
        app_key="test-key",
        app_secret="test-secret",
        account_prefix="12345678",
        account_product="01",
        environment="live",
        transport=QueueTransport(
            [
                HttpResponse(
                    200,
                    {},
                    {
                        "rt_cd": "0",
                        "msg_cd": "0",
                        "msg1": "OK",
                        "output": {
                            "stck_prpr": "70000",
                            "stck_cntg_hour": "093000",
                        },
                    },
                ),
                HttpResponse(
                    200,
                    {},
                    {
                        "rt_cd": "0",
                        "msg_cd": "0",
                        "msg1": "OK",
                        "output1": {
                            "askp1": "70100",
                            "bidp1": "69900",
                            "stck_bsop_date": "20260727",
                            "aspr_acpt_hour": "093001",
                        },
                        "output2": {"stck_prpr": "70000"},
                    },
                ),
            ]
        ),
        access_token="test-token",
    )
    try:
        undated_quote_kis.quote("KR", "005930", venue="KRX")
    except BlockedError as exc:
        assert "each require a valid" in str(exc)
    else:
        raise AssertionError("KIS KR quote must not synthesize a trading date")

    single_timestamp_kis = KisBroker(
        app_key="test-key",
        app_secret="test-secret",
        account_prefix="12345678",
        account_product="01",
        environment="live",
        transport=QueueTransport(
            [
                HttpResponse(
                    200,
                    {},
                    {
                        "rt_cd": "0",
                        "msg_cd": "0",
                        "msg1": "OK",
                        "output": {
                            "stck_prpr": "70000",
                            "stck_bsop_date": "20260727",
                        },
                    },
                ),
                HttpResponse(
                    200,
                    {},
                    {
                        "rt_cd": "0",
                        "msg_cd": "0",
                        "msg1": "OK",
                        "output1": {
                            "askp1": "70100",
                            "bidp1": "69900",
                            "stck_bsop_date": "20260727",
                            "aspr_acpt_hour": "093001",
                        },
                        "output2": {"stck_prpr": "70000"},
                    },
                ),
            ]
        ),
        access_token="test-token",
    )
    try:
        single_timestamp_kis.quote("KR", "005930", venue="KRX")
    except BlockedError as exc:
        assert "independent broker-supplied" in str(exc)
    else:
        raise AssertionError("KIS KR price and book timestamps must be independent")

    us_cancel = kis.build_cancel_request(
        market="US",
        broker_order_id="12345",
        symbol="AAPL",
        quantity="1",
        price="200.00",
        venue="NASD",
    )
    assert us_cancel["tr_id"] == "VTTT1004U"
    assert us_cancel["body"]["PDNO"] == "AAPL"
    assert us_cancel["body"]["OVRS_ORD_UNPR"] == "0"
    assert us_cancel["body"]["ORD_SVR_DVSN_CD"] == "0"

    assert (
        kis_market_timestamp(
            date_value="20260727",
            time_value="093001",
            timezone_name="America/New_York",
        )
        == "2026-07-27T09:30:01-04:00"
    )
    assert (
        kis_market_timestamp(
            date_value="",
            time_value="093001",
            timezone_name="America/New_York",
        )
        is None
    )

    for venue, expected_exchange_code, symbol in (
        ("NASD", "NAS", "AAPL"),
        ("NYSE", "NYS", "IBM"),
    ):
        us_quote_transport = QueueTransport(
            [
                HttpResponse(
                    200,
                    {},
                    {
                        "rt_cd": "0",
                        "msg_cd": "0",
                        "msg1": "OK",
                        "output1": {
                            "last": "200.25",
                            "pask1": "200.30",
                            "pbid1": "200.20",
                            "dymd": "20260727",
                            "dhms": "093001",
                        },
                    },
                )
            ]
        )
        us_quote_kis = KisBroker(
            app_key="test-key",
            app_secret="test-secret",
            account_prefix="12345678",
            account_product="01",
            environment="live",
            transport=us_quote_transport,
            access_token="test-token",
        )
        us_quote = us_quote_kis.quote("US", symbol, venue=venue)
        assert us_quote["last_price"] == "200.25"
        assert us_quote["trade_timestamp"] == us_quote["book_timestamp"]
        assert us_quote["trade_timestamp"].endswith("T09:30:01-04:00")
        assert len(us_quote_transport.requests) == 1
        assert us_quote_transport.requests[0]["query"]["EXCD"] == expected_exchange_code
        assert us_quote_transport.requests[0]["url"].endswith(
            "/uapi/overseas-price/v1/quotations/inquire-asking-price"
        )

    no_last_transport = QueueTransport(
        [
            HttpResponse(
                200,
                {},
                {
                    "rt_cd": "0",
                    "msg_cd": "0",
                    "msg1": "OK",
                    "output1": {
                        "pask1": "200.30",
                        "pbid1": "200.20",
                        "dymd": "20260727",
                        "dhms": "093001",
                    },
                },
            ),
            HttpResponse(
                200,
                {},
                {
                    "rt_cd": "0",
                    "msg_cd": "0",
                    "msg1": "OK",
                    "output": {"last": "199.00"},
                },
            ),
        ]
    )
    no_last_kis = KisBroker(
        app_key="test-key",
        app_secret="test-secret",
        account_prefix="12345678",
        account_product="01",
        environment="live",
        transport=no_last_transport,
        access_token="test-token",
    )
    try:
        no_last_kis.quote("US", "AAPL", venue="NASD")
    except BlockedError as exc:
        assert "same-source last/bid/ask" in str(exc)
    else:
        raise AssertionError("KIS US quote must not borrow last from another endpoint")
    assert len(no_last_transport.requests) == 1
    assert len(no_last_transport.responses) == 1

    try:
        kis.quote("US", "AAPL", venue="NASD")
    except BlockedError as exc:
        assert "paper order book" in str(exc)
    else:
        raise AssertionError("KIS U.S. paper quote must be blocked")

    ambiguous = QueueTransport([AmbiguousMutationError("accepted then timeout")])
    ambiguous_broker = KisBroker(
        app_key="test-key",
        app_secret="test-secret",
        account_prefix="12345678",
        account_product="01",
        environment="paper",
        transport=ambiguous,
        access_token="test-token",
    )
    try:
        ambiguous_broker.submit(intent, venue="NASD")
    except AmbiguousMutationError:
        pass
    else:
        raise AssertionError("ambiguous KIS mutation did not raise")

    print(
        canonical_json(
            {
                "self_test": "PASS",
                "toss_request_hash": preview["request_hash"],
                "kis_request_hash": kis_preview["request_hash"],
            }
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--capabilities", choices=("toss", "kis-paper", "kis-live"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.capabilities == "toss":
        print(canonical_json(TossBroker.capabilities()))
        return 0
    if args.capabilities == "kis-paper":
        print(canonical_json(KisBroker.capabilities("paper")))
        return 0
    if args.capabilities == "kis-live":
        print(canonical_json(KisBroker.capabilities("live")))
        return 0
    print(
        canonical_json(
            {
                "status": "BLOCKED",
                "reason": "use --self-test or --capabilities",
            }
        )
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
