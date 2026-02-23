---
name: wolf-strategy
description: >-
  Aggressive 2-slot autonomous trading strategy for Hyperliquid perps.
  IMMEDIATE_MOVER as primary entry trigger, mechanical DSL exits,
  concentration over diversification. 7-cron architecture with race
  condition prevention. Proven: +$750 across 14 trades, 64% win rate.
  Use when running aggressive autonomous trading with concentrated
  positions, IMMEDIATE_MOVER entries, or 2-slot position management.
license: Apache-2.0
compatibility: >-
  Requires python3, mcporter, and cron. Depends on dsl-dynamic-stop-loss,
  opportunity-scanner, and emerging-movers skills.
metadata:
  author: jason-goldberg
  version: "3.0"
  platform: senpi
  exchange: hyperliquid
---

# WOLF v3 — Aggressive Autonomous Trading

2-slot concentrated position management. Mechanical exits, discretionary entries. DSL v4 handles all exit logic — human/AI judgment on entry signals only.

**Proven:** +$750 realized across 14 trades, 64% win rate in a single session.

## Core Design

- **2 slots max** — concentration beats diversification at small account sizes
- **Mechanical exits** via DSL v4 — no discretion on exits
- **IMMEDIATE_MOVER** is the primary entry trigger (not scanner score)
- **Aggressive rotation** — favor swapping into higher-conviction setups
- **Every slot must maximize ROI** — empty slot > mediocre position

## Cron Architecture

| # | Job | Interval | Script | Purpose |
|---|-----|----------|--------|---------|
| 1 | Emerging Movers | 60s | `emerging-movers.py` | Hunt IMMEDIATE_MOVER signals |
| 2 | Opportunity Scanner | 15min | `opportunity-scan.py` | Deep-dive scoring, threshold 175+ |
| 3 | DSL (per position) | 180s | `dsl-v4.py` | Trailing stops — created/destroyed per position |
| 4 | SM Flip Detector | 5min | (inline) | Conviction collapse detection |
| 5 | Watchdog | 5min | (inline) | Stale position detection |
| 6 | Portfolio Update | 15min | (agent heartbeat) | PnL reporting |
| 7 | Health Check | 10min | (inline) | Orphan DSL detection |

**DSL crons are ephemeral:** created when a position opens, destroyed when it closes.

**Race condition rule:** When ANY job closes a position → immediately deactivate DSL state file + disable DSL cron in the same action.

## Entry Rules

### Primary Signal: IMMEDIATE_MOVER

Emerging Movers fires IMMEDIATE_MOVER when an asset jumps 10+ ranks from #25+ in a single 60-second scan. Act fast — don't wait for scanner confirmation.

**Entry checklist:**
1. IMMEDIATE_MOVER fired with `erratic: false` + `lowVelocity: false`
2. Hourly trend aligned (LONG needs hourlyTrend UP, SHORT needs DOWN)
3. SM conviction ≥ 2 and trader count ≥ 30
4. Max leverage ≥ 10x (check `max-leverage.json`)
5. Not already holding this asset
6. Slot available (max 2 positions)

### Secondary Signal: Opportunity Scanner (Score 175+)

Scanner runs every 15 min. Use for entries when no IMMEDIATE is firing.

### What to Skip

- Erratic rank history (`erratic: true`)
- Low velocity (`lowVelocity: true`)
- < 10 SM traders
- Counter-trend on hourly
- Max leverage < 10x

## Exit Rules

See [references/exit-rules.md](references/exit-rules.md) for complete exit logic.

1. **DSL v4 Mechanical Exit** — primary. Handles all trailing stops automatically.
2. **SM Conviction Collapse** — conviction drops 4→1 (e.g., 220→24 traders) → cut immediately.
3. **Dead Weight Rule** — conviction 0, no SM interest → cut immediately.
4. **Race Condition Prevention** — when closing: deactivate DSL state + disable cron in same action.

## Rotation Rules

**Favor Rotation If:**
- New IMMEDIATE_MOVER firing while current position is flat or slightly negative
- Current position in Phase 1 with no tier hit after 30+ minutes
- New opportunity scores 50+ points higher than current

**Favor Hold If:**
- Current position in DSL Tier 2+
- Current position has strong and rising conviction
- New opportunity hasn't been confirmed by scanner yet

## Position Management & DSL

### Margin Types
- **Cross-margin** for standard Hyperliquid assets
- **Isolated margin** (`leverageType: "ISOLATED"`) for XYZ DEX positions

### DSL Tier Structure

| Tier | Trigger ROE | Lock | Retrace |
|------|-------------|------|---------|
| 1 | 10% | 5% | 1.5% |
| 2 | 20% | 14% | 1.2% |
| 3 | 30% | 22% | 1.0% |
| 4 | 50% | 40% | 0.8% |
| 5 | 75% | 60% | 0.6% |
| 6 | 100% | 80% | 0.5% |

### Stagnation Take-Profit
Auto-close if ROE ≥ 8% and high-water stale for 1 hour.

## Proven Session Results

See [references/session-results.md](references/session-results.md) for the complete Feb 23, 2026 trade log.

**Summary:** 14 trades, 9 wins / 5 losses, 64% win rate, +$750 realized.

## Key Learnings

See [references/learnings.md](references/learnings.md) for bugs, footguns, and trading discipline notes.

## Setup Checklist

1. Install companion skills: `dsl-dynamic-stop-loss`, `opportunity-scanner`, `emerging-movers`
2. Create strategy wallet with budget
3. Set up all 7 cron jobs
4. Create `max-leverage.json` reference file
5. Agent watches Emerging Movers for IMMEDIATE_MOVER signals and acts
