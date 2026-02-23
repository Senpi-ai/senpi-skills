# Scoring System — Complete Point Breakdowns

## Pillar 1: Smart Money (25%, 0-100)

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

## Pillar 2: Market Structure (25%, 0-100)

| Signal | Points |
|---|---|
| Volume > $50M/24h | +30 |
| Volume surge > 2x | +30 |
| OI > $10M | +20 |
| Healthy OI/Volume ratio | +20 |

## Pillar 3: Technicals (25%, 0-100)

| Signal | Points |
|---|---|
| 4h trend aligned with direction | 0-20 |
| **1h trend structure aligned** (v5) | **0-20** |
| 1h RSI favorable (< 30 long / > 70 short) | 0-20 |
| 15m RSI convergence | 0-10 |
| Volume confirmation | 0-15 |
| 15m candlestick patterns | 0-15 |
| 1h candlestick patterns | 0-5 |
| 15m momentum alignment | 0-10 |
| 4h price momentum | 0-10 |
| Volume-price divergence | -5 to +10 |
| **Counter-trend on hourly** (v5) | **-30** |
| Counter-trend on 4h | -5 |

## Pillar 4: Funding (25%, 0-100)

| Signal | Points |
|---|---|
| Neutral funding (< 5% ann) | +40 |
| Favorable + extreme (> 50% ann) | +35 |
| Favorable + moderate | +25 |
| Unfavorable + extreme | -20 |

## BTC Macro Modifier

| BTC State | LONG modifier | SHORT modifier |
|---|---|---|
| `strong_down` + 1h falling >1% | -40 | +15 |
| `down` | -20 | +10 |
| `neutral` | 0 | 0 |
| `up` | +10 | -20 |
| `strong_up` + 1h rising >1% | +15 | -40 |
