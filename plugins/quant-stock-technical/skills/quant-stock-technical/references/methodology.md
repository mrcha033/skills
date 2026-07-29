# QTA 1.0.0 methodology

## Scope

`qta-1.0.0` is a long-only, daily, trend-following technical-strength model. It uses adjusted OHLCV and a same-market benchmark. It contains no news, fundamentals, sentiment, analyst estimates, sector narrative, or discretionary overrides.

## Causal data handling

- Use only sessions on or before the analysis date.
- Intersect ticker and benchmark dates.
- Align adjusted historical prices to the ticker's raw close scale on the analysis date.
- Exclude the current feature observation from its historical reference distribution.
- Require at least 756 shared sessions and at least 252 valid prior observations for every scored feature.

## Percentile transform

For each feature, compare the current value with up to 756 preceding valid values:

```text
percentile = 100 * (count(prior < current) + 0.5 * count(prior = current)) / count(prior)
```

This is a ticker-relative historical percentile. It is not a forecast probability and is not a cross-sectional rank against other securities.

## Horizon scores

Short score:

```text
25% return_5
20% return_20
20% close_over_sma_20
15% sma_5_over_sma_20
15% signed_log_volume_ratio_20
 5% rsi_14
```

Medium score:

```text
25% return_63
20% close_over_sma_50
20% sma_20_over_sma_50
20% relative_return_63
15% adx_direction_14
```

Long score:

```text
25% return_126
25% return_252
20% close_over_sma_200
15% sma_50_over_sma_200
15% relative_return_252
```

`signed_log_volume_ratio_20` is the sign of the one-day return multiplied by `ln(volume / median_volume_20)`. `adx_direction_14` is `(+DI - -DI) * min(ADX / 25, 1)`.

## Risk score and total score

Risk score:

```text
30% percentile(ATR_14 / close)
25% percentile(20-session annualized log-return volatility)
25% percentile(252-session maximum drawdown magnitude)
20% percentile(60-session 95th-percentile absolute overnight gap)
```

Total score:

```text
clip(
  0.25 * short
  + 0.35 * medium
  + 0.40 * long
  - 0.20 * max(risk - 50, 0),
  0,
  100
)
```

Opinion labels are mechanical:

| Score | Label |
|---:|---|
| 80-100 | 강한 긍정 |
| 60-79.999 | 긍정 |
| 40-59.999 | 중립 |
| 20-39.999 | 부정 |
| 0-19.999 | 강한 부정 |

## Price levels

Use the market tick size supplied by the caller:

```text
entry = round_up_to_tick(max(current_close, prior_20_session_high + tick))
stop = round_down_to_tick(entry - 2 * ATR_14)
take_profit = round_down_to_tick(entry + 2 * (entry - stop))
```

The setup is `READY` when total score is at least 60 and otherwise `CONDITIONAL`. Data or calculation failures return `BLOCKED`.

## Quantitative counterpoint

Select the risk component with the highest historical percentile. Render only its metric value, historical percentile, and entry-to-stop percentage using a fixed template. Do not add narrative judgment.

## Downstream handoff contract

Machine-readable results include:

```text
source_skill = quant-stock-technical
result_schema = quant-stock-technical/v1
method_version = qta-1.0.0
calculation_status = READY
```

`stock-scenario-story` accepts only the complete unmodified JSON result. A Markdown rendering, hand-entered score summary, incomplete payload, or failed calculation is not a valid handoff.

## QTA 2.0.0 first-hour research model

`qta-2.0.0` reuses the same causal daily features, cutoff rules, price levels,
and per-ticker historical percentile transform. It changes only the horizon
aggregation for the first regular-session hour:

```text
clip(
  0.50 * short
  + 0.30 * medium
  + 0.20 * (100 - risk),
  0,
  100
)
```

Long-horizon strength remains visible in the payload but has zero weight in
the first-hour total. A setup also requires median 20-session turnover of at
least KRW 1,000,000,000 for Korean instruments or USD 1,000,000 for U.S.
instruments.

The frozen payload requires a downstream same-session market-regime check:

```text
metric = same_session_previous_close_return_bps
minimum_change_bps = 0
max_age_seconds = 90
```

The runner admits a new entry only when the exchange benchmark or documented
broad-market proxy is fresh, belongs to the current session, and is not below
the previous close. It never applies this check to exit management.

The model was created after a full-universe diagnostic for the 2026-07-28
opening hour. On that single session relative to QTA1, Spearman rank
correlation increased on KOSDAQ, KOSPI, NASDAQ, and NYSE. This is diagnostic
evidence, not sufficient promotion evidence. The payload therefore emits
`validation_status=RESEARCH_ONLY`, and the execution skill accepts it only in
shadow mode until multiple later sessions are evaluated without retuning the
formula.
