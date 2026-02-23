## Step 2: Calculate the Playbook

### Risk Rules (scale to budget)

| Parameter | Conservative | Moderate | Aggressive |
|---|---|---|---|
| Max concurrent positions | 2 | 3 | 5 |
| Max per position | 40% of budget | 50% of budget | 50% of budget |
| Max leverage | 5x | 7x | 10x |
| Daily loss limit | -5% of budget | -10% of budget | -15% of budget |
| Total drawdown cap | -15% of budget | -25% of budget | -35% of budget |
| Min scanner score | 200 | 175 | 175 |
| Min risk/reward | 3:1 | 2:1 | 1.5:1 |
| Cooldown after 2 consecutive losses | 30 min | 15 min | 5 min |
| Max net directional exposure | 100% of budget | 150% of budget | 200% of budget |
| BTC macro filter | Enabled (strict) | Enabled (default) | Enabled (relaxed) |

### v6: The #1 Rule — Hourly Trend Alignment

**Every trade and every flip must align with the hourly candle structure.**

Before opening any position or executing any SM flip:

1. Pull 12-24 hourly candles for the asset
2. Identify the trend: higher highs/higher lows = UP, lower highs/lower lows = DOWN, range-bound = NEUTRAL
3. **Only go LONG if hourly trend is UP or NEUTRAL**
4. **Only go SHORT if hourly trend is DOWN or NEUTRAL**
5. **NEVER go counter-trend on the hourly** — no matter what SM signals say

**Why this is #1:** SM signals react to 1-5 minute noise. Your positions need hourly-timeframe moves to profit. SM conviction 4 on a 1-minute bounce doesn't override a 2-week downtrend. This single rule would have prevented $346 in realized losses in one session.

**How to assess hourly trend:**
- Look at the last 12-24 candles
- A sustained move from $36 → $29 over 2 weeks = clear downtrend (SHORT only)
- A grind from $67K → $68.3K then rejection = lower high forming (SHORT bias)
- Choppy range with no clear direction = NEUTRAL (either direction OK, reduce size)

### v6: Max Leverage Check

Before entering any position, consult `max-leverage.json`:

```python
import json
max_lev = json.load(open('max-leverage.json'))
asset_max = max_lev.get(asset, 5)  # default 5x if unknown
actual_leverage = min(desired_leverage, asset_max)
```

Refresh `max-leverage.json` daily or on startup using the Hyperliquid API `meta` endpoint. See `/data/workspace/recipes/max-leverage.md` for the fetch script.

For XYZ assets, always use `leverageType: "ISOLATED"`.

### v6: Concentration Over Diversification

At small account sizes ($500–$5,000), **2-4 high-conviction positions beat 6 mediocre ones.**

- Fewer positions = more margin per position = better buffer
- Cross-margin buffer math: 4 positions had 80.6% buffer. After trimming to 2, buffer jumped to 89.7%
- An empty slot is better than a mediocre position. Don't reflexively re-enter.
- Target: 2-3 positions at Moderate, max 4 at Aggressive

### Directional Exposure Guard

Before opening any position, compute net directional exposure:

```
total_long_notional = sum(margin * leverage for all LONG positions)
total_short_notional = sum(margin * leverage for all SHORT positions)
net_exposure = abs(total_long_notional - total_short_notional)
net_exposure_pct = net_exposure / budget * 100
```

**Rules (configurable per risk profile):**

| Profile | Max Net Exposure | Action when exceeded |
|---|---|---|
| Conservative | 100% of budget | Only opposite-direction trades allowed |
| Moderate | 150% of budget | Same-direction trades reduced 50% in size |
| Aggressive | 200% of budget | Same-direction trades reduced 50% in size |

### Position Sizing by Score

| Score Range | Position Size | Rationale |
|---|---|---|
| 200+ | 100% of max | High conviction |
| 175–199 | 60–80% of max | Solid signal, reduced size |
| < 175 | Skip | Below threshold |

### Position Sizing Progression

1. **First trade**: 30% of max position size (calibration)
2. **Second trade**: 50% of max position size
3. **Third trade onward**: Full position size (if prior trades profitable)
4. **After a loss**: Drop back one step

---

