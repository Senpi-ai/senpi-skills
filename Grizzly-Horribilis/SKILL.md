---
name: grizzly-horribilis-strategy
description: >-
  GRIZZLY HORRIBILIS v2.1 — BTC Contrarian Sniper (recalibrated). Fades exhausted
  SM consensus moves on BTC with conviction-scaled leverage (7x at score 10-11, 10x
  at score 12+). v2.1 tightens the gate after -35% ROE on v1.1: MIN_SCORE raised
  8→10 (matches Cheetah v5.1 APEX pattern), MIN_EXHAUSTION_PCT raised 2.5→4.5
  (matches Dog v2.2 — stops fighting fresh trends), pyramid scale-up bar raised
  9→12 (apex conviction only). 50% margin, internal execution, DSL-only exits.
license: MIT
metadata:
  author: jason-goldberg
  version: "2.1"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# GRIZZLY HORRIBILIS v2.1 — BTC Contrarian Sniper (recalibrated)

Same BTC thesis as Grizzly v3.0. Conviction-scaled leverage. Always RatchetStop protected.

## CRITICAL RULES

### RULE 1: Scanner enters. RatchetStop exits. Scanner NEVER exits positions.
### RULE 2: Scanner calls create_position internally (Wolverine pattern)
The cron command is just: `python3 grizzly-scanner.py`. No parsing, no execution in cron.

### RULE 3: MAX 1 POSITION — BTC only
### RULE 4: MAX 2 ENTRIES PER DAY. 180-minute cooldown.
### RULE 5: Leverage is conviction-scaled. MAX 10x.

| Score | Leverage |
|---|---|
| 10-11 | 7x |
| 12+ | 10x |

**40x is BANNED.** Fleet max is 10x. Score below 10 = no entry.

### RULE 6: ensureExecutionAsTaker = false
All entries use FEE_OPTIMIZED_LIMIT with ensureExecutionAsTaker: false.

### RULE 7: Margin = 50% of account value
At $1,000 account: $500 margin per trade.

### RULE 8: NEVER add to an open position
If asked about sizing on an open trade, explain the issue and fix it for the NEXT trade. Do NOT edit_position to change size mid-trade.

## How Horribilis Trades

### Entry (all score contributors, no hard gates)
| Signal | Points |
|---|---|
| SM concentration ≥15% | +3 (DOMINANT) |
| SM concentration ≥10% | +2 (STRONG) |
| SM concentration ≥5% | +1 (ALIGNED) |
| 4H price strong (≥2%) in direction | +2 |
| 4H price confirms (≥0.5%) in direction | +1 |
| 4H price opposing | -1 |
| 1H momentum confirms | +1 |
| Contribution surge (≥3%) | +2 |
| Contribution growing (≥1%) | +1 |
| Funding alignment | +1 |
| Deep consensus (≥100 traders) | +1 |

Min score: **10** (v2.1 — raised from 8). Plus **MIN_EXHAUSTION_PCT=4.5** hard gate — BTC must have already moved ≥4.5% in the SM direction before we fade it. This is the v2.1 recalibration — stops the contrarian from fighting fresh trends.

### Exit — RatchetStop Only
Phase 2 tiers lock profit aggressively:

| Trigger | Lock % | Breaches |
|---|---|---|
| 5% ROE | 30% | 3 |
| 10% ROE | 50% | 3 |
| 15% ROE | 70% | 2 |
| 20% ROE | 85% | 1 |
| 30% ROE | 90% | 1 |

## Cron Setup

| Cron | Interval | Command |
|---|---|---|
| Scanner | 3 min | `python3 /data/workspace/skills/grizzly-strategy/scripts/grizzly-scanner.py` |

One cron only. Scanner handles execution internally. DSL exit managed by plugin runtime.

## Runtime Setup
```bash
sed -i 's/${WALLET_ADDRESS}/<WALLET>/' /data/workspace/skills/grizzly-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/grizzly-strategy/runtime.yaml
openclaw senpi runtime create --path /data/workspace/skills/grizzly-strategy/runtime.yaml
openclaw senpi runtime list
```

## Bootstrap Gate
On EVERY session start, verify runtime + scanner cron (3 min, main).
Send: "🐻 HORRIBILIS v2.1 online. BTC contrarian sniper. Score≥10 entries only, 4.5% exhaustion floor, 7-10x conviction-scaled. RatchetStop protects everything. Silence = no conviction."

## Risk
| Rule | Value |
|---|---|
| Max positions | 1 (BTC only) |
| Max entries/day | 2 |
| Cooldown | 180 min |
| Margin | 50% of account |
| Leverage | 7x (score 8-9), 10x (score 10+) |
| Daily loss limit | 10% |

## Files
| File | Purpose |
|---|---|
| `scripts/grizzly-scanner.py` | BTC conviction scorer + internal execution (shared with Grizzly v3.1) |
| `scripts/grizzly_config.py` | Config helper, MCP, state I/O |
| `config/grizzly-config.json` | Wallet, strategy ID |
| `runtime.yaml` | Plugin runtime (position tracker + RatchetStop) |

## Changelog
### v2.1 (2026-04-15) — fleet-fix recalibration
- MIN_SCORE raised 8 → 10 (Cheetah v5.1 APEX pattern)
- MIN_EXHAUSTION_PCT raised 2.5 → 4.5 (Dog v2.2 fix — stops fighting fresh trends)
- SCALE_MIN_SCORE raised 9 → 12 (pyramid only on apex conviction)
- Leverage tiers shifted: 7x at 10-11, 10x at 12+ (was 7x at 8-9, 10x at 10+)
- Cooldown raised 90 → 180 min (sniper cadence)

### v1.1 (2026-04-06)
- Leverage capped at 10x (was 40x)
- Conviction-scaled: 7x at score 8-9, 10x at score 10+
- Margin increased to 50% (was 15%)
- Scanner calls create_position internally (Wolverine pattern)
- ensureExecutionAsTaker: false with feeOptimizedLimitOptions
- 4H price alignment: hard gate → score contributor
- SM thresholds: hard gate → score contributors

### v1.0
- Initial release: 7x-40x conviction-scaled leverage


---

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.
