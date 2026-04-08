---
name: bald-eagle-strategy
description: >-
  BALD EAGLE v3.0 — XYZ Alpha Hunter (Hardened). Focused on 6 high-liquidity
  XYZ assets: CL, BRENTOIL, GOLD, SILVER, SP500, XYZ100. Conviction-scaled
  leverage (5-10x based on score). Wider DSL for macro assets. Maker-only
  execution. Scanner calls create_position internally.
  v3.0: focused assets, conviction-scaled leverage, XYZ-tuned DSL, no thesis exit.
license: MIT
metadata:
  author: jason-goldberg
  version: "3.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# BALD EAGLE v3.0 — XYZ Alpha Hunter (Hardened)

The only Senpi agent trading commodities, indices, and equities on Hyperliquid.
Focused on the 6 XYZ assets with deepest SM signal.

## CRITICAL RULES

### RULE 1: Scanner enters. RatchetStop exits. Scanner NEVER exits.
v2.0 had 14.3% win rate partly because positions got chopped by tight timings.
v3.0 uses XYZ-appropriate wide DSL timings and lets macro trades play out.

### RULE 2: Scanner calls create_position internally
The cron command is just: `python3 eagle-scanner.py`. No parsing, no execution in cron.

### RULE 3: FOCUSED ASSETS ONLY
CL, BRENTOIL, GOLD, SILVER, SP500, XYZ100. These have the deepest SM signal
(CL: 181 traders, BRENTOIL: 166). Everything else is sub-50 traders — not enough signal.

### RULE 4: CONVICTION-SCALED LEVERAGE
| Score | Leverage |
|---|---|
| 8-9 | 5x |
| 10-11 | 7x |
| 12+ | 10x |

Higher conviction = higher leverage. When SM is screaming, press it.

### RULE 5: MAKER-ONLY EXECUTION
ensureExecutionAsTaker = false. AMM slippage on XYZ assets destroyed v2.0.
A missed fill is cheaper than -0.5% slippage on entry.

### RULE 6: Spread gate is a HARD gate
The only hard gate in the scanner. If spread > 0.1%, don't trade.
AMM spread = guaranteed slippage = negative edge.

### RULE 7: MAX 2 POSITIONS, MIN_SCORE 9
Higher bar than crypto agents. AMM spread tax means you need stronger conviction.

## v2.0 Post-Mortem
- 54 trades, -$75.25, 14.3% win rate on DSL-managed trades
- CL and BRENTOIL: 100% of volume. All other XYZ assets had too few SM traders.
- 8% retrace at 7x = 1.14% price move. Oil moves 1-2% on noise. Every trade stopped by volatility.
- ensureExecutionAsTaker caused taker fills into AMM — double slippage on entry + exit
- DSL timings (45min hard timeout, 20min weak peak) far too tight for commodities

## What Changed in v3.0

| v2.0 | v3.0 |
|---|---|
| All 54 XYZ assets | 6 focused assets (CL, BRENTOIL, GOLD, SILVER, SP500, XYZ100) |
| 7x leverage fixed | Conviction-scaled: 5x/7x/10x |
| 8% retrace threshold | 12% retrace threshold |
| 45 min hard timeout | 360-600 min (score-dependent) |
| 20 min weak peak | 180-300 min (score-dependent) |
| 12 min dead weight | 90-180 min (score-dependent) |
| minScore 7 | minScore 9 |
| 15% margin | 50% margin |
| Taker fallback | Maker-only |
| Cron parses output | Scanner calls create_position |

## Scoring System (all contributors, no hard gates except spread)

| Signal | Points | Note |
|---|---|---|
| SM concentration ≥20% | +4 | DOMINANT — CL hit 28% recently |
| SM concentration ≥10% | +3 | HIGH |
| SM concentration ≥5% | +2 | SOLID |
| SM concentration ≥3% | +1 | BASE |
| Trader depth ≥100 | +2 | Deep consensus |
| Trader depth ≥30 | +1 | Active |
| Contribution surge 1H (>5%) | +2 | SM piling in |
| Contribution rising 1H (>2%) | +1 | |
| Contribution sustained 4H | +1 | Multi-hour conviction |
| 4H price aligned (>0.5%) | +2 | |
| 4H price positive | +1 | |
| 4H price opposing | -1 | |
| 4H trend structure aligned | +2 | From candles |
| 4H trend opposing | -1 | |
| 1H momentum aligned | +1 | |
| Volume rising | +1 | |
| Funding aligned | +1 | |

Max possible: ~20. Min threshold: 9.

## DSL — XYZ-Tuned Wide Timings

### Phase 2 Tiers (RatchetStop)
| Trigger | Lock % | Breaches |
|---|---|---|
| 5% ROE | 0% | 3 |
| 10% ROE | 20% | 3 |
| 20% ROE | 35% | 2 |
| 30% ROE | 50% | 2 |
| 50% ROE | 70% | 1 |
| 75% ROE | 85% | 1 |

### Phase 1 — Conviction-Scaled Timing (XYZ macro assets need hours)
| Score | Absolute Floor | Hard Timeout | Weak Peak | Dead Weight |
|---|---|---|---|---|
| 8 | -20% ROE | 360 min | 180 min | 90 min |
| 10 | -25% ROE | 480 min | 240 min | 120 min |
| 12 | -30% ROE | 600 min | 300 min | 180 min |

Retrace threshold: 12% (vs 8% in crypto agents).

## Cron Setup

| Cron | Interval | Command |
|---|---|---|
| Scanner | 5 min | `python3 /data/workspace/skills/bald-eagle-strategy/scripts/eagle-scanner.py` |

One cron only. Scanner handles execution. DSL exit managed by plugin runtime.

## Bootstrap Gate

On EVERY session, check `config/bootstrap-complete.json`. If missing:
1. Read senpi-trading-runtime skill
2. Verify Senpi MCP
3. Set wallet in runtime.yaml
4. Set Telegram in runtime.yaml
5. Install runtime: `openclaw senpi runtime create --path /data/workspace/skills/bald-eagle-strategy/runtime.yaml`
6. Verify runtime: `openclaw senpi runtime list`
7. Remove old DSL cron if upgrading
8. Create scanner cron (5 min, main)
9. Write `config/bootstrap-complete.json`
10. Send: "🦅 BALD EAGLE v3.0 online. XYZ Alpha Hunter — focused assets, conviction-scaled leverage, wide DSL. Silence = no alpha."

## Risk
| Rule | Value |
|---|---|
| Max positions | 2 |
| Max entries/day | 4 |
| Min score | 9 |
| Leverage | 5-10x (conviction-scaled) |
| Margin | 50% per trade |
| Spread gate | > 0.1% = rejected |
| Per-asset cooldown | 120 min |
| Daily loss limit | 10% |
| Stagnation TP | 15% ROE stale 4 hours |
| Allowed assets | CL, BRENTOIL, GOLD, SILVER, SP500, XYZ100 |

## Files
| File | Purpose |
|---|---|
| `scripts/eagle-scanner.py` | v3.0 XYZ scanner with conviction-scaled leverage |
| `scripts/eagle_config.py` | Config helper, MCP, state I/O |
| `config/bald-eagle-config.json` | Wallet, strategy ID |
| `runtime.yaml` | Plugin runtime (position tracker + RatchetStop) |

## Changelog

### v3.0 (2026-04-06)
- Focused asset list: CL, BRENTOIL, GOLD, SILVER, SP500, XYZ100
- Conviction-scaled leverage: 5x/7x/10x
- Wider DSL: retrace 12%, 360-600 min timeouts
- Maker-only execution (ensureExecutionAsTaker: false)
- Min score raised to 9
- Margin increased to 50%
- Scanner calls create_position internally
- Contribution velocity scoring added
- All hard gates → score contributors (except spread)
- Thesis exit removed

### v2.0
- Spread gate, XYZ ban on SNDK, leverage capped 7x

### v1.0
- 20x leverage, no spread gate, DSL state bugs → -28.6%
