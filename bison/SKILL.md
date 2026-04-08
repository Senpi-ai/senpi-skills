---
name: bison-strategy
description: >-
  BISON v2.0 — Conviction Holder (Hardened). Top 10 assets by volume. All signals
  are score contributors — no hard gates. Scanner enters via create_position internally
  (Wolverine pattern). RatchetStop exits. Thesis exit REMOVED.
  v2.0: every hard gate converted to score contributor, ensureExecutionAsTaker=false,
  conviction-scaled margin 25-37%.
license: Apache-2.0
metadata:
  author: jason-goldberg
  version: "2.0"
  platform: senpi
  exchange: hyperliquid
---

# BISON v2.0 — Conviction Holder (Hardened)

Top 10 assets by volume. Multi-signal conviction scoring. RatchetStop exits.

## CRITICAL RULES

### RULE 1: Scanner enters. RatchetStop exits. Scanner NEVER exits positions.
v1.x had thesis re-evaluation that chopped winners. Wolverine v1.1 lost -22.7% because
the scanner killed 25 of 27 trades. The one trade it let run hit +29.92% ROE. REMOVED in v2.0.

### RULE 2: Scanner calls create_position internally
The scanner calculates margin, selects leverage, and calls create_position via mcporter.
The cron command is just: `python3 bison-scanner.py`. No parsing, no execution logic in cron.

### RULE 3: All signals are score contributors, not hard gates
4H trend, 1H trend, 1H momentum, SM direction — all add/subtract points.
The minScore threshold (8) is the ONLY gate. Nothing kills a signal before scoring.

### RULE 4: MANDATORY — DSL High Water Mode
Use the tiers and lockMode defined in the scanner's DSL state output.
Never substitute with standard DSL tiers. Never merge with dsl-profile.json.

### RULE 5: ensureExecutionAsTaker = false
All entries use FEE_OPTIMIZED_LIMIT with ensureExecutionAsTaker: false.
entryEnsureTaker: true destroyed $500+ in fees across the fleet.

## How BISON v2.0 Trades

### Entry (all score contributors)
| Signal | Points | Type |
|---|---|---|
| 4H trend aligned | +3 | Score |
| 4H trend opposing | -1 | Score |
| 1H trend agrees | +2 | Score |
| 1H trend opposing | -1 | Score |
| 1H strong momentum (≥1%) | +2 | Score |
| 1H moderate momentum (≥0.5%) | +1 | Score |
| 1H counter momentum | -1 | Score |
| SM aligned | +2 | Score |
| SM opposing | -2 | Score |
| Funding aligned | +2 | Score |
| Funding crowded | -1 | Score |
| Volume rising | +1 | Score |
| OI growing | +1 | Score |
| RSI room | +1 | Score |
| RSI extreme | -1 | Score |
| 4H momentum (>1.5%) | +1 | Score |

Min score: 8. Max possible: ~16.

### Direction Determination
1. 4H trend structure → LONG if BULLISH, SHORT if BEARISH
2. If 4H NEUTRAL → follow SM direction
3. If SM NEUTRAL → follow 1H momentum (>0.5%)
4. If all neutral → no signal

### Conviction-Scaled Margin
| Score | Margin |
|---|---|
| 8-9 | 25% of account |
| 10-11 | 31% |
| 12+ | 37% |

### Exit — RatchetStop Only
DSL High Water Mode. Wide early, tight late:
- +10% ROE: no lock (confirms working)
- +50% ROE: lock 60% of high water
- +100% ROE: lock 85% — infinite trail

### Conviction-Scaled Phase 1 Timing
| Score | Absolute Floor | Hard Timeout | Weak Peak | Dead Weight |
|---|---|---|---|---|
| 6-7 | -25% ROE | 60 min | 30 min | 30 min |
| 8-9 | -30% ROE | 90 min | 45 min | 45 min |
| 10+ | -35% ROE | 120 min | 60 min | 60 min |

## Cron Setup

| Cron | Interval | Command |
|---|---|---|
| Scanner | 5 min | `python3 /data/workspace/skills/bison-strategy/scripts/bison-scanner.py` |

One cron only. Scanner handles execution. DSL exit managed by plugin runtime.

## Hardcoded Constants (not configurable)
- MAX_LEVERAGE: 10
- MIN_LEVERAGE: 7
- XYZ_BANNED: true
- MAX_POSITIONS: 3
- MAX_DAILY_LOSS_PCT: 10

## Bootstrap Gate

On EVERY session, check `config/bootstrap-complete.json`. If missing:
1. Verify Senpi MCP
2. Create scanner cron (5 min, isolated)
3. Write `config/bootstrap-complete.json`
4. Send: "🦬 BISON v2.0 online. Conviction scoring — no hard gates. RatchetStop manages exits. Silence = no conviction."

## Notification Policy
**ONLY alert:** Position OPENED, position CLOSED (RatchetStop), critical error.
**NEVER alert:** Scanner idle, health check, DSL routine.

## Risk
| Rule | Value |
|---|---|
| Max positions | 3 |
| Max entries/day | 3 base, 6 hard cap |
| Daily loss limit | 10% |
| Per-asset cooldown | 120 min |
| Stagnation TP | 15% ROE stale for 2 hours |

## Files
| File | Purpose |
|---|---|
| `scripts/bison-scanner.py` | v2.0 conviction scorer + internal execution |
| `scripts/bison_config.py` | Config helper, MCP, state I/O |
| `config/bison-config.json` | Wallet, strategy ID, configurable params |
| `runtime.yaml` | Plugin runtime (position tracker + RatchetStop) |

## Changelog

### v2.0 (2026-04-06)
- ALL hard gates converted to score contributors
- Thesis exit (evaluate_held_position) REMOVED
- Scanner calls create_position internally (Wolverine pattern)
- ensureExecutionAsTaker: false with feeOptimizedLimitOptions
- Direction waterfall: 4H → SM → 1H momentum
- Trade counter increment on successful execution

### v1.2.1
- DSL state template in scanner output
- Dead weight cuts added
- Per-asset cooldown
- XYZ banned, leverage capped 7-10x
