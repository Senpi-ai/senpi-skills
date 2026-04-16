---
name: condor-strategy
description: >-
  CONDOR v3.0 — "One Amazing Trade per Day." Complete rewrite from v2.0
  multi-asset thesis picker. Scans top 50 Hyperliquid assets by 24h notional
  volume for apex trend-continuation setups: 4h + 1h + 15m + SM direction
  all aligned, SM consensus >=65%, MACRO TREND GATE (no fighting runaway
  trends), BTC macro aligned. Goes big on apex confluence (50-80% margin,
  10x leverage, MIN_SCORE=11) and holds ONE TRADE per day while DSL manages
  exits. Built from Kodiak's top 3 lifetime winners (+$133 / +$87 / +$78 on
  SOL) + Wolverine's HYPE SHORT post-mortem identifying the counter-trend
  failure mode. Max 1 position, 2h post-exit cooldown.
license: MIT
metadata:
  author: jason-goldberg
  version: "3.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🦅 CONDOR v3.0 — One Amazing Trade per Day

Top 50 assets. Pure trend continuation. Apex confluence only. One trade a day.

---

## ⛔ CRITICAL AGENT RULES

### RULE 1: Install path is `/data/workspace/skills/condor-strategy/`
### RULE 2: THE SCANNER DOES NOT EXIT POSITIONS — DSL only.
### RULE 3: MAX 1 POSITION (the one amazing trade)
### RULE 4: Verify runtime on every session start
### RULE 5: Never modify parameters
### RULE 6: MAX 1 ENTRY per 24h
### RULE 7: 120-minute post-exit cooldown before next entry
### RULE 8: HARD_STOP circuit breaker triggers at -25% drawdown

---

## Thesis

**Pure trend continuation, never counter-trend.**

From Kodiak's lifetime top 3 winners (all SOL, +$133 / +$87 / +$78):

> "The absolute highest predictor of a massive directional swing is when
> the 4H, 1H, 15m, and 5m price momentum are perfectly unified in a
> single direction, AND the Smart Money leaderboard is heavily lopsided
> (>65% directional consensus) in that exact same direction."

From Wolverine's HYPE SHORT post-mortem (-$160 loss, 2026-04-16):

> "We stepped in front of a runaway 32% freight train to catch a 1%
> micro-retrace. The historical winners traded with the massive macro
> shift, not against it."

Condor v3.0 enforces both insights as hard gates.

---

## Hard Gates (all must pass — fail = skip asset)

1. **Not XYZ, not stablecoin**
2. **OI > $1M USD** (PR #196 context-aware read)
3. **trader_count >= 50** (signal validity)
4. **3TF ALIGNMENT** — 4h_price + 1h_price + 15m SM velocity all aligned in entry direction, each clearing magnitude threshold
5. **MACRO TREND GATE** — if `|4h_move| > 10%` in OPPOSITE direction of entry, BLOCK. No stepping in front of freight trains.
6. **SM consensus >= 65%** in entry direction
7. **BTC macro aligned** — alts don't fight strong BTC macro (BTC 4h > 1.5% in opposite direction = block)

---

## Scoring (max ~18 pts, MIN_SCORE=11)

| Signal | Points |
|---|---:|
| 4h move magnitude: >2% (+2), >4% (+3), >6% (+4) | 1-4 |
| 1h confirmation: >0.5% (+1), >1% (+2) | 1-2 |
| 15m SM velocity: >1.0 (+1), >2.0 (+2) | 1-2 |
| 3TF_ALIGNED bonus | +3 |
| SM consensus: >=65% (+2), >=75% (+3), >=80% STRONGLY_TILTED (+4) | 2-4 |
| trader_count >= 100 (DEEP_CONSENSUS) | +1 |
| Funding pays direction | +1 |
| BTC macro confirms (aligned, >1.5%) | +1 |
| Peak session (13-19 UTC or 00-05 UTC) | +1 |

---

## Position Sizing (Kodiak empirical 10x cap)

| Score | Leverage | Margin |
|---|---:|---:|
| 11-12 | 10x (clamped to asset max) | 50% of equity |
| 13-14 | 10x | 70% of equity (HIGH) |
| **15+ (APEX)** | **10x** | **80% of equity** |

**Never above 10x leverage.** Both Kodiak and Wolverine confirmed 10x is the empirical ceiling — above this, fees + wick vulnerability destroy the edge.

Leverage auto-clamped to per-asset Hyperliquid max via `strategy_get_asset_trading_limits`.

---

## Exit (DSL) — Mid-Beta Profile

Calibrated from Kodiak's SOL winners. Single-breach onchain RatchetStop.

| Mechanism | Value | Rationale |
|---|---|---|
| hard_timeout | 1440 min (24h) | One trade per day — close if not working by EOD |
| weak_peak_cut | 120 min @ 3% min | Peak stale for 2h = not developing |
| dead_weight_cut | 60 min | No movement = dead |
| Phase 1 max_loss | 20% | Same level that killed Wolverine's HYPE SHORT — but that was counter-trend. Kodiak's SOL trades didn't hit Phase 1. |
| Phase 1 retrace | 8% | |
| Phase 2 tier 1 | 8% ROE trigger → 30% HW lock | Kodiak's Tier 1 |
| Phase 2 tier 2 | 15% → 50% | Tier 2 |
| Phase 2 tier 3 | 25% → 70% | Kodiak's #3 trade exit tier |
| Phase 2 tier 4 | 40% → 85% | Kodiak's #1/#2 exit tier |
| Phase 2 tier 5 | 60% → 90% | Infinite trail on monster winners |
| Phase 2 tier 6 | 100% → 94% | Monster-class winners |

**Note:** DSL profile is mid-beta default. High-beta alts (HYPE/WIF/AVAX) would benefit from wider tiers (12%/20%, 20%/40%, 35%/65%, 55%/80%). Low-beta majors (BTC) would benefit from tighter (5%/35%, 10%/55%). Future v3.1 could branch per-asset; v3.0 ships with mid-beta as universal.

---

## One-Trade-a-Day Discipline

- **Max 1 concurrent position.** No diversification — we're swinging for ONE big winner.
- **Daily cap = 1** (circuit breaker triggers 0 at -25% drawdown).
- **2h post-exit cooldown** before next entry on ANY asset.
- **24h hard_timeout** on DSL — if position isn't working by end of day, close.

---

## Install

```bash
mkdir -p /data/workspace/skills/condor-strategy/{config,scripts,state}

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/condor/runtime.yaml -o /data/workspace/skills/condor-strategy/runtime.yaml
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/condor/SKILL.md -o /data/workspace/skills/condor-strategy/SKILL.md
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/condor/config/condor-config.json -o /data/workspace/skills/condor-strategy/config/condor-config.json
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/condor/scripts/condor-scanner.py -o /data/workspace/skills/condor-strategy/scripts/condor-scanner.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/condor/scripts/condor_config.py -o /data/workspace/skills/condor-strategy/scripts/condor_config.py
```

## Configure

```bash
sed -i 's/${WALLET_ADDRESS}/<YOUR_STRATEGY_WALLET>/' /data/workspace/skills/condor-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<YOUR_TELEGRAM_CHAT_ID>/' /data/workspace/skills/condor-strategy/runtime.yaml
```

## Install runtime + create scanner cron

```bash
openclaw senpi runtime create --path /data/workspace/skills/condor-strategy/runtime.yaml
openclaw senpi runtime list
# Create 3-minute cron: python3 /data/workspace/skills/condor-strategy/scripts/condor-scanner.py
```

---

## Changelog

### v3.0 (2026-04-16) — COMPLETE REWRITE
- Thesis flipped: multi-asset thesis picker → single-position trend-continuation apex sniper
- Universe expanded: 4 majors → top 50 HL assets by 24h volume
- Added 3TF_ALIGNMENT hard gate (Kodiak's pattern)
- Added MACRO_TREND_GATE (Wolverine's insight — no counter-trend fights)
- Added BTC macro alignment check
- Added peak-session bonus (13-19 UTC or 00-05 UTC)
- Sizing scales with score: 50% / 70% / 80% margin
- Leverage hard-capped at 10x (Kodiak empirical)
- DSL profile calibrated from Kodiak's SOL winner exit tiers
- ONE TRADE PER DAY discipline (max 1 position, 24h hard timeout)
- Fleet-standard: canonical MCP schema, leverage clamping, inner-order validation, context-aware reads

### v2.x (pre-2026-04-16)
- Multi-asset thesis picker (BTC/ETH/SOL/HYPE)
- Conviction-scaled margin
- Not fit for the single-breach DSL era

---

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills
