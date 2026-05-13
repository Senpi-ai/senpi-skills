---
name: python-strategy
description: >-
  Patience hunter. Multi-day LONG-biased trend holds on the top 50 HL
  assets — target 96-hour hold, 36% win rate paired to a 3.14:1
  win/loss ratio. Wide DSL Phase 1 retrace breathes through
  intra-trend pullbacks; weak-peak cut disabled so winners only close
  on price action.
license: MIT
metadata:
  author: jason-goldberg
  version: "2.0.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime>=1.1.0
    - senpi_runtime_helpers
---

# 🐍 PYTHON v2.0.0 — The Patience Hunter

## v2.0.0 (2026-05-12) — plumbing-only migration

NO thesis change. v1.2 scoring + MIN_SCORE 8 + LONG bias bonus + MACRO_TREND_GATE + 3-7x leverage tiers + conviction-scaled margin + 96h hard_timeout + weak_peak_cut DISABLED all preserved verbatim. Migration: mcporter → SenpiClient, scanner-side `create_position` → producer `push_signal()` with LLM-gated runtime action, Python state files → `runtime.yaml risk.guard_rails`, MARKET exits → FEE_OPTIMIZED_LIMIT.

The snake doesn't chase. It waits for the right meal to wander into range
and then holds tight for days. Python is the fleet's first multi-day hold
agent.

---

## ⛔ CRITICAL AGENT RULES

### RULE 1: Install path is `/data/workspace/skills/python-strategy/`
### RULE 2: THE SCANNER DOES NOT EXIT POSITIONS — DSL only.
### RULE 3: MAX 2 POSITIONS concurrent — wider than single-asset specialists
### RULE 4: Verify runtime on every session start
### RULE 5: Never modify parameters without flagging
### RULE 6: HARD_STOP circuit breaker triggers at -25% drawdown
### RULE 7: Hold is the edge. If tempted to exit early, DON'T — let DSL work.

---

## Why This Agent Exists

Every other Senpi predator (Kodiak, Condor, Wolverine, Polar, Grizzly,
Scorpion, Orca, Mantis, etc.) scans fast, strikes, and rotates out within
1-12 hours. Every one is a short-term momentum hunter.

None of them hold for days. That's a blind spot.

Analysis of pr0br000's Arena account (`0x5b1c...5c0e`, userId M192140)
revealed the fleet is missing an entire archetype:

- **101 trades across 62 assets, $800 → $2,573 in 27 days (+221%)**
- 36 wins / 64 losses = **36% win rate** (they LOSE more than they win)
- Win dollars: $3,017 / Loss dollars: $960 = **3.14:1 win/loss ratio**
- Top 5 winners held 20-114 hours, drove **90% of gross profit**
- All top 5 winners were LONG, 3-5x leverage, mid-beta assets (WLD, HEMI, MON, XPL)
- Fee drag only 12% (vs 94% on some fleet agents)

Python embeds these findings as design constraints, not suggestions.

---

## Philosophy

**Don't try to win every trade. Try to NOT MISS the one big multi-day winner.**

| Most predators (Kodiak, Condor, etc.) | Python |
|---|---|
| High win rate on small wins | Low win rate, ratio does the work |
| Many trades per day | 1-3 trades per day |
| Hourly holds | Multi-day holds |
| 10x leverage | 3-5x typical, 7x apex |
| Single-asset or thesis-picker | Wide universe (top 50) |
| DSL tightens early | DSL loose early, tight late |

---

## Hard Gates (all must pass)

1. **4h trend structure ≠ NEUTRAL** — must have macro direction
2. **1h trend agrees with 4h**
3. **15m momentum confirms direction** (≥0.1% in direction)
4. **MACRO TREND GATE** — no counter-trend against runaway moves >10%
5. **SM opposes = HARD BLOCK** (consensus-backed asymmetry)
6. **RSI filter** — LONG blocked if RSI > 78, SHORT blocked if RSI < 22
7. **Move-exhaustion penalty** — active on moves >4% against direction
8. **OI > $1M**, trader_count ≥ 30 (wider than Condor's 50)
9. **Not stablecoin, not XYZ DEX**

---

## Scoring (MIN_SCORE = 8)

| Signal | Points |
|---|---:|
| 4h trend strong (>80% higher-lows/lower-highs) | +4 |
| 4h trend confirmed (60-80%) | +3 |
| 4h trend weak (55-60%) | +2 |
| 1h momentum strong (>1%) | +2 |
| 1h momentum ok (>0.5%) | +1 |
| **Daily candle trend strong (>1%)** | **+2** |
| Daily candle trend ok | +1 |
| **LONG_bias bonus** (pr0br000 insight) | **+1** |
| SM aligned | +2 |
| SM strongly tilted (>70%) | +1 |
| 15m velocity fresh | +1 |
| Funding pays direction | +1 |
| Funding crowded against | -1 |
| Volume ratio ≥ 1.3x | +1 |
| Volume weak (<0.6x) | -1 |
| RSI room (midrange) | +1 |
| MOVE_EXHAUSTION (≥6% with us) | -2 |
| MOVE_TIRING (≥4% with us) | -1 |

---

## Position Sizing (Kodiak's empirical 7x cap for patience)

| Score | Leverage | Margin |
|---|---:|---:|
| 8-9 | 3x | 25% |
| 10-11 | 5x | 30% |
| **12+** | **7x** | **40%** |

Never exceeds 7x. pr0br000 averaged 4-5x on winners. Higher leverage
destroys the edge via funding + liquidation sensitivity on multi-day holds.

Leverage auto-clamped to per-asset HL max via `strategy_get_asset_trading_limits`.

---

## Exit (DSL) — Patience Profile

The whole point of Python is to NOT exit early.

| Mechanism | Value | Rationale |
|---|---|---|
| hard_timeout | 5760 min (96h / 4 days) | pr0br000's max hold was 114h |
| weak_peak_cut | 720 min (12h) @ 3% min | Let consolidations develop |
| dead_weight_cut | 360 min (6h) | Don't cut pauses in trend |
| Phase 1 max_loss | 20% | Patient on losers too |
| Phase 1 retrace | 10% | |
| Phase 2 tier 1 | +5% → 15% lock | LOOSE — let winners breathe |
| Phase 2 tier 2 | +12% → 35% | |
| Phase 2 tier 3 | +25% → 60% | |
| Phase 2 tier 4 | +50% → 80% | pr0br000 MON/WLD tier |
| Phase 2 tier 5 | +100% → 90% | Monster winner trail |
| Phase 2 tier 6 | +200% → 94% | Ultra-rare monster (pr0br000 had 2) |

**Loose early locks are the feature, not the bug.** Aggressive Phase 2
early locking would exit the +1000% tail trades pr0br000 won on.

---

## Runtime Controls

- **Scan interval:** 10 minutes (most predators run 3 min)
- **Max concurrent positions:** 2
- **Daily entry cap:** 3 (dynamic — hot hand unlocks more, drawdown shrinks)
- **Per-asset cooldown:** 12 hours after exit
- **Same-direction cooldown:** 6 hours after any trade
- **Universe size:** top 50 HL perps by 24h volume (crypto only)
- **LONG bias:** +1 score bonus (pr0br000's top 5 were all LONG)

---

## Install

```bash
mkdir -p /data/workspace/skills/python-strategy/{config,scripts,state}

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/python/runtime.yaml -o /data/workspace/skills/python-strategy/runtime.yaml
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/python/SKILL.md -o /data/workspace/skills/python-strategy/SKILL.md
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/python/config/python-config.json -o /data/workspace/skills/python-strategy/config/python-config.json
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/python/scripts/python-scanner.py -o /data/workspace/skills/python-strategy/scripts/python-scanner.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/python/scripts/python_config.py -o /data/workspace/skills/python-strategy/scripts/python_config.py
```

## Configure

```bash
sed -i 's/${WALLET_ADDRESS}/<YOUR_STRATEGY_WALLET>/' /data/workspace/skills/python-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<YOUR_TELEGRAM_CHAT_ID>/' /data/workspace/skills/python-strategy/runtime.yaml
```

## Install runtime + create scanner cron

```bash
openclaw senpi runtime create --path /data/workspace/skills/python-strategy/runtime.yaml
openclaw senpi runtime list
# Create 10-minute cron: python3 /data/workspace/skills/python-strategy/scripts/python-scanner.py
```

---

## Best For

- Operators who want to complement a short-term predator fleet with a
  multi-day hold strategy
- Capital sizes $1K+ (Python's 2-position max needs enough margin per slot)
- Users comfortable watching paint dry for days while trades mature
- Users who understand that 36% win rate with a 3:1 ratio beats 60% with
  a 1.5:1 ratio

## Not For

- Operators wanting fast rotation or daily closures
- Capital below $500 (margin per slot is too small)
- Users uncomfortable with multi-day holds through drawdowns

---

## Changelog

### v1.0 (2026-04-17) — FIRST SHIP
- New agent, new archetype
- Full Kodiak-family architecture (4-timeframe gates, SM HARD BLOCK,
  move-exhaustion, RSI filter)
- Wide universe (top 50 by volume, crypto only)
- MIN_SCORE 8 (lower than Condor's 11 — casts wider net)
- Max leverage 7x (lower than family's 10x)
- Max 2 concurrent positions
- DSL profile: 96h hard timeout, 12h weak peak, 6h dead weight
- Phase 2 tiers extended to +200% lock for monster-winner trails
- LONG-bias +1 scoring bonus
- Derived from pr0br000's Arena Weeks 2-3 pattern analysis

---

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills
