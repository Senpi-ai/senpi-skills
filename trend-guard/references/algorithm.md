# Trend Guard — Algorithm Reference

## Overview

Trend Guard classifies the hourly trend for a Hyperliquid asset using two complementary signals:
1. **EMA crossover** (EMA-5 vs EMA-13 on closes) — fast momentum indicator
2. **Swing structure** (higher-highs/higher-lows or lower-highs/lower-lows) — price structure

Both signals must agree for a strong classification. Divergence → NEUTRAL.

---

## Input

24 x 1h candles from `market_get_asset_data` with `candle_intervals=["1h"]`.

Each candle:
```
{ "t": timestamp_ms, "o": open, "h": high, "l": low, "c": close, "v": volume }
```

Minimum requirement: 8 candles. With fewer, returns NEUTRAL immediately.

---

## Step 1: EMA Calculation

Standard exponential moving average:

```
k = 2 / (period + 1)
EMA[0] = closes[0]
EMA[i] = closes[i] * k + EMA[i-1] * (1 - k)
```

- **EMA-5**: period=5, multiplier k=0.333
- **EMA-13**: period=13, multiplier k=0.143

The last value of each series is used for classification.

**EMA separation** = `|EMA5 - EMA13| / mid_price * 100`

- < 0.1% separation → considered flat (NEUTRAL regardless of swing structure)

---

## Step 2: Swing Detection

Using a ±3 bar lookback window:

**Swing High** at index `i` when:
```
highs[i] == max(highs[i-3 : i+4])
```

**Swing Low** at index `i` when:
```
lows[i] == min(lows[i-3 : i+4])
```

Swings are detected in the range `[3, len-3]` to ensure full windows.

With 24 candles and 3-bar lookback, typically yields 3–6 swing points of each type.

---

## Step 3: Classification

Check the last 2–3 swing points:

```
higher_lows  = all(recent_lows[i] > recent_lows[i-1])
lower_highs  = all(recent_highs[i] < recent_highs[i-1])
```

Classification logic:

| Condition | Trend |
|---|---|
| EMA separation < 0.1% | **NEUTRAL** (flat EMAs override everything) |
| `higher_lows` AND `EMA5 > EMA13` | **UP** |
| `lower_highs` AND `EMA5 < EMA13` | **DOWN** |
| `EMA5 > EMA13` AND NOT `lower_highs` | **UP** (EMA-only, weak) |
| `EMA5 < EMA13` AND NOT `higher_lows` | **DOWN** (EMA-only, weak) |
| Mixed / neither | **NEUTRAL** |

The EMA is the tiebreaker — swing structure alone is not sufficient when EMAs disagree.

---

## Step 4: Strength Score (0–100)

Three components:

### Component 1: EMA Separation (0–40 pts)

```
ema_pts = min(ema_sep_pct / 2.0, 1.0) * 40
```

- 0% separation → 0 pts
- 1% separation → 20 pts
- 2%+ separation → 40 pts (cap)

### Component 2: Swing Structure Consistency (0–40 pts)

- 20 pts if the primary swing type is confirmed (higher-lows for UP, lower-highs for DOWN)
- +20 pts if the secondary swing type also confirms (higher-highs for UP, lower-lows for DOWN)

A fully confirmed UP trend (higher-highs AND higher-lows with EMA confirmation) scores 40 pts here.

### Component 3: Volume Confirmation (0–20 pts)

```
avg_up_vol   = average volume on up-candles  (close >= open)
avg_down_vol = average volume on down-candles (close < open)
total_avg    = (avg_up + avg_down) / 2
```

For UP trend:
```
vol_pts = min(avg_up_vol / total_avg - 1, 1.0) * 20
```

For DOWN trend:
```
vol_pts = min(avg_down_vol / total_avg - 1, 1.0) * 20
```

This gives up to 20 pts when trending-direction volume is double the mean.

### Strength Interpretation

| Range | Meaning |
|---|---|
| 0–25 | Weak signal, marginal trend |
| 26–50 | Moderate trend, some confirmation |
| 51–75 | Strong trend, well-confirmed |
| 76–100 | Very strong, high conviction |

**Gate threshold:** Callers should only hard-block (downgrade to log-only) when `aligned: false` AND `strength > 50`. Below 50, a counter-trend signal may be an emerging reversal.

---

## Alignment Rules

When `direction` is provided:

| direction | trend | aligned |
|---|---|---|
| LONG | UP | `true` |
| LONG | NEUTRAL | `true` (don't block uncertainty) |
| LONG | DOWN | `false` |
| SHORT | DOWN | `true` |
| SHORT | NEUTRAL | `true` |
| SHORT | UP | `false` |

---

## Design Decisions

### Why EMA-5 and EMA-13?

- EMA-5 is highly responsive to recent price action (≈ last 2–3 candles on hourly)
- EMA-13 spans a half-day on hourly data — captures the intraday trend
- The 5/13 pair is fast enough to detect emerging trends but slow enough to avoid noise
- Consistent with the opportunity-scanner v5 implementation (kept in sync)

### Why 3-bar swing lookback?

- 3 bars is the minimum for meaningful local extrema on 1h data
- Avoids false swings from single-candle wicks
- Standard in technical analysis for swing detection

### Why require both EMA and swing confirmation for strong signals?

- EMA crossovers alone have high false positive rates in ranging markets
- Swing structure alone can lag in fast-moving trends
- Combined confirmation reduces false signals significantly

### Why 24 candles?

- 24 x 1h = 24 hours of data
- Enough for 3–6 swing points with 3-bar lookback
- Single API call (no multi-call batching needed)
- Performance target: < 3 seconds total

---

## Edge Cases

| Case | Behavior |
|---|---|
| < 8 candles returned | Returns NEUTRAL, strength=0, error field set |
| MCP call fails | Returns NEUTRAL, strength=0, error field set |
| All candles same close price | EMA flat → NEUTRAL |
| No swing highs detected | Falls back to EMA-only classification |
| No swing lows detected | Falls back to EMA-only classification |
| Zero volume candles | Volume component = 0 pts (no crash) |

---

## Comparison with Opportunity Scanner v5

The opportunity scanner's `classify_hourly_trend()` in `references/hourly-trend.md` uses a similar swing structure approach. Differences:

| Feature | Opportunity Scanner v5 | Trend Guard |
|---|---|---|
| EMA confirmation | No | Yes (EMA-5/13) |
| Volume confirmation | No | Yes (up to 20 pts) |
| Strength score | No | Yes (0–100) |
| Weak uptrend detection | Single swing type counts | EMA required |
| Importable | No | Yes |
| Standalone | No | Yes |

Trend Guard is intentionally more conservative — it requires EMA confirmation to avoid false signals from single-sided swing detection.
