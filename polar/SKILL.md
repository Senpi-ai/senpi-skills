---
name: polar-strategy
description: >-
  POLAR v1.0 — ETH alpha hunter with position lifecycle. Single asset, every signal
  (SM, funding, OI, 4TF trend, volume, BTC correlation). Three-mode lifecycle:
  HUNTING (scan for entry) / RIDING (DSL trails) / STALKING (watch for reload on dip).
  After DSL takes profit, watches for fresh momentum impulse while confirming macro thesis
  is intact. If thesis dies, resets. If dip reloads, re-enters. DSL High Water Mode (mandatory).
license: MIT
metadata:
  author: jason-goldberg
  version: "1.0"
  platform: senpi
  exchange: hyperliquid
  base_skill: grizzly-v2.0
---

# POLAR v1.0 — ETH Alpha Hunter with Position Lifecycle

One asset. Every signal. Maximum conviction. Reload-on-dip.

POLAR stares at ETH and nothing else. Every signal source available — smart money positioning, funding rate, open interest, 4-timeframe trend structure, volume, BTC correlation — feeds into a single thesis: is there a high-conviction ETH trade right now?

Based on GRIZZLY v2.0's three-mode lifecycle, adapted for ETH's volatility profile.

## The Three-Mode Lifecycle

### MODE 1 — HUNTING (default)

Scan ETH every 3 minutes. All signals must align (4h trend, 1h momentum, SM, funding, OI, volume). Score 10+ to enter. When a position opens, switch to MODE 2.

### MODE 2 — RIDING

Active position. DSL High Water trails it. Thesis re-evaluation every scan. If thesis breaks (4h trend flips, SM flips, funding extreme, volume dies, BTC diverges) -> thesis exit and reset to MODE 1. If DSL closes the position -> switch to MODE 3.

### MODE 3 — STALKING

DSL locked profits. The trend may not be over. Watch for a reload opportunity. Every scan checks:

**Reload conditions (ALL must pass):**
1. At least one completed 1h candle since exit (~30 min minimum)
2. Fresh 5m momentum impulse in the exit direction
3. OI stable or growing
4. Volume at least 50% of original entry
5. Funding not spiked into crowded territory
6. SM still aligned in the exit direction
7. 4h trend structure still intact

If ALL pass -> RELOAD. Re-enter same direction, same leverage. Switch to MODE 2.

**Kill conditions (ANY triggers reset to MODE 1):**
- 4h trend reversed
- SM flipped against exit direction
- OI collapsed 20%+
- Stalking for more than 6 hours with no reload
- Funding spiked above 100% annualized

**maxPositions: 1.** POLAR holds one ETH position at a time.

## MANDATORY: DSL High Water Mode

**POLAR MUST use DSL High Water Mode. This is not optional.**

Spec: https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md

DSL tiers in `polar-config.json`. Arm DSL immediately after every entry fill. Zero naked positions.

## Why ETH-Only at 10-15x Leverage

- **Second deepest liquidity on Hyperliquid** — tight spreads, reliable fills
- **Strong SM positioning data** — most top traders hold ETH alongside BTC
- **High OI concentration** — funding rate signals are meaningful
- **Structural trends** — ETH trends correlate with but don't clone BTC moves
- **BTC as leading indicator** — when BTC confirms, ETH conviction is highest

## How POLAR Trades

### Entry (score >= 10 required)

Every 3 minutes, the scanner evaluates ETH across all signal sources:

| Signal | Points | Required? |
|---|---|---|
| 4h trend structure (higher lows / lower highs) | 3 | **Yes** |
| 1h trend agrees with 4h | 2 | **Yes** |
| 15m momentum confirms direction | 0-1 | **Yes** |
| 5m alignment (all 4 timeframes agree) | 1 | No |
| SM aligned with direction | 2-3 | **Hard block if opposing** |
| Funding pays to hold the direction | 2 | No |
| Volume above average | 1-2 | No |
| OI growing | 1 | No |
| BTC confirms move | 1 | No |
| RSI has room | 1 | No (blocks overbought/oversold) |
| 4h momentum strength | 1 | No |

Maximum score: ~18. Minimum to enter: 10.

### Conviction-Scaled Leverage

| Score | Leverage |
|---|---|
| 10-11 | 12x |
| 12-13 | 14x |
| 14+ | 15x |

### Conviction-Scaled Margin

| Score | Margin |
|---|---|
| 10-11 | 25% of account |
| 12-13 | 31% |
| 14+ | 37% |

## Risk Management

| Rule | Value |
|---|---|
| Max positions | 1 |
| Phase 1 floor | 3% notional (~45% ROE at 12x) |
| Drawdown halt | 25% from peak |
| Daily loss limit | 10% |
| Cooldown | 120 min after 3 consecutive losses |
| Stagnation TP | 12% ROE stale 90 min |

## Cron Architecture

| Cron | Interval | Session | Purpose |
|---|---|---|---|
| Scanner | 3 min | isolated | Thesis builder + re-evaluator + stalk/reload |
| DSL v5 | 3 min | isolated | High Water Mode trailing stops |

Both MUST be isolated sessions with `agentTurn`. Use `NO_REPLY` for idle cycles.

## Notification Policy

**ONLY alert:** Position OPENED (direction, leverage, score, reasons), position CLOSED (DSL or thesis exit), risk guardian triggered, critical error.
**NEVER alert:** Scanner found no thesis, thesis re-eval passed, DSL routine, any reasoning.

## Bootstrap Gate

On EVERY session, check `config/bootstrap-complete.json`. If missing:
1. Verify Senpi MCP
2. Create scanner cron (3 min, isolated) and DSL cron (3 min, isolated)
3. Write `config/bootstrap-complete.json`
4. Send: "🐻‍❄️ POLAR is online. Watching ETH. DSL High Water Mode active. Silence = no conviction."

## Expected Behavior

| Metric | Expected |
|---|---|
| Trades/day | 1-3 |
| Avg hold time | 2-24 hours |
| Win rate | ~50-55% |
| Avg winner | 25-60%+ ROE |
| Avg loser | -25 to -45% ROE |

## Files

| File | Purpose |
|---|---|
| `scripts/polar-scanner.py` | ETH thesis builder + re-evaluator + stalk/reload |
| `scripts/polar_config.py` | Shared config, MCP helpers, state I/O |
| `config/polar-config.json` | All configurable variables with DSL High Water tiers |

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills
