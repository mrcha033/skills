#!/usr/bin/env python3
"""Deterministic daily stock technical analysis for quant-stock-technical."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Optional


METHOD_VERSION = "qta-1.0.0"
MIN_SHARED_SESSIONS = 756
PERCENTILE_LOOKBACK = 756
MIN_REFERENCE_OBSERVATIONS = 252


class BlockedError(ValueError):
    pass


@dataclass(frozen=True)
class RawBar:
    day: date
    open: float
    high: float
    low: float
    close: float
    adjusted_close: float
    volume: float


@dataclass(frozen=True)
class Bar:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float


def _number(row: dict[str, str], field: str, row_number: int) -> float:
    try:
        value = float(row[field])
    except (KeyError, TypeError, ValueError) as exc:
        raise BlockedError(f"row {row_number}: invalid or missing {field}") from exc
    if not math.isfinite(value):
        raise BlockedError(f"row {row_number}: non-finite {field}")
    return value


def read_csv(path: str) -> list[RawBar]:
    required = {"date", "open", "high", "low", "close", "adjusted_close", "volume"}
    rows: list[RawBar] = []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = {str(x).strip().lower() for x in (reader.fieldnames or [])}
        if not required.issubset(fields):
            missing = ", ".join(sorted(required - fields))
            raise BlockedError(f"CSV missing columns: {missing}")
        for row_number, source_row in enumerate(reader, start=2):
            row = {str(k).strip().lower(): v for k, v in source_row.items()}
            try:
                day = date.fromisoformat(str(row["date"]).strip())
            except (KeyError, ValueError) as exc:
                raise BlockedError(f"row {row_number}: date must be YYYY-MM-DD") from exc
            values = {name: _number(row, name, row_number) for name in required - {"date"}}
            if min(values["open"], values["high"], values["low"], values["close"], values["adjusted_close"]) <= 0:
                raise BlockedError(f"row {row_number}: prices must be positive")
            if values["volume"] < 0:
                raise BlockedError(f"row {row_number}: volume must be non-negative")
            if values["high"] < max(values["open"], values["low"], values["close"]):
                raise BlockedError(f"row {row_number}: high is inconsistent")
            if values["low"] > min(values["open"], values["high"], values["close"]):
                raise BlockedError(f"row {row_number}: low is inconsistent")
            rows.append(RawBar(day=day, **values))
    rows.sort(key=lambda item: item.day)
    if len({item.day for item in rows}) != len(rows):
        raise BlockedError("CSV contains duplicate dates")
    if not rows:
        raise BlockedError("CSV contains no observations")
    return rows


def normalize_to_latest_scale(rows: list[RawBar]) -> list[Bar]:
    latest_factor = rows[-1].adjusted_close / rows[-1].close
    if latest_factor <= 0:
        raise BlockedError("invalid latest adjustment factor")
    normalized: list[Bar] = []
    for row in rows:
        factor = (row.adjusted_close / row.close) / latest_factor
        normalized.append(
            Bar(
                day=row.day,
                open=row.open * factor,
                high=row.high * factor,
                low=row.low * factor,
                close=row.close * factor,
                volume=row.volume,
            )
        )
    return normalized


def cutoff(rows: list[RawBar], analysis_date: Optional[date]) -> list[RawBar]:
    selected = [row for row in rows if analysis_date is None or row.day <= analysis_date]
    if not selected:
        raise BlockedError("no observations on or before analysis date")
    if analysis_date is not None and selected[-1].day != analysis_date:
        raise BlockedError("analysis date is not present in both completed-session files")
    return selected


def align(ticker_rows: list[RawBar], benchmark_rows: list[RawBar], analysis_date: Optional[date]) -> tuple[list[Bar], list[Bar]]:
    ticker_cut = cutoff(ticker_rows, analysis_date)
    benchmark_cut = cutoff(benchmark_rows, analysis_date)
    ticker_map = {row.day: row for row in ticker_cut}
    benchmark_map = {row.day: row for row in benchmark_cut}
    days = sorted(set(ticker_map) & set(benchmark_map))
    if not days:
        raise BlockedError("ticker and benchmark have no shared dates")
    target = analysis_date or min(ticker_cut[-1].day, benchmark_cut[-1].day)
    days = [day for day in days if day <= target]
    if not days or days[-1] != target:
        raise BlockedError("latest requested date is not shared by ticker and benchmark")
    ticker_aligned = normalize_to_latest_scale([ticker_map[day] for day in days])
    benchmark_aligned = normalize_to_latest_scale([benchmark_map[day] for day in days])
    if len(days) < MIN_SHARED_SESSIONS:
        raise BlockedError(f"need at least {MIN_SHARED_SESSIONS} shared sessions; found {len(days)}")
    return ticker_aligned, benchmark_aligned


def sma(values: list[float], period: int) -> list[Optional[float]]:
    output: list[Optional[float]] = [None] * len(values)
    running = 0.0
    for index, value in enumerate(values):
        running += value
        if index >= period:
            running -= values[index - period]
        if index >= period - 1:
            output[index] = running / period
    return output


def returns(values: list[float], period: int) -> list[Optional[float]]:
    output: list[Optional[float]] = [None] * len(values)
    for index in range(period, len(values)):
        output[index] = values[index] / values[index - period] - 1.0
    return output


def rsi(values: list[float], period: int = 14) -> list[Optional[float]]:
    output: list[Optional[float]] = [None] * len(values)
    if len(values) <= period:
        return output
    gains = [0.0] * len(values)
    losses = [0.0] * len(values)
    for index in range(1, len(values)):
        delta = values[index] - values[index - 1]
        gains[index] = max(delta, 0.0)
        losses[index] = max(-delta, 0.0)
    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period
    for index in range(period, len(values)):
        if index > period:
            avg_gain = ((period - 1) * avg_gain + gains[index]) / period
            avg_loss = ((period - 1) * avg_loss + losses[index]) / period
        output[index] = 50.0 if avg_gain == avg_loss == 0 else (100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss))
    return output


def atr_adx(bars: list[Bar], period: int = 14) -> tuple[list[Optional[float]], list[Optional[float]], list[Optional[float]], list[Optional[float]]]:
    length = len(bars)
    tr = [0.0] * length
    plus_dm = [0.0] * length
    minus_dm = [0.0] * length
    for index, bar in enumerate(bars):
        if index == 0:
            tr[index] = bar.high - bar.low
            continue
        tr[index] = max(bar.high - bar.low, abs(bar.high - bars[index - 1].close), abs(bar.low - bars[index - 1].close))
        up = bar.high - bars[index - 1].high
        down = bars[index - 1].low - bar.low
        plus_dm[index] = up if up > down and up > 0 else 0.0
        minus_dm[index] = down if down > up and down > 0 else 0.0

    atr_values: list[Optional[float]] = [None] * length
    plus_di: list[Optional[float]] = [None] * length
    minus_di: list[Optional[float]] = [None] * length
    adx: list[Optional[float]] = [None] * length
    if length <= period:
        return atr_values, adx, plus_di, minus_di

    atr_value = sum(tr[1 : period + 1]) / period
    plus_value = sum(plus_dm[1 : period + 1]) / period
    minus_value = sum(minus_dm[1 : period + 1]) / period
    dx: list[Optional[float]] = [None] * length
    for index in range(period, length):
        if index > period:
            atr_value = ((period - 1) * atr_value + tr[index]) / period
            plus_value = ((period - 1) * plus_value + plus_dm[index]) / period
            minus_value = ((period - 1) * minus_value + minus_dm[index]) / period
        atr_values[index] = atr_value
        if atr_value > 0:
            plus_di[index] = 100.0 * plus_value / atr_value
            minus_di[index] = 100.0 * minus_value / atr_value
            denominator = plus_di[index] + minus_di[index]
            dx[index] = 0.0 if denominator == 0 else 100.0 * abs(plus_di[index] - minus_di[index]) / denominator

    first_adx = 2 * period - 1
    initial_dx = [value for value in dx[period : first_adx + 1] if value is not None]
    if len(initial_dx) == period:
        adx_value = sum(initial_dx) / period
        adx[first_adx] = adx_value
        for index in range(first_adx + 1, length):
            if dx[index] is None:
                continue
            adx_value = ((period - 1) * adx_value + dx[index]) / period
            adx[index] = adx_value
    return atr_values, adx, plus_di, minus_di


def rolling_volatility(values: list[float], period: int = 20) -> list[Optional[float]]:
    log_returns: list[Optional[float]] = [None] + [math.log(values[i] / values[i - 1]) for i in range(1, len(values))]
    output: list[Optional[float]] = [None] * len(values)
    for index in range(period, len(values)):
        window = [value for value in log_returns[index - period + 1 : index + 1] if value is not None]
        if len(window) == period:
            output[index] = statistics.stdev(window) * math.sqrt(252.0)
    return output


def rolling_max_drawdown(values: list[float], period: int = 252) -> list[Optional[float]]:
    output: list[Optional[float]] = [None] * len(values)
    for index in range(period - 1, len(values)):
        peak = values[index - period + 1]
        worst = 0.0
        for value in values[index - period + 1 : index + 1]:
            peak = max(peak, value)
            worst = max(worst, 1.0 - value / peak)
        output[index] = worst
    return output


def quantile(values: Iterable[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise BlockedError("cannot calculate quantile from empty data")
    location = (len(ordered) - 1) * probability
    lower = math.floor(location)
    upper = math.ceil(location)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - location) + ordered[upper] * (location - lower)


def rolling_gap95(bars: list[Bar], period: int = 60) -> list[Optional[float]]:
    gaps: list[Optional[float]] = [None] + [abs(bars[i].open / bars[i - 1].close - 1.0) for i in range(1, len(bars))]
    output: list[Optional[float]] = [None] * len(bars)
    for index in range(period, len(bars)):
        window = [value for value in gaps[index - period + 1 : index + 1] if value is not None]
        if len(window) == period:
            output[index] = quantile(window, 0.95)
    return output


def rolling_signed_volume(bars: list[Bar], period: int = 20) -> list[Optional[float]]:
    output: list[Optional[float]] = [None] * len(bars)
    for index in range(period - 1, len(bars)):
        median_volume = statistics.median(bar.volume for bar in bars[index - period + 1 : index + 1])
        if index == 0 or median_volume <= 0 or bars[index].volume <= 0:
            continue
        direction = 1.0 if bars[index].close > bars[index - 1].close else (-1.0 if bars[index].close < bars[index - 1].close else 0.0)
        output[index] = direction * math.log(bars[index].volume / median_volume)
    return output


def ratio_series(left: list[Optional[float]], right: list[Optional[float]]) -> list[Optional[float]]:
    output: list[Optional[float]] = [None] * len(left)
    for index, (a, b) in enumerate(zip(left, right)):
        if a is not None and b not in (None, 0):
            output[index] = a / b - 1.0
    return output


def close_over(values: list[float], average: list[Optional[float]]) -> list[Optional[float]]:
    return [None if avg in (None, 0) else value / avg - 1.0 for value, avg in zip(values, average)]


def relative_returns(ticker: list[float], benchmark: list[float], period: int) -> list[Optional[float]]:
    output: list[Optional[float]] = [None] * len(ticker)
    for index in range(period, len(ticker)):
        output[index] = (ticker[index] / ticker[index - period] - 1.0) - (benchmark[index] / benchmark[index - period] - 1.0)
    return output


def percentile_rank(series: list[Optional[float]], index: int) -> tuple[float, int]:
    current = series[index]
    if current is None or not math.isfinite(current):
        raise BlockedError("current feature value is unavailable")
    start = max(0, index - PERCENTILE_LOOKBACK)
    reference = [value for value in series[start:index] if value is not None and math.isfinite(value)]
    if len(reference) < MIN_REFERENCE_OBSERVATIONS:
        raise BlockedError(f"feature has only {len(reference)} prior observations; need {MIN_REFERENCE_OBSERVATIONS}")
    less = sum(value < current for value in reference)
    equal = sum(math.isclose(value, current, rel_tol=1e-12, abs_tol=1e-15) for value in reference)
    return 100.0 * (less + 0.5 * equal) / len(reference), len(reference)


def weighted_score(percentiles: dict[str, float], weights: dict[str, float]) -> float:
    return sum(percentiles[name] * weight for name, weight in weights.items())


def opinion(score: float) -> str:
    if score >= 80:
        return "강한 긍정"
    if score >= 60:
        return "긍정"
    if score >= 40:
        return "중립"
    if score >= 20:
        return "부정"
    return "강한 부정"


def decimals_for_tick(tick: float) -> int:
    return max(0, -Decimal(str(tick)).normalize().as_tuple().exponent)


def round_up_tick(value: float, tick: float) -> float:
    return math.ceil((value - 1e-12) / tick) * tick


def round_down_tick(value: float, tick: float) -> float:
    return math.floor((value + 1e-12) / tick) * tick


def calculate(ticker_bars: list[Bar], benchmark_bars: list[Bar], market: str, ticker: str, tick_size: float, source_name: str) -> dict[str, Any]:
    if tick_size <= 0 or not math.isfinite(tick_size):
        raise BlockedError("tick size must be a positive finite number")
    closes = [bar.close for bar in ticker_bars]
    benchmark_closes = [bar.close for bar in benchmark_bars]
    index = len(ticker_bars) - 1

    sma5 = sma(closes, 5)
    sma20 = sma(closes, 20)
    sma50 = sma(closes, 50)
    sma200 = sma(closes, 200)
    ret5 = returns(closes, 5)
    ret20 = returns(closes, 20)
    ret63 = returns(closes, 63)
    ret126 = returns(closes, 126)
    ret252 = returns(closes, 252)
    rsi14 = rsi(closes, 14)
    atr14, adx14, plus_di14, minus_di14 = atr_adx(ticker_bars, 14)
    signed_volume20 = rolling_signed_volume(ticker_bars, 20)
    vol20 = rolling_volatility(closes, 20)
    max_drawdown252 = rolling_max_drawdown(closes, 252)
    gap95_60 = rolling_gap95(ticker_bars, 60)

    close_sma20 = close_over(closes, sma20)
    close_sma50 = close_over(closes, sma50)
    close_sma200 = close_over(closes, sma200)
    sma5_sma20 = ratio_series(sma5, sma20)
    sma20_sma50 = ratio_series(sma20, sma50)
    sma50_sma200 = ratio_series(sma50, sma200)
    relative63 = relative_returns(closes, benchmark_closes, 63)
    relative252 = relative_returns(closes, benchmark_closes, 252)
    atr_pct = [None if atr is None else atr / closes[i] for i, atr in enumerate(atr14)]
    adx_direction: list[Optional[float]] = [None] * len(closes)
    for i in range(len(closes)):
        if adx14[i] is not None and plus_di14[i] is not None and minus_di14[i] is not None:
            adx_direction[i] = (plus_di14[i] - minus_di14[i]) * min(adx14[i] / 25.0, 1.0)

    features = {
        "return_5": ret5,
        "return_20": ret20,
        "close_over_sma_20": close_sma20,
        "sma_5_over_sma_20": sma5_sma20,
        "signed_log_volume_ratio_20": signed_volume20,
        "rsi_14": rsi14,
        "return_63": ret63,
        "close_over_sma_50": close_sma50,
        "sma_20_over_sma_50": sma20_sma50,
        "relative_return_63": relative63,
        "adx_direction_14": adx_direction,
        "return_126": ret126,
        "return_252": ret252,
        "close_over_sma_200": close_sma200,
        "sma_50_over_sma_200": sma50_sma200,
        "relative_return_252": relative252,
    }
    risk_features = {
        "ATR14/close": atr_pct,
        "20d annualized volatility": vol20,
        "252d maximum drawdown": max_drawdown252,
        "60d gap p95": gap95_60,
    }
    percentiles: dict[str, float] = {}
    reference_counts: dict[str, int] = {}
    for name, series in {**features, **risk_features}.items():
        percentiles[name], reference_counts[name] = percentile_rank(series, index)

    short_weights = {
        "return_5": 0.25,
        "return_20": 0.20,
        "close_over_sma_20": 0.20,
        "sma_5_over_sma_20": 0.15,
        "signed_log_volume_ratio_20": 0.15,
        "rsi_14": 0.05,
    }
    medium_weights = {
        "return_63": 0.25,
        "close_over_sma_50": 0.20,
        "sma_20_over_sma_50": 0.20,
        "relative_return_63": 0.20,
        "adx_direction_14": 0.15,
    }
    long_weights = {
        "return_126": 0.25,
        "return_252": 0.25,
        "close_over_sma_200": 0.20,
        "sma_50_over_sma_200": 0.15,
        "relative_return_252": 0.15,
    }
    risk_weights = {
        "ATR14/close": 0.30,
        "20d annualized volatility": 0.25,
        "252d maximum drawdown": 0.25,
        "60d gap p95": 0.20,
    }
    short_score = weighted_score(percentiles, short_weights)
    medium_score = weighted_score(percentiles, medium_weights)
    long_score = weighted_score(percentiles, long_weights)
    risk_score = weighted_score(percentiles, risk_weights)
    total_score = min(100.0, max(0.0, 0.25 * short_score + 0.35 * medium_score + 0.40 * long_score - 0.20 * max(risk_score - 50.0, 0.0)))

    prior_high = max(bar.high for bar in ticker_bars[-21:-1])
    current_close = closes[-1]
    current_atr = atr14[-1]
    if current_atr is None:
        raise BlockedError("ATR14 is unavailable")
    entry = round_up_tick(max(current_close, prior_high + tick_size), tick_size)
    stop = round_down_tick(entry - 2.0 * current_atr, tick_size)
    if stop <= 0:
        raise BlockedError("calculated stop is non-positive")
    take_profit = round_down_tick(entry + 2.0 * (entry - stop), tick_size)
    decimals = decimals_for_tick(tick_size)

    dominant_risk = max(risk_features, key=lambda name: percentiles[name])
    raw_risk = risk_features[dominant_risk][-1]
    if raw_risk is None:
        raise BlockedError("dominant risk value is unavailable")
    risk_value = f"{100.0 * raw_risk:.2f}%"
    stop_distance = 100.0 * (entry - stop) / entry
    risk_line = f"{dominant_risk} {risk_value} (과거 백분위 {percentiles[dominant_risk]:.1f}), 진입-손절 거리 {stop_distance:.2f}%"

    result: dict[str, Any] = {
        "source_skill": "quant-stock-technical",
        "result_schema": "quant-stock-technical/v1",
        "calculation_status": "READY",
        "setup_status": "READY" if total_score >= 60.0 else "CONDITIONAL",
        "method_version": METHOD_VERSION,
        "analysis_date": ticker_bars[-1].day.isoformat(),
        "market": market,
        "ticker": ticker,
        "source_name": source_name,
        "shared_sessions": len(ticker_bars),
        "score_basis": "ticker-relative historical percentile; not probability of profit",
        "short": {"opinion": opinion(short_score), "score": round(short_score, 2)},
        "medium": {"opinion": opinion(medium_score), "score": round(medium_score, 2)},
        "long": {"opinion": opinion(long_score), "score": round(long_score, 2)},
        "risk": {"score": round(risk_score, 2), "counterpoint": risk_line},
        "entry_price": round(entry, decimals),
        "stop_price": round(stop, decimals),
        "take_profit_price": round(take_profit, decimals),
        "total_score": round(total_score, 2),
        "reference_observations": {name: reference_counts[name] for name in sorted(reference_counts)},
        "assumptions": [
            "input rows are finalized completed daily sessions",
            "prices are corporate-action adjusted and aligned to analysis-date raw price scale",
            "fees, tax, FX, slippage, position size, and execution are excluded",
        ],
    }
    return result


def render_markdown(result: dict[str, Any]) -> str:
    if result.get("calculation_status") != "READY":
        return f"**계산 상태:** BLOCKED\n\n**사유:** {result.get('reason', 'unknown')}"
    return "\n".join(
        [
            f"## {result['ticker']} quantitative technical analysis",
            "",
            f"- 분석 기준일: {result['analysis_date']}",
            f"- 시장: {result['market']}",
            f"- 티커: {result['ticker']}",
            f"- 계산 상태: {result['calculation_status']}",
            f"- 진입 상태: {result['setup_status']}",
            f"- 방법론: {result['method_version']}",
            "",
            "| 항목 | 의견 | 점수 |",
            "|---|---|---:|",
            f"| 단기 | {result['short']['opinion']} | {result['short']['score']:.2f} |",
            f"| 중기 | {result['medium']['opinion']} | {result['medium']['score']:.2f} |",
            f"| 장기 | {result['long']['opinion']} | {result['long']['score']:.2f} |",
            f"| 종합 | {opinion(result['total_score'])} | {result['total_score']:.2f} |",
            "",
            f"- 리스크/반박: {result['risk']['counterpoint']}",
            f"- 진입가: {result['entry_price']}",
            f"- 손절가: {result['stop_price']}",
            f"- 익절가: {result['take_profit_price']}",
            f"- 점수 기준: {result['score_basis']}",
            f"- 데이터: {result['source_name']} ({result['shared_sessions']} shared sessions)",
        ]
    )


def synthetic_bars(length: int, drift: float, phase: float) -> list[Bar]:
    output: list[Bar] = []
    day = date(2021, 1, 1)
    price = 100.0
    index = 0
    while len(output) < length:
        if day.weekday() < 5:
            cycle = 0.004 * math.sin(index / 17.0 + phase)
            change = drift + cycle
            previous = price
            price = max(1.0, price * (1.0 + change))
            opening = previous * (1.0 + 0.001 * math.sin(index / 9.0 + phase))
            high = max(opening, price) * 1.01
            low = min(opening, price) * 0.99
            volume = 1_000_000.0 * (1.0 + 0.2 * math.sin(index / 13.0 + phase))
            output.append(Bar(day, opening, high, low, price, volume))
            index += 1
        day += timedelta(days=1)
    return output


def self_test() -> None:
    ticker_bars = synthetic_bars(1100, 0.0006, 0.0)
    benchmark_bars = synthetic_bars(1100, 0.0003, 1.0)
    with tempfile.TemporaryDirectory(prefix="qta-self-test-") as directory:
        paths = [Path(directory) / "ticker.csv", Path(directory) / "benchmark.csv"]
        for path, bars in zip(paths, (ticker_bars, benchmark_bars)):
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["date", "open", "high", "low", "close", "adjusted_close", "volume"])
                writer.writeheader()
                for bar in bars:
                    writer.writerow(
                        {
                            "date": bar.day.isoformat(),
                            "open": bar.open,
                            "high": bar.high,
                            "low": bar.low,
                            "close": bar.close,
                            "adjusted_close": bar.close,
                            "volume": bar.volume,
                        }
                    )
        parsed_ticker, parsed_benchmark = align(read_csv(str(paths[0])), read_csv(str(paths[1])), ticker_bars[-1].day)
        result = calculate(parsed_ticker, parsed_benchmark, "TEST", "SYNTH", 0.01, "deterministic-self-test")
    assert result["calculation_status"] == "READY"
    assert result["source_skill"] == "quant-stock-technical"
    assert result["result_schema"] == "quant-stock-technical/v1"
    assert 0 <= result["total_score"] <= 100
    assert result["stop_price"] < result["entry_price"] < result["take_profit_price"]
    print(json.dumps({"self_test": "PASS", "method_version": METHOD_VERSION, "total_score": result["total_score"]}, ensure_ascii=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker-csv")
    parser.add_argument("--benchmark-csv")
    parser.add_argument("--market")
    parser.add_argument("--ticker")
    parser.add_argument("--tick-size", type=float)
    parser.add_argument("--source-name", default="user-supplied-eod-csv")
    parser.add_argument("--analysis-date")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        return 0
    missing = [name for name in ("ticker_csv", "benchmark_csv", "market", "ticker", "tick_size") if getattr(args, name) is None]
    if missing:
        result = {"source_skill": "quant-stock-technical", "result_schema": "quant-stock-technical/v1", "calculation_status": "BLOCKED", "reason": "missing arguments: " + ", ".join(missing), "method_version": METHOD_VERSION}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    try:
        requested_date = date.fromisoformat(args.analysis_date) if args.analysis_date else None
        ticker_rows, benchmark_rows = align(read_csv(args.ticker_csv), read_csv(args.benchmark_csv), requested_date)
        result = calculate(ticker_rows, benchmark_rows, args.market, args.ticker, args.tick_size, args.source_name)
    except (BlockedError, OSError, ValueError) as exc:
        result = {"source_skill": "quant-stock-technical", "result_schema": "quant-stock-technical/v1", "calculation_status": "BLOCKED", "reason": str(exc), "method_version": METHOD_VERSION}
        print(render_markdown(result) if args.format == "markdown" else json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    print(render_markdown(result) if args.format == "markdown" else json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
