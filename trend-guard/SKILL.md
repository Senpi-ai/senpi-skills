---
name: trend-guard
description: >-
  Lightweight hourly trend classifier for Hyperliquid assets. Fetches 1h candles,
  analyzes swing highs/lows, outputs UP/DOWN/NEUTRAL with strength score.
  One MCP call per asset, < 3 seconds. Use as a pre-entry gate for any trading
  signal — counter-trend entries are the #1 documented loss pattern.
  Can be called standalone or imported by other skills.
license: Apache-2.0
compatibility: "Requires python3 and mcporter."
metadata:
  author: senpi
  version: "1.0"
  platform: senpi
  exchange: hyperliquid
---

# Trend Guard

Classifies the hourly trend for any Hyperliquid asset. Single MCP call, < 3 seconds. Returns UP/DOWN/NEUTRAL with a strength score.

**The core guard:** Counter-trend entries are the #1 documented loss pattern. This skill acts as a pre-entry gate to prevent them.

---

## Architecture

```
[Hourly cron at :02]
trend-guard.py (TREND_BATCH=1)
├── Fetch SM leaderboard top 50 (1 MCP call: leaderboard_get_markets)
├── For each asset: classify_trend(asset)
│   ├── Fetch 24x 1h candles (1 MCP call: market_get_asset_data)
│   ├── Compute EMA-5 and EMA-13 from closes
│   ├── Find swing highs/lows (3-bar lookback)
│   ├── Classify: UP / DOWN / NEUTRAL
│   └── Compute strength (0–100)
└── atomic_write → trend-cache.json

[Every 3 min — Emerging Movers cron]
emerging-movers.py
├── Load trend-cache.json at startup (1 disk read, <1ms)
├── ... detect signals ...
└── check_trend_alignment(asset, direction)
    ├── Cache fresh (≤62 min) → 0ms, use cached
    ├── Cache stale/missing → live classify_trend() call (~2-3s)
    └── MCP down → block entry, never trade blind
```

Importable as a Python module — `from trend_guard import classify_trend(asset, direction)` — so other skills (e.g., emerging-movers, opportunity-scanner) can call it directly without subprocess overhead.

---

## Quick Start

1. Ensure Senpi MCP is connected (`mcporter list` should show `senpi`)
2. Create the hourly batch cron from [references/cron-templates.md](references/cron-templates.md)
3. Verify it ran: check `$OPENCLAW_WORKSPACE/trend-cache.json` exists after `:02`
4. Run standalone (ad-hoc check):
   ```bash
   TREND_ASSET=HYPE python3 scripts/trend-guard.py
   TREND_ASSET=HYPE TREND_DIRECTION=LONG python3 scripts/trend-guard.py
   ```
5. Run batch manually:
   ```bash
   TREND_BATCH=1 python3 scripts/trend-guard.py
   ```

---

## Algorithm

See [references/algorithm.md](references/algorithm.md) for the full algorithm.

### Classification Rules

| Condition | Trend |
|---|---|
| Last 2 swing lows are higher-lows AND EMA-5 > EMA-13 | **UP** |
| Last 2 swing highs are lower-highs AND EMA-5 < EMA-13 | **DOWN** |
| Mixed signals OR EMA separation < 0.1% | **NEUTRAL** |

### Strength Score (0–100)

- EMA separation as % of price (wider = stronger, up to 40 pts)
- Swing structure consistency (all swings aligned = up to 40 pts)
- Volume confirmation (higher vol on trend-direction candles = up to 20 pts)

### Alignment

When `TREND_DIRECTION` env var (or `direction` arg) is set:

| Trade Direction | Trend | Aligned |
|---|---|---|
| LONG | UP | `true` |
| LONG | DOWN | `false` |
| LONG | NEUTRAL | `true` (don't block neutral) |
| SHORT | DOWN | `true` |
| SHORT | UP | `false` |
| SHORT | NEUTRAL | `true` (don't block neutral) |

**Hard rule:** If `aligned: false` AND `strength > 50`, the signal is counter-trend with conviction. Callers should downgrade or skip.

---

## Output

```json
{
  "asset": "HYPE",
  "trend": "DOWN",
  "strength": 72,
  "ema5": 28.45,
  "ema13": 28.92,
  "aligned": false,
  "alignedDirection": "LONG",
  "candles_used": 24,
  "timestamp": "2026-03-05T12:00:00Z"
}
```

- `aligned` is only present when `TREND_DIRECTION` env var (or `direction` arg) is set
- `aligned: false` = counter-trend: LONG on DOWN, or SHORT on UP
- `strength` = 0–100 conviction score

---

## Integration with Other Skills

### Emerging Movers

In `emerging-movers.py`, after detecting an IMMEDIATE signal:

```python
try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'trend-guard', 'scripts'))
    from trend_guard import classify_trend
    TREND_GUARD_AVAILABLE = True
except ImportError:
    TREND_GUARD_AVAILABLE = False

if TREND_GUARD_AVAILABLE and alert.get("isImmediate"):
    trend_result = classify_trend(asset, direction=alert["direction"])
    alert["hourlyTrend"] = trend_result.get("trend")
    alert["trendAligned"] = trend_result.get("aligned", True)
    if not trend_result.get("aligned", True) and trend_result.get("strength", 0) > 50:
        alert["isImmediate"] = False
        alert["downgradedReason"] = "counter-trend on hourly (strength > 50)"
```

Backward compatible — if trend-guard is not installed, behavior is unchanged.

### Opportunity Scanner

The opportunity scanner's hourly trend gate uses the same algorithm. When trend-guard is available, import `classify_trend` instead of the inline implementation to keep them in sync.

---

## API Dependencies

| MCP Tool | Purpose |
|---|---|
| `market_get_asset_data` | Fetch 24 x 1h candles for the asset |

One MCP call per asset.

---

## Cron Setup

One cron: **hourly batch at :02**. See [references/cron-templates.md](references/cron-templates.md) for the full template.

```
Schedule:  2 * * * *   (at :02 past every hour)
Session:   isolated
Model:     Budget tier
Mandate:   TREND_BATCH=1 python3 {SCRIPTS}/trend-guard.py
```

`:02` ensures the 1h candle has closed and finalized. The batch run classifies all SM top-50 assets and writes `trend-cache.json`. Emerging Movers reads from that cache on every 3-min run — zero latency added to entry decisions.

## Cache Format (`trend-cache.json`)

Written to `$OPENCLAW_WORKSPACE/trend-cache.json` after every batch run.

```json
{
  "HYPE": {
    "trend": "UP",
    "strength": 65,
    "ema5": 28.45,
    "ema13": 28.12,
    "computedAt": "2026-03-05T12:02:03Z"
  },
  "BTC": {
    "trend": "DOWN",
    "strength": 78,
    "ema5": 64200.0,
    "ema13": 65100.0,
    "computedAt": "2026-03-05T12:02:03Z"
  }
}
```

Override cache path: set `TREND_CACHE=/custom/path/trend-cache.json` env var (must match in both the batch cron and the Emerging Movers cron).

---

## Error Handling

- If MCP call fails or returns < 8 candles: outputs `trend: "NEUTRAL"`, `strength: 0`, `error` field with reason
- If asset is unknown: same fallback
- Designed to fail safe — on error, callers should treat as neutral (don't block, don't act)

---

## Known Limitations

- 1h timeframe only — does not classify 4h or 15m trends
- Swing detection requires at least 8 candles (6-bar windows) — always met with 24-candle fetch
- Not a reversal detector — classifies existing trend, not inflection points
