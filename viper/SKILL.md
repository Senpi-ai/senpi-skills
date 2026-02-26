---
name: viper-strategy
description: >-
  VIPER v1 — Range-bound trading for Hyperliquid perps via Senpi MCP.
  Detects choppy/sideways markets, maps support/resistance boundaries,
  and trades the bounces with maker limit orders at known levels.
  Exits immediately on range break. 3 hunt phases: Range Detection
  (ADX + BB + boundary touches), Bounce Trading (limit orders at
  support/resistance), and Break Detection (volume + ADX + OI confirmation).
  6-cron architecture. Maker-dominant entries. Pure Python analysis.
  Requires Senpi MCP, python3, mcporter CLI, and OpenClaw cron system.
license: Apache-2.0
compatibility: >-
  Python 3.8+, no external deps (stdlib only). Requires mcporter
  (configured with Senpi auth) and OpenClaw cron system.
metadata:
  author: jason-goldberg
  version: "1.0"
  platform: senpi
  exchange: hyperliquid
---

# VIPER v1 — Range Trading for Choppy Markets

Detect the range. Map the boundaries. Trade the bounces. Exit when the range breaks.

**Philosophy:** WOLF chases trends. TIGER hunts setups. LION trades crashes. VIPER profits from boredom — the 60-70% of the time markets go nowhere.

---

## Architecture

```
┌──────────────────────────────────────────┐
│           6 OpenClaw Crons               │
│  Scanner(15m) Mapper(1h) Bouncer(2m)     │
│  BreakDet(2m) Exit(5m) Health(10m)       │
├──────────────────────────────────────────┤
│           Python Scripts                  │
│  viper_lib.py  viper_config.py           │
│  range-scanner / boundary-mapper /        │
│  bounce-trader / break-detector /         │
│  viper-exit / viper-health               │
├──────────────────────────────────────────┤
│           Senpi MCP (via mcporter)        │
│  market_list_instruments                  │
│  market_get_asset_data                    │
│  create_position / close_position         │
│  edit_position / cancel_order             │
│  strategy_get_clearinghouse_state         │
├──────────────────────────────────────────┤
│           State Files                     │
│  viper-config.json → viper_config.py      │
│  state/{instance}/*.json (atomic writes)  │
└──────────────────────────────────────────┘
```

**State flow:** Scanner detects range candidates → Mapper confirms and maps boundaries → Bouncer posts limit orders at boundaries → Break Detector watches for range breaks → Exit manages open positions. All share state via `state/{instanceKey}/` directory using atomic writes.

---

## Quick Start

1. Ensure Senpi MCP is connected (`mcporter list` shows `senpi`)
2. Create a custom strategy: `strategy_create_custom_strategy`
3. Fund the wallet: `strategy_top_up`
4. Run setup:
   ```bash
   python3 scripts/viper-setup.py --wallet 0x... --strategy-id UUID \
     --budget 5000 --chat-id 12345
   ```
5. Create 6 OpenClaw crons from `references/cron-templates.md`

**Note:** VIPER needs 1-4 hours to identify its first range. This is normal — range detection requires 12+ hours of ADX data and 3+ boundary touches.

---

## 4-Phase Lifecycle

```
Phase 1: SCANNING  → Find range-bound assets
Phase 2: MAPPING   → Confirm range, map support/resistance zones
Phase 3: TRADING   → Bounce trading at boundaries (limit orders)
Phase 4: BREAK     → Range breaks, exit everything, 4h cooldown, return to Phase 1
```

---

## Range Detection Rules

All criteria must be true before VIPER confirms a range:

| # | Criterion | Threshold | Why |
|---|-----------|-----------|-----|
| 1 | ADX(14) on 4h | < 20 for 12+ hours | No directional conviction |
| 2 | BB width percentile on 4h | < 30th percentile (100-bar lookback) | Volatility compressing |
| 3 | Boundary touches | ≥ 3 per side in 48h | Range is confirmed, not just 2 points |
| 4 | Range width | > 2% (support to resistance) | Room for profit after fees |
| 5 | Funding rate | |magnitude| < 0.03% per 8h | No directional crowding |
| 6 | Daily volume | > $5M | Liquidity for limit fills |

**Range scoring:**

```
range_score = (
  (20 - adx) / 20 × 0.25 +
  (1 - bb_pctl / 100) × 0.20 +
  min(support_touches, 5) / 5 × 0.25 +
  min(resistance_touches, 5) / 5 × 0.25 +
  (range_width_pct / 6) × 0.05
)
```

Assets scoring > 0.6 with all criteria → advance to MAPPING.

---

## Boundary Mapping Rules

Support and resistance are **zones**, not single levels:

1. Collect candle wicks (1h) from last 48h that fall within bottom/top 20% of range
2. Cluster with 0.2% tolerance
3. Zone = cluster min to max. Level = cluster median.
4. Support and resistance zones must not overlap

**Dead zone:** middle 40% of range. VIPER never enters in the dead zone.

```
═══ Resistance Zone ═══  (SHORT entry)
    ~~~~ Dead Zone ~~~~  (No trades)
═══ Support Zone ═══     (LONG entry)
```

---

## Bounce Trading Rules

### Entry

| Condition | Action |
|-----------|--------|
| Price enters support zone | Post limit BUY at support level |
| Price enters resistance zone | Post limit SELL at resistance level |
| Pending order unfilled > 15min | Cancel, re-post at updated boundary |
| Price in dead zone | Do nothing |

**All entries are limit orders at boundary levels.** No market orders. No chasing.

### Position Sizing

| Range Width | Range Score 0.6-0.7 | Score 0.7-0.85 | Score > 0.85 |
|-------------|---------------------|-----------------|--------------|
| 2-3% | 10% of balance | 12% | 15% |
| 3-5% | 12% | 15% | 18% |
| > 5% | 15% | 18% | 20% |

All values are percentages. Config key: `positionSizing` (nested by range width bucket).

### Leverage

| Range Width | Leverage |
|-------------|----------|
| 2-3% | 7-8x |
| 3-4% | 6-7x |
| 4-5% | 5-6x |
| > 5% | 5x |

Config key: `leverageByWidth`. Wider range → lower leverage (wider stop).

### Exit

| Rule | Trigger | Action |
|------|---------|--------|
| Take profit | Opposite boundary - 5% buffer | Limit order (maker) |
| Mid-range TP | Price at midpoint + (trade open > 12h OR bounce > 4) | Close 50% |
| Trailing lock | Unrealized > 50% of range width | Lock 60% of gains |
| Trailing tighten | Unrealized > 80% of range width | Lock 75% |
| Time stop | Open > 24h in 2-3% range, or > 36h in any range | Market close |
| Stop loss | Support - 0.5×ATR (longs), Resistance + 0.5×ATR (shorts) | Aggressive limit |

### Bounce Counting

| Bounce # | Adjustment |
|----------|------------|
| 1-4 | Standard sizing |
| 5-6 | Reduce size 25% (range aging) |
| 7-8 | Reduce size 50%, mid-range TP only |
| 9+ | Stop trading this range. Return to scanning. |

Config key: `maxBouncesPerRange` (default: 8).

---

## Range Break Rules

Any ONE of these triggers immediate exit:

| # | Break Signal | Why |
|---|-------------|-----|
| 1 | 1h candle closes outside range + volume > 2× avg | Confirmed breakout |
| 2 | ADX crosses above 25 | Directional conviction returning |
| 3 | OI surges > 8% in break direction within 2h | New money entering trend |
| 4 | Two consecutive 1h closes outside range | Sustained break |
| 5 | Funding spikes to |> 0.05%| per 8h | Directional crowding formed |

**Break response:**
1. Close ALL VIPER positions on the asset (aggressive limit)
2. Cancel all pending limit orders
3. Move asset to COOLDOWN (4 hours — config: `breakCooldownHours`, default: 4)
4. Notify via Telegram (consolidated)

**The handoff:** VIPER's range break = WOLF's breakout entry. Complementary.

---

## Risk Management

| Rule | Limit | Config Key | Default |
|------|-------|-----------|---------|
| Max single trade loss | 5% of balance | `maxSingleLossPct` | 5 |
| Max daily loss | 6% of balance | `maxDailyLossPct` | 6 |
| Max drawdown from peak | 10% | `maxDrawdownPct` | 10 |
| Max concurrent positions | 2 | `maxSlots` | 2 |
| Max ranges tracked | 3 | `maxRanges` | 3 |
| Consecutive stops same range | 3 → stop trading it | `maxConsecutiveStops` | 3 |
| Max daily trades | 6 | `maxDailyTrades` | 6 |

All percentage values are whole numbers (5 = 5%, not 0.05). See `references/state-schema.md`.

**Why tighter than WOLF/TIGER:** Range breaks can cluster (market regime shift). Tighter guards prevent correlated losses when multiple ranges break simultaneously.

---

## Anti-Patterns

1. **NEVER enter in the dead zone.** Middle 40% has no edge.
2. **NEVER fight a range break.** Close everything, cooldown, move on.
3. **NEVER reduce detection criteria to find more ranges.** 3-touch minimum exists to prevent false ranges.
4. **NEVER increase size after a loss.** A stop-out means the range is weakening.
5. **NEVER trade bounce 9+.** Range is exhausted. Win rate drops after bounce 6.
6. **NEVER hold through a break warning.** Tighten to breakeven, don't add.
7. **NEVER enter book trades during a cascade.** Break detector takes precedence over bounce trader.

---

## API Dependencies

| Tool | Used By | Purpose |
|------|---------|---------|
| `market_list_instruments` | range-scanner | Asset discovery, OI, funding, volume |
| `market_get_asset_data` | range-scanner, boundary-mapper, break-detector | Candles for ADX, BB, boundary mapping; L2 book via `include_order_book` |
| `create_position` | bounce-trader | Limit entries at boundaries |
| `close_position` | break-detector, viper-exit | Position exits |
| `edit_position` | viper-exit | Partial closes (mid-range TP) |
| `strategy_get_clearinghouse_state` | viper-health | Margin, positions |

---

## State Schema

See `references/state-schema.md` for full schema with field descriptions.

Key state files:
```
state/{instanceKey}/
├── viper-state.json          # Ranges, positions, watchlist, safety
├── range-{ASSET}.json        # Per-range boundary data + bounce history
└── trade-log.json            # All trades with outcomes
```

All state files include `version`, `active`, `instanceKey`, `createdAt`, `updatedAt` fields. All writes use `atomic_write()` (write to `.tmp`, then `os.replace()`).

---

## Cron Setup

See `references/cron-templates.md` for ready-to-use OpenClaw cron payloads.

| # | Job | Interval | Script | Model Tier |
|---|-----|----------|--------|------------|
| 1 | Range Scanner | 15 min | `range-scanner.py` | Tier 1 |
| 2 | Boundary Mapper | 1 hour | `boundary-mapper.py` | Tier 1 |
| 3 | Bounce Trader | 2 min | `bounce-trader.py` | Tier 2 |
| 4 | Break Detector | 2 min | `break-detector.py` | Tier 1 |
| 5 | Risk & Exit | 5 min | `viper-exit.py` | Tier 2 |
| 6 | Health & Report | 10 min | `viper-health.py` | Tier 1 |

**Tier 1** (fast/cheap): binary checks, threshold comparisons, data collection. Most crons.
**Tier 2** (capable): judgment calls — bounce trader needs to evaluate whether to enter, exit manager evaluates trailing logic.

---

## Expected Performance

| Metric | Target |
|--------|--------|
| Ranges active per day | 1-3 |
| Bounces per range | 4-8 |
| Trades per day | 2-6 |
| Win rate | 65-75% |
| Avg winner ROE | 15-25% (full bounce), 8-12% (mid TP) |
| Avg loser ROE | 5-8% |
| Profit factor | 2.5-3.5 |
| Maker fill rate | 85-95% |
| Best conditions | Choppy, ranging markets |
| Worst conditions | Strong trends, macro breakouts |

---

## Known Limitations

- **Range detection delay.** Needs 12+ hours of ADX < 20 and 3+ touches per boundary. Will miss short-lived ranges.
- **No historical ADX from API.** Computed from candles. Needs ~30+ bars on 4h (5+ days) for reliable ADX(14).
- **Boundary drift.** Support/resistance can shift. Mapper re-validates hourly, but intra-hour shifts possible.
- **Correlated range breaks.** Macro selloff breaks multiple ranges simultaneously. 2-slot limit and 6% daily guard mitigate.
- **Single strategy wallet.** Alternates direction (long at support → close → short at resistance), never simultaneous.
- **L2 orderbook format assumption.** Boundary mapper uses `market_get_asset_data` with `include_order_book=True`. The exact response format for bid/ask levels may need adjustment after testing with live API responses.
- **Not backtested in v1.** Parameters from TA best practices. Tune via config.

---

## Gotchas

- `maxSingleLossPct` is a whole number: `5` = 5%, not `0.05`.
- `breakCooldownHours` is hours, not minutes.
- `leverageByWidth` keys are strings: `"2-3"`, `"3-4"`, `"4-5"`, `">5"`.
- Boundary zones can overlap if range is very tight — mapper rejects these.
- Dead zone is always middle 40% of range. Not configurable in v1.

---

## Optimization Levers

| Lever | Config Key | Conservative | Default | Aggressive |
|-------|-----------|-------------|---------|------------|
| ADX threshold | `adxThreshold` | 18 | 20 | 22 |
| Min touches per side | `minTouchesPerSide` | 4 | 3 | 2 |
| Min range width % | `minRangeWidthPct` | 3 | 2 | 1.5 |
| Max bounces | `maxBouncesPerRange` | 6 | 8 | 10 |
| Max slots | `maxSlots` | 1 | 2 | 3 |
| Daily loss halt % | `maxDailyLossPct` | 4 | 6 | 8 |
| TP target | `tpMode` | midrange | opposite_boundary | full_boundary |

Start conservative. Validate range detection on first 5 confirmed ranges before loosening.
