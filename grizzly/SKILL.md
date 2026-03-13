---
name: grizzly-strategy
description: >-
  GRIZZLY v2.0 — BTC alpha hunter with position lifecycle. Single asset, every signal
  (SM, funding, OI, 4TF trend, volume, ETH correlation). Three-mode lifecycle:
  HUNTING (scan for entry) → RIDING (DSL trails) → STALKING (watch for reload on dip).
  After DSL takes profit, watches for fresh momentum impulse while confirming macro thesis
  is intact. If thesis dies, resets. If dip reloads, re-enters. DSL High Water Mode (mandatory).
license: MIT
metadata:
  author: jason-goldberg
  version: "2.0"
  platform: senpi
  exchange: hyperliquid
---

# GRIZZLY v2.0 — BTC Alpha Hunter with Position Lifecycle

One asset. Every signal. Maximum conviction. Now with reload-on-dip.

GRIZZLY stares at BTC and nothing else. Every signal source available — smart money positioning, funding rate, open interest, 4-timeframe trend structure, volume, ETH correlation — feeds into a single thesis: is there a high-conviction BTC trade right now?

## What's New in v2.0: The Three-Mode Lifecycle

v1.0 treated every entry as independent. After DSL closed a winning trade, GRIZZLY immediately scanned for a new entry — often re-entering on a minor dip that was just the aftershock of the move it just profited from.

v2.0 adds a STALKING mode between exits and new entries. After DSL takes profit, GRIZZLY watches the asset for a genuine reload opportunity while confirming the macro thesis is still alive.

### MODE 1 — HUNTING (default)

Normal behavior. Scan BTC every 3 minutes. All signals must align (4h trend, 1h momentum, SM, funding, OI, volume). Score 10+ to enter. When a position opens, switch to MODE 2.

### MODE 2 — RIDING

Active position. DSL High Water trails it. Thesis re-evaluation every scan. If thesis breaks (4h trend flips, SM flips, funding extreme, volume dies, ETH diverges) → thesis exit and reset to MODE 1 (thesis is dead, don't stalk). If DSL closes the position → switch to MODE 3.

### MODE 3 — STALKING

DSL locked profits. The trend may not be over. Watch for a reload opportunity. Every scan checks:

**Reload conditions (ALL must pass):**
1. At least one completed 1h candle since exit (minimum ~30 min)
2. Fresh 5m momentum impulse in the exit direction (new acceleration, not dead cat bounce)
3. OI stable or growing (not collapsing from profit-taking)
4. Volume at least 50% of what powered the original entry
5. Funding not spiked into crowded territory (< 50% annualized)
6. SM still aligned in the exit direction
7. 4h trend structure still intact

If ALL pass → RELOAD. Re-enter same direction, same leverage. Switch to MODE 2.

**Kill conditions (ANY triggers reset to MODE 1):**
- 4h trend reversed
- SM flipped against exit direction
- OI collapsed 20%+
- Stalking for more than 6 hours with no reload
- Funding spiked above 100% annualized

The loop: HUNTING → RIDING → STALKING → RELOAD → RIDING → STALKING → ... until a kill condition fires → HUNTING.

**maxPositions: 1.** GRIZZLY holds one BTC position at a time. All capital, all attention, one trade.

## MANDATORY: DSL High Water Mode

**GRIZZLY MUST use DSL High Water Mode. This is not optional. Do not substitute standard DSL tiers.**

Spec: https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md

When creating DSL state files for any GRIZZLY position, you MUST include:

```json
{
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 5,
  "tiers": [
    {"triggerPct": 5,  "lockHwPct": 20, "consecutiveBreachesRequired": 3},
    {"triggerPct": 10, "lockHwPct": 40, "consecutiveBreachesRequired": 3},
    {"triggerPct": 15, "lockHwPct": 60, "consecutiveBreachesRequired": 2},
    {"triggerPct": 20, "lockHwPct": 75, "consecutiveBreachesRequired": 1},
    {"triggerPct": 30, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
  ]
}
```

**If `tiers` or `lockMode` is missing from the state file, the DSL engine falls back to flat 1.5% retrace and High Water Mode is silently disabled. Always verify the state file contains these fields after creation.**

**FALLBACK (until DSL engine supports `pct_of_high_water`):** Use the `tiersLegacyFallback` array from `grizzly-config.json` — wide fixed tiers going up to +100% ROE. Switch to High Water tiers the moment the engine supports them.

## Why BTC-Only at High Leverage

Every other skill scans 10-230 assets. GRIZZLY scans one. The tradeoff: fewer trades, but every signal source is concentrated on the asset with the most data.

- **Deepest liquidity on Hyperliquid** — no slippage even at 20x
- **Most SM data** — every whale trades BTC, leaderboard positioning is most meaningful
- **Highest OI concentration** — funding rate signals are strongest
- **Tightest spreads** — maker orders fill reliably
- **Structural moves** — BTC trends last hours to days, not minutes

High leverage amplifies the edge. At 15x, a 2% BTC move = 30% ROE. At 20x, it's 40% ROE. BTC's structural, sustained trends are the ideal setup for leveraged conviction trades with wide trailing stops.

## How GRIZZLY Trades

### Entry (score ≥ 10 required)

Every 3 minutes, the scanner evaluates BTC across all signal sources:

| Signal | Points | Required? |
|---|---|---|
| 4h trend structure (higher lows / lower highs) | 3 | **Yes** — no entry without macro structure |
| 1h trend agrees with 4h | 2 | **Yes** |
| 15m momentum confirms direction | 0-1 | **Yes** (min 0.1%) |
| 5m alignment (all 4 timeframes agree) | 1 | No |
| SM aligned with direction | 2-3 | **Hard block if opposing** |
| Funding pays to hold the direction | 2 | No |
| Volume above average | 1-2 | No |
| OI growing (new money entering) | 1 | No |
| ETH confirms BTC's move | 1 | No |
| RSI has room | 1 | No (but blocks overbought/oversold) |
| 4h momentum strength | 1 | No |

Maximum score: ~18. Minimum to enter: 10. This means at least 4h structure + 1h agreement + SM aligned + one more booster.

### Conviction-Scaled Leverage

| Score | Leverage |
|---|---|
| 10-11 | 15x |
| 12-13 | 18x |
| 14+ | 20x |

### Conviction-Scaled Margin

| Score | Margin |
|---|---|
| 10-11 | 30% of account |
| 12-13 | 37% |
| 14+ | 45% |

### Hold (thesis re-evaluation every 3 min)

GRIZZLY re-evaluates the thesis every scan. The position holds as long as:
- 4h trend structure hasn't flipped
- SM hasn't flipped against the position
- Funding hasn't gone extreme against the position
- Volume hasn't dried up for 3+ hours
- ETH isn't strongly diverging from BTC

If ANY of these break → thesis exit. The agent closes because the reason it entered is dead.

### Exit (DSL High Water — BTC-specific tiers)

BTC pulls back 3-5% ROE regularly during trends. The tiers are designed for this:

| Tier | Trigger ROE | Lock (% of HW) | Breaches | Notes |
|---|---|---|---|---|
| 1 | 5% | 20% | 3 | Very light — BTC pullbacks are normal |
| 2 | 10% | 40% | 3 | Patient — let the trend develop |
| 3 | 15% | 60% | 2 | Tightening |
| 4 | 20% | 75% | 1 | Strong protection |
| 5 | 30%+ | 85% | 1 | Infinite trail — no ceiling |

At 15x leverage: BTC +2% price = +30% ROE → stop at +25.5% ROE (85% of 30).
At 20x leverage: BTC +3% price = +60% ROE → stop at +51% ROE (85% of 60).

Phase 1: conviction-scaled floors. Score 10 = -50% ROE max loss. Score 12+ = unrestricted. No time exits.
Stagnation TP: 12% ROE stale for 90 minutes → close. BTC trends consolidate longer than alts.

## Risk Management

| Rule | Value | Notes |
|---|---|---|
| Max positions | 1 | One BTC trade at a time |
| Phase 1 floor | 3.5% notional | ~52% ROE at 15x, ~70% at 20x |
| G5 per-position cap | 10% of account | Wider than other skills — BTC conviction |
| G2 drawdown halt | 25% from peak | Halt all trading |
| Daily loss limit | 10% | |
| Cooldown | 120 min after 3 consecutive losses | Long — BTC conviction trades shouldn't chain-fail |
| Stagnation TP | 12% ROE stale 90 min | BTC needs patience |

## Cron Architecture

| Cron | Interval | Session | Purpose |
|---|---|---|---|
| Scanner | 3 min | isolated | Thesis builder (if flat) + thesis re-evaluator (if holding) |
| DSL v5.3.1 | 3 min | isolated | High Water Mode trailing stops |

Both MUST be isolated sessions with `agentTurn`. Use `NO_REPLY` for idle cycles.

## Notification Policy

**ONLY alert:** BTC position OPENED (direction, leverage, score, thesis reasons), position CLOSED (DSL or thesis exit with reason), risk guardian triggered, critical error.
**NEVER alert:** Scanner found no thesis, thesis re-evaluation passed, DSL routine check, any reasoning.
All crons isolated. `NO_REPLY` for idle cycles. No rogue processes.

## Bootstrap Gate

On EVERY session, check `config/bootstrap-complete.json`. If missing:
1. Verify Senpi MCP
2. Create scanner cron (3 min, isolated) and DSL cron (3 min, isolated)
3. Write `config/bootstrap-complete.json`
4. Send: "🐻 GRIZZLY is online. Watching BTC. DSL High Water Mode active. Silence = no conviction."

## Expected Behavior

| Metric | Expected |
|---|---|
| Trades/day | 1-3 (BTC conviction trades are rare) |
| Avg hold time | 2-24 hours |
| Win rate | ~50-55% |
| Avg winner | 30-80%+ ROE (high leverage + infinite trailing) |
| Avg loser | -30 to -50% ROE (wide floors for BTC) |
| Fee drag/day | $3-10 (1-3 maker entries) |
| Profit factor | Target 1.5-2.5 (big winners, managed losers) |

## Files

| File | Purpose |
|---|---|
| `scripts/grizzly-scanner.py` | BTC thesis builder + re-evaluator |
| `scripts/grizzly_config.py` | Shared config, MCP helpers, state I/O |
| `config/grizzly-config.json` | All configurable variables with DSL High Water tiers + legacy fallback |
| DSL v5.3.1 (shared skill) | Trailing stop engine — MUST be configured with High Water Mode |

## License

MIT — Built by Senpi (https://senpi.ai). 
Source: https://github.com/Senpi-ai/senpi-skills
