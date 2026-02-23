### v5: Hourly Trend Classification

The 1h candle data is analyzed for swing structure to classify the hourly trend:

```python
def classify_hourly_trend(candles_1h):
    """
    Analyze last 12-24 hourly candles for higher-high/higher-low
    or lower-high/lower-low structure.
    
    Returns: "UP", "DOWN", or "NEUTRAL"
    """
    if len(candles_1h) < 8:
        return "NEUTRAL"
    
    # Find swing highs and lows (lookback=3)
    highs = [float(c["h"]) for c in candles_1h]
    lows = [float(c["l"]) for c in candles_1h]
    
    swing_highs = []
    swing_lows = []
    for i in range(3, len(candles_1h) - 3):
        if highs[i] == max(highs[i-3:i+4]):
            swing_highs.append((i, highs[i]))
        if lows[i] == min(lows[i-3:i+4]):
            swing_lows.append((i, lows[i]))
    
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "NEUTRAL"
    
    # Check last 2-3 swing points
    recent_highs = [h for _, h in swing_highs[-3:]]
    recent_lows = [l for _, l in swing_lows[-3:]]
    
    higher_highs = all(recent_highs[i] > recent_highs[i-1] 
                       for i in range(1, len(recent_highs)))
    higher_lows = all(recent_lows[i] > recent_lows[i-1] 
                      for i in range(1, len(recent_lows)))
    lower_highs = all(recent_highs[i] < recent_highs[i-1] 
                      for i in range(1, len(recent_highs)))
    lower_lows = all(recent_lows[i] < recent_lows[i-1] 
                     for i in range(1, len(recent_lows)))
    
    if higher_highs and higher_lows:
        return "UP"
    elif lower_highs and lower_lows:
        return "DOWN"
    elif higher_highs or higher_lows:
        return "UP"  # Weak uptrend
    elif lower_highs or lower_lows:
        return "DOWN"  # Weak downtrend
    else:
        return "NEUTRAL"
```

**This is the #1 gate for all trade decisions.** The agent should NEVER open a LONG on an asset with `hourlyTrend: "DOWN"` or a SHORT on an asset with `hourlyTrend: "UP"`.

### Stage 4: Cross-Scan Momentum

After scoring, save compact results to `scan-history.json`. Compare with previous scans to compute:

- `scoreDelta` — change from last scan (positive = rising conviction)
- `scanStreak` — consecutive scans this asset appeared in top results

## 4-Pillar Scoring System (25% each, 0-100 per pillar, 0-400 total)

### Pillar 1: Smart Money (25%)

**v5: Increased weight on trader count — separates real signals from noise.**

| Signal | Points |
|---|---|
| pnlContribution > 15% | +50 |
| pnlContribution > 5% | +35 |
| pnlContribution > 1% | +20 |
| **traderCount > 400** (v5) | **+30** |
| traderCount > 300 | +25 |
| traderCount > 100 | +18 |
| traderCount > 30 | +10 |
| Acceleration (4h change) > 10 | +20 |
| Freshness: avgAtPeak > 85% | +15 |
| Freshness: avgAtPeak < 50% | -10 |
| nearPeakPct > 50% | +10 |

**Why v5 adds the 400+ tier:** Conviction 4 with 130 traders caused 3 whipsaw flips in 2 hours (pure noise). Conviction 4 with 400+ traders has consistently been a real signal. The trader count separates micro-noise from macro sentiment shifts.

### Pillar 2: Market Structure (25%)

| Signal | Points |
|---|---|
| Volume > $50M/24h | +30 |
| Volume surge > 2x | +30 |
| OI > $10M | +20 |
| Healthy OI/Volume ratio | +20 |

### Pillar 3: Technicals (25%) — Multi-Timeframe

| Signal | Points |
|---|---|
| 4h trend aligned with direction | 0-20 |
| **1h trend structure aligned** (v5) | **0-20** |
| 1h RSI favorable (< 30 long / > 70 short) | 0-20 |
| 15m RSI convergence (both TFs agree) | 0-10 |
| Volume confirmation (best of 1h/15m surge) | 0-15 |
| 15m candlestick patterns | 0-15 |
| 1h candlestick patterns | 0-5 |
| 15m momentum alignment | 0-10 |
| 4h price momentum | 0-10 |
| Volume-price divergence | -5 to +10 |
| **Counter-trend on hourly penalty** (v5) | **-30** |
| Counter-trend on 4h penalty | -5 |

**v5 change:** Counter-trend on hourly is now -30 points (was -5 for 4h only). This makes it nearly impossible for a counter-trend-on-hourly trade to pass the 175 threshold. Combined with the hard disqualifier flag, this is defense in depth.

### Pillar 4: Funding (25%)

| Signal | Points |
|---|---|
| Neutral funding (< 5% ann) | +40 |
| Favorable + extreme (> 50% ann) | +35 |
| Favorable + moderate | +25 |
| Unfavorable + extreme | -20 |

### BTC Macro Modifier (applied to final score)

| BTC State | LONG modifier | SHORT modifier |
|---|---|---|
| `strong_down` + 1h falling >1% | -40 | +15 |
| `down` | -20 | +10 |
| `neutral` | 0 | 0 |
| `up` | +10 | -20 |
| `strong_up` + 1h rising >1% | +15 | -40 |

All modifiers are configurable via `scanner-config.json`:

```json
{
  "macroModifiers": {
    "strongDownLong": -40,
    "strongDownShort": 15,
    "downLong": -20,
    "downShort": 10,
    "upLong": 10,
    "upShort": -20,
    "strongUpLong": 15,
    "strongUpShort": -40
  },
  "disableMacroFilter": false
}
```

## v5: Hard Disqualifiers (skip regardless of score)

These cause an opportunity to be **skipped entirely**, not just penalized:

| Condition | Rationale |
|---|---|
| **Counter-trend on hourly** (v5) | SM conviction 4 on a 1-min bounce doesn't override a 2-week downtrend. $346 lesson. |
| Extreme RSI: < 20 for SHORTs, > 80 for LONGs | Reversal imminent |
| Counter-trend on 4h with strength > 50 | Strong macro against you |
| Volume dying (ratio < 0.5 on both TFs) | No liquidity for clean entries/exits |
| Funding heavily against you (> 50% ann) | Fee drag kills profits |
| BTC macro headwind > 30 pts | Market-wide risk |

**v5 key change:** Counter-trend on hourly is a HARD SKIP, not a soft warning. This is the single most impactful filter based on real trading results.

## Implementation

**All computation in Python. Zero LLM tokens.**

```
┌──────────────────────────────────────────┐
│  Stage 0: BTC macro context              │
│  curl × 2 → python trend analysis        │
│  Cost: 0 LLM tokens                      │
├──────────────────────────────────────────┤
│  Stage 1: metaAndAssetCtxs               │
│  curl → python filter → ~70 assets       │
│  Cost: 0 LLM tokens                      │
├──────────────────────────────────────────┤
│  Stage 2: leaderboard_get_markets + top  │
│  mcporter → python scoring               │
│  Cost: 0 LLM tokens                      │
├──────────────────────────────────────────┤
│  Stage 3: candle fetch (top 15-16)       │
│  PARALLEL via ThreadPoolExecutor         │
│  + v5: hourly trend classification       │
│  Cost: 0 LLM tokens, ~15-25s runtime     │
├──────────────────────────────────────────┤
│  Stage 4: cross-scan momentum            │
│  python → scan-history.json              │
│  Cost: 0 LLM tokens                      │
├──────────────────────────────────────────┤
│  v5: Hard disqualifier check             │
│  → Remove counter-trend-on-hourly        │
│  → Remove extreme conditions             │
│  Cost: 0 LLM tokens                      │
├──────────────────────────────────────────┤
│  Final: LLM formats report               │
│  Input: ~2-3k tokens (scored JSON)       │
│  Cost: ~5k total tokens                  │
└──────────────────────────────────────────┘
```

## Scripts & Files

| File | Purpose |
|---|---|
| `scripts/opportunity-scan-v5.py` | Python pipeline — fetches data, scores, applies hourly trend gate, outputs JSON |
| `scripts/opportunity-report.sh` | Wrapper — runs pipeline + outputs LLM prompt with data + context |
| `active-positions.json` | Current user positions (scanner reads for conflict flags) |
| `scanner-config.json` | User prefs: risk tolerance, leverage, macro modifiers, parallelism |
| `scan-history.json` | Auto-maintained: last 12 scan results for cross-scan tracking |

## Scanner Config Schema (v5)

```json
{
  "minVolume24h": 500000,
  "topNDeep": 15,
  "maxWorkers": 8,
  "macroModifiers": {
    "strongDownLong": -40,
    "strongDownShort": 15,
    "downLong": -20,
    "downShort": 10,
    "upLong": 10,
    "upShort": -20,
    "strongUpLong": 15,
    "strongUpShort": -40
  },
  "disableMacroFilter": false,
  "volatilityLeverage": {
    "highVolThreshold": 3.0,
    "highVolPenalty": 3,
    "medVolThreshold": 1.5,
    "medVolPenalty": 1
  },
  "scanHistorySize": 12,
  "hourlyTrendGate": true,
  "counterTrendHourlyPenalty": -30
}
```

All fields are optional with sensible defaults. Set `hourlyTrendGate: false` to disable the hard disqualifier (not recommended).

## Output Format

```json
{
  "scanTime": "2026-02-20T14:40:00Z",
  "assetsScanned": 229,
  "passedStage1": 70,
  "passedStage2": 16,
  "deepDived": 16,
  "disqualified": 3,
  "btcContext": {
    "trend": "strong_down",
    "strength": 45,
    "chg1h": -1.8,
    "macroModifier": { "LONG": -40, "SHORT": 15 }
  },
  "opportunities": [
    {
      "asset": "HYPE",
      "direction": "SHORT",
      "leverage": 6,
      "finalScore": 253,
      "scoreDelta": 33,
      "scanStreak": 3,
      "hourlyTrend": "DOWN",
      "trendAligned": true,
      "pillarScores": {
        "smartMoney": 100,
        "marketStructure": 50,
        "technicals": 63,
        "funding": 40
      },
      "smartMoney": {
        "pnlPct": 20.7,
        "traders": 321,
        "accel": 15.0,
        "direction": "SHORT",
        "avgAtPeak": 75.0,
        "nearPeakPct": 11.0
      },
      "technicals": {
        "rsi1h": 38.8,
        "rsi15m": 27.1,
        "volRatio1h": 1.38,
        "volRatio15m": 2.46,
        "trend4h": "strong_down",
        "trendStrength": 45,
        "hourlyTrend": "DOWN",
        "patterns15m": ["three_crows"],
        "patterns1h": [],
        "momentum15m": -0.35,
        "divergence": null,
        "chg1h": -0.5,
        "chg4h": -1.8,
        "chg24h": -2.1,
        "support": 28.5,
        "resistance": 29.3,
        "atrPct": 1.85
      },
      "funding": {
        "rate": 0.0013,
        "annualized": 11.0,
        "favorable": true
      },
      "risks": ["oversold RSI"]
    }
  ],
  "disqualifiedAssets": [
    {
      "asset": "ETH",
      "direction": "LONG",
      "reason": "counter-trend on hourly (hourlyTrend=DOWN)",
      "wouldHaveScored": 195
    }
  ]
}
```

**v5 output additions:**
- `hourlyTrend` per opportunity (UP/DOWN/NEUTRAL)
- `trendAligned` boolean — quick check for the agent
- `disqualified` count and `disqualifiedAssets` array — transparency on what was filtered and why
- `wouldHaveScored` on disqualified assets — shows what the score would have been without the gate, useful for tuning

## Source Code

> **For agents:** Extract the code blocks below into `scripts/opportunity-scan-v5.py` and `scripts/opportunity-report.sh`, then `chmod +x` both.

### Key v5 additions to the Python scanner

The full v4 code base is unchanged except for these additions:

#### 1. Add `classify_hourly_trend()` function

```python
def classify_hourly_trend(candles_1h):
    """
    Analyze last 12-24 hourly candles for higher-high/higher-low
    or lower-high/lower-low structure.
    Returns: "UP", "DOWN", or "NEUTRAL"
    """
    if len(candles_1h) < 8:
        return "NEUTRAL"
    
    highs = [float(c["h"]) for c in candles_1h]
    lows = [float(c["l"]) for c in candles_1h]
    
    swing_highs = []
    swing_lows = []
    for i in range(3, len(candles_1h) - 3):
        if highs[i] == max(highs[i-3:i+4]):
            swing_highs.append((i, highs[i]))
        if lows[i] == min(lows[i-3:i+4]):
            swing_lows.append((i, lows[i]))
    
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "NEUTRAL"
    
    recent_highs = [h for _, h in swing_highs[-3:]]
    recent_lows = [l for _, l in swing_lows[-3:]]
    
    higher_highs = all(recent_highs[i] > recent_highs[i-1] 
                       for i in range(1, len(recent_highs)))
    higher_lows = all(recent_lows[i] > recent_lows[i-1] 
                      for i in range(1, len(recent_lows)))
    lower_highs = all(recent_highs[i] < recent_highs[i-1] 
                      for i in range(1, len(recent_highs)))
    lower_lows = all(recent_lows[i] < recent_lows[i-1] 
                     for i in range(1, len(recent_lows)))
    
    if higher_highs and higher_lows:
        return "UP"
    elif lower_highs and lower_lows:
        return "DOWN"
    elif higher_highs or higher_lows:
        return "UP"
    elif lower_highs or lower_lows:
        return "DOWN"
    else:
        return "NEUTRAL"
```

#### 2. In Stage 3 deep dive, after `multi_tf_analysis()`:

```python
# v5: Classify hourly trend
hourly_trend = classify_hourly_trend(candles_1h)
tf_data["hourlyTrend"] = hourly_trend

# v5: Check trend alignment
trend_aligned = True
if hourly_trend == "DOWN" and direction == "LONG":
    trend_aligned = False
elif hourly_trend == "UP" and direction == "SHORT":
    trend_aligned = False
```

#### 3. In scoring, add hourly trend to technicals scoring:

```python
# v5: Hourly trend alignment scoring
hourly_trend = tf_data.get("hourlyTrend", "NEUTRAL")
if direction == "LONG" and hourly_trend == "UP":
    score += 20
elif direction == "SHORT" and hourly_trend == "DOWN":
    score += 20
elif direction == "LONG" and hourly_trend == "DOWN":
    score -= 30  # v5: heavy penalty
elif direction == "SHORT" and hourly_trend == "UP":
    score -= 30  # v5: heavy penalty
# NEUTRAL gets no bonus or penalty
```

#### 4. After scoring, apply hard disqualifier:

```python
HOURLY_GATE = config.get("hourlyTrendGate", True)

# v5: Hard disqualification check
disqualified = []
qualified_results = []
for r in results:
    hourly = r["technicals"].get("hourlyTrend", "NEUTRAL")
    direction = r["direction"]
    
    disqualify_reason = None
    if HOURLY_GATE:
        if hourly == "DOWN" and direction == "LONG":
            disqualify_reason = f"counter-trend on hourly (hourlyTrend=DOWN)"
        elif hourly == "UP" and direction == "SHORT":
            disqualify_reason = f"counter-trend on hourly (hourlyTrend=UP)"
    
    # Other hard disqualifiers...
    rsi1h = r["technicals"].get("rsi1h", 50)
    if direction == "SHORT" and rsi1h < 20:
        disqualify_reason = f"extreme oversold RSI ({rsi1h})"
    elif direction == "LONG" and rsi1h > 80:
        disqualify_reason = f"extreme overbought RSI ({rsi1h})"
    
    if disqualify_reason:
        disqualified.append({
            "asset": r["asset"],
            "direction": direction,
            "reason": disqualify_reason,
            "wouldHaveScored": r["finalScore"]
        })
    else:
        r["hourlyTrend"] = hourly
        r["trendAligned"] = True
        qualified_results.append(r)

results = qualified_results
```

#### 5. In output, add disqualified info:

```python
output = {
    # ... existing fields ...
    "disqualified": len(disqualified),
    "disqualifiedAssets": disqualified[:5],  # Top 5 for transparency
    "opportunities": results[:15]
}
```
