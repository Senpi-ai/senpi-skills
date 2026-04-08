---
name: viper-strategy
description: >-
  VIPER v2.1 — Range-bound liquidity sniper. Enters at support/resistance when Bollinger Bands
  are tight, ATR is low, RSI is at extremes within the range, and volume is declining.
  Works when nothing is trending — the chop predator. USD-weighted OI filtering (min $5M).
  DSL High Water Mode (mandatory). 5-minute scan interval.
  Second-best performer in the Senpi zoo at +6.2% ROI.
license: MIT
metadata:
  author: jason-goldberg
  version: "2.1"
  platform: senpi
  exchange: hyperliquid
---

# VIPER v2.1 — Range-Bound Liquidity Sniper

The chop predator. When every momentum strategy is sitting idle because nothing is trending, VIPER is working.

**+6.2% ROI** and climbing. Second-best performer on the Senpi Predators tracker.

## What VIPER Does

VIPER scans the top 25 assets by USD-weighted open interest every 5 minutes. It looks for assets stuck in a range — price bouncing between support and resistance — and enters at the boundaries:

- **Long at support** when price is at the bottom of a tight Bollinger Band range, RSI is oversold, and volume is declining (sellers exhausting)
- **Short at resistance** when price is at the top of a tight range, RSI is overbought, and volume is declining (buyers exhausting)

The edge: range-bound markets are predictable. Support holds, resistance holds, and the bounce is mechanical. VIPER catches that bounce.

## MANDATORY: DSL High Water Mode

**VIPER MUST use DSL High Water Mode. This is not optional.**

Spec: https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md

When creating DSL state files for any VIPER position, you MUST include:

```json
{
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 5,
  "tiers": [
    {"triggerPct": 5,  "lockHwPct": 30, "consecutiveBreachesRequired": 3},
    {"triggerPct": 10, "lockHwPct": 50, "consecutiveBreachesRequired": 2},
    {"triggerPct": 15, "lockHwPct": 70, "consecutiveBreachesRequired": 2},
    {"triggerPct": 20, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
  ]
}
```

**If `tiers` or `lockMode` is missing from the state file, the DSL engine falls back to flat 1.5% retrace and High Water Mode is silently disabled. Always verify the state file contains these fields after creation.**

**FALLBACK (until DSL engine supports `pct_of_high_water`):** Use `tiersLegacyFallback` from config.

## Entry Requirements

| Signal | Requirement |
|---|---|
| Bollinger Band width | < 4% (tight range) |
| ATR | < 1.5% (low volatility) |
| RSI | Oversold (< 35) for longs, overbought (> 65) for shorts |
| Volume | Declining (sellers/buyers exhausting) |
| OI (USD-weighted) | ≥ $5M (liquid enough to trade) |
| Min score | 5 |

All filters must pass. If any fails, skip. VIPER is selective — most scans produce zero signals.

## DSL Configuration

### Phase 1 — Structural Invalidation Only

| Setting | Value |
|---|---|
| Hard timeout | Disabled (0) |
| Weak peak cut | Disabled (0) |
| Dead weight | Disabled (0) |
| Floor base | 1.5% notional |

No time exits. If the range breaks, the floor catches it. If the range holds, patience wins.

### Phase 2 — High Water Mode

| Tier | Trigger ROE | Lock (% of HW) | Breaches |
|---|---|---|---|
| 1 | 5% | 30% | 3 |
| 2 | 10% | 50% | 2 |
| 3 | 15% | 70% | 2 |
| 4 | 20%+ | 85% | 1 (infinite trail) |

Phase 2 triggers at +5% ROE (lower than momentum skills — range profits are smaller, start protecting earlier).

### Stagnation TP

5% ROE stale for 30 minutes → close. Range bounces play out quickly. If the profit stalls, take it.

## Margin & Position Sizing

| Setting | Value |
|---|---|
| Margin per trade | 28% of account |
| Max positions | 3 |
| Leverage | Default 10x |

3 slots × 28% = 84% utilization. VIPER deploys capital aggressively because range entries have higher win rates.

## Risk Management

| Rule | Value |
|---|---|
| Max entries/day | 8 |
| Daily loss limit | 8% |
| Max drawdown | 18% |
| Max single loss | 5% of account |
| Max consecutive losses | 4 → 20 min cooldown |

## Cron Architecture

| Cron | Interval | Session | Purpose |
|---|---|---|---|
| Scanner | 5 min | isolated | Range detection + entry signals |
| DSL v5 | 3 min | isolated | High Water Mode trailing stops |

Both MUST be isolated sessions with `agentTurn`. Use `NO_REPLY` for idle cycles.

## Notification Policy

**ONLY alert:** Position OPENED (asset, direction, support/resistance level, score), position CLOSED (DSL or structural exit), risk triggered, critical error.
**NEVER alert:** Scanner found nothing, DSL routine, any reasoning.
All crons isolated. `NO_REPLY` for idle cycles.

## Bootstrap Gate

On EVERY session, check `config/bootstrap-complete.json`. If missing:
1. Verify Senpi MCP
2. Create scanner cron (5 min, isolated) and DSL cron (3 min, isolated)
3. Write `config/bootstrap-complete.json`
4. Send: "🐍 VIPER is online. Scanning for range-bound setups. Silence = nothing is ranging."

## Optional: Trading Strategy Variant

VIPER is proven at +6.2% ROI as-is. One variant is available:

| Strategy | What Changes | When To Consider |
|---|---|---|
| **MAMBA** | VIPER entries + High Water Mode with wider tiers | When you want to capture range breakouts that escape into trends |

Start with vanilla VIPER. MAMBA is the upgrade path for capturing more from breakout trades.

## Expected Behavior

| Metric | Expected |
|---|---|
| Trades/day | 3-8 |
| Win rate | ~60-65% (range entries are predictable) |
| Avg winner | 5-15% ROE |
| Avg loser | -8 to -12% ROE |
| Fee drag/day | $8-20 |
| Profit factor | Target 1.2-1.5 |

## Files

| File | Purpose |
|---|---|
| `scripts/viper-scanner.py` | Range detection + entry signals |
| `scripts/viper_config.py` | Shared config, MCP helpers, state I/O |
| `config/viper-config.json` | All configurable variables with DSL High Water tiers + legacy fallback |
| DSL v5 (shared) | Trailing stop engine — MUST be configured with High Water Mode |

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills
