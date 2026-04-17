---
name: grizzly-strategy
description: >-
  GRIZZLY v5.0 — BTC Alpha Hunter with Position Lifecycle. Single-asset
  BTC scanner, trend-following. Family member alongside Kodiak (SOL),
  Wolverine (HYPE), Polar (ETH) — all four share the same architecture,
  tuned per asset. v5.0 is a complete rewrite that reverts v4.x contrarian
  direction flip and ports Kodiak's winning 3-mode lifecycle (HUNTING /
  RIDING / STALKING) with BTC-specific tuning.
license: MIT
metadata:
  author: jason-goldberg
  version: "5.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🐻 GRIZZLY v5.0 — BTC Alpha Hunter

Trend-continuation on BTC. Same architecture as Kodiak / Wolverine / Polar.
One asset, one scanner, one exit system. No contrarian flips.

---

## ⛔ CRITICAL AGENT RULES

### RULE 1: Install path is `/data/workspace/skills/grizzly-strategy/`
### RULE 2: THE SCANNER DOES NOT EXIT POSITIONS — DSL only.
### RULE 3: MAX 1 POSITION (BTC only)
### RULE 4: Verify runtime on every session start
### RULE 5: Never modify parameters without family-wide review
### RULE 6: HARD_STOP circuit breaker triggers at -25% drawdown

---

## Thesis

**Pure trend continuation on BTC, never counter-trend.**

From Kodiak's lifetime top-3 SOL winners (+$133 / +$87 / +$78):

> "The absolute highest predictor of a massive directional swing is when
> the 4H, 1H, 15m, and 5m price momentum are perfectly unified in a
> single direction, AND the Smart Money leaderboard is heavily lopsided
> (>65% directional consensus) in that exact same direction."

Grizzly v5.0 applies that exact thesis to BTC. BTC is slower and heavier
than SOL, so thresholds are tightened — but the pattern is identical.

---

## v5.0 — COMPLETE REWRITE

v4.x was a contrarian SM-fader. The April 10 inversion test read "if we
flipped direction we'd win 81.8%" as evidence that SM was broken on BTC.
The correct read was: the scanner was late, entering *after* the move
exhausted. Flipping direction made those same late entries profitable
temporarily, but it also set Grizzly up to fight every real trend.

v4.x lost $245. Grizzly was the only family member running a direction
opposite to the family intent.

v5.0 returns to the family pattern:

- **REMOVED:** CONTRARIAN_FLIP — Grizzly now trades WITH SM consensus
- **REMOVED:** CONTRARIAN_EXHAUSTION_GATE — gating contrarian behavior
- **ADDED:** 3-mode state machine (HUNTING / RIDING / STALKING)
- **ADDED:** 4-timeframe alignment (5m + 15m + 1h + 4h)
- **ADDED:** 4TF_aligned bonus scoring
- **ADDED:** SM strongly tilted bonus (>65% consensus)
- **ADDED:** SM opposes HARD BLOCK
- **ADDED:** Move-exhaustion + tiring penalties
- **ADDED:** RSI filter (70/30 BTC-tuned)
- **ADDED:** Volume trend + OI-proxy growth signal
- **ADDED:** STALKING mode — reload on dip after DSL exit
- **KEPT:** Dynamic daily cap (P&L-aware circuit breaker)
- **KEPT:** has_resting_orders with auto-cancel stale
- **KEPT:** 10x MAX_LEVERAGE cap

---

## Hard Gates (all must pass)

1. **4h trend structure ≠ NEUTRAL** (BULLISH or BEARISH, >60% higher-lows or lower-highs)
2. **1h trend agrees** with 4h direction
3. **15m momentum confirms** direction (>0.05% in direction)
4. **SM opposes = HARD BLOCK** (leaderboard_get_markets)
5. **RSI filter** — LONG requires RSI ≤ 70, SHORT requires RSI ≥ 30

---

## BTC-Specific Tuning vs Kodiak (SOL)

BTC moves ~0.6% in 4h; SOL moves 2–4%. Thresholds tightened ~3x:

| Parameter | Kodiak (SOL) | Grizzly (BTC) | Why |
|---|---|---|---|
| min_mom_15m | 0.10% | 0.05% | BTC slower |
| rsi_max_long | 74 | 70 | BTC rarely pushes 74 |
| rsi_min_short | 26 | 30 | BTC rarely pushes 26 |
| move_exhaustion | 4.0% | 2.5% | BTC exhaustion at lower % |
| move_tiring | 2.5% | 1.5% | |
| 4h_strong | 4.0% | 2.0% | |
| vol_ratio_min | 1.2 | 1.1 | BTC volume always deep |
| funding_extreme | 0.005 | 0.003 | BTC lower-magnitude |

MIN_SCORE = 10 (family standard). Dynamic daily cap same as family.

---

## Scoring (max ~18 pts, MIN_SCORE = 10)

| Signal | Points |
|---|---:|
| 4h trend structure | +3 |
| 1h trend agrees | +2 |
| 15m momentum strength | +1 |
| 4TF_aligned (5m agrees) | +1 |
| SM aligned | +2 |
| SM strongly tilted (>65%) | +1 |
| 15m velocity fresh (>0.5) | +1 |
| 15M_STALE_PENALTY (cc ≤ 0) | -3 |
| Funding pays direction | +2 |
| Funding crowded against | -1 |
| Volume ratio ≥ 1.1x | +1 |
| Volume rising >15% | +1 |
| OI growing >10% | +1 |
| RSI room | +1 |
| 4h strong (>2%) | +1 |
| MOVE_EXHAUSTION (>2.5%) | -2 |
| MOVE_TIRING (>1.5%) | -1 |

---

## Position Sizing

| Score | Leverage | Margin % |
|---|---:|---:|
| 10-11 | 10x (clamped to HL max) | 30% |
| 12-13 | 10x | 37.5% |
| **14+** | **10x** | **45%** |

Leverage auto-clamped to BTC's Hyperliquid max via `strategy_get_asset_trading_limits`.

---

## Exit (DSL) — BTC-Tuned per RatchetStop Timing Guide

| Mechanism | Value | Why |
|---|---|---|
| hard_timeout | 360 min (6h) | BTC trends take time |
| weak_peak_cut | 120 min @ 2% min | BTC peaks consolidate slowly |
| dead_weight_cut | 90 min | BTC can sit quietly within a trend |
| Phase 1 max_loss | 15% | Same as Kodiak |
| Phase 1 retrace | 8% | |
| Phase 2 tier 1 | +5% → 25% lock | First profit lock |
| Phase 2 tier 2 | +10% → 45% | |
| Phase 2 tier 3 | +15% → 65% | |
| Phase 2 tier 4 | +20% → 80% | |
| Phase 2 tier 5 | +30% → 90% | Apex lock |
| Phase 2 tier 6 | +50% → 94% | Monster winner trail |

---

## 3-Mode Lifecycle

```
HUNTING → [all gates pass, score >= 10] → ENTER → RIDING
RIDING  → [DSL closes position]         → STALKING
STALKING → [reload conditions met]      → RELOAD → RIDING
STALKING → [kill conditions hit]        → RESET  → HUNTING
STALKING → [6h timeout]                 → RESET  → HUNTING
```

**HUNTING:** scanner evaluates BTC every 3 minutes. All hard gates must pass.

**RIDING:** scanner does NOT re-evaluate the position. DSL manages all exits.
(Wolverine v1.1 data: 25/27 trades killed by thesis re-evaluation, not DSL.)

**STALKING:** after DSL closes, scanner watches for conditions to reload
same direction — fresh 5m impulse + volume holding + SM still aligned +
4h trend intact. Kills if trend reverses, SM flips, funding extremes, or
OI collapses.

---

## Family Relationship

All four single-asset specialists run the same architecture:

| Agent | Asset | MIN_SCORE | DSL Profile |
|---|---|---:|---|
| Kodiak | SOL | 10 | Mid-beta (240/60/90min) |
| Wolverine | HYPE | 10 | High-beta (180/45/60min) |
| Polar | ETH | 10 | Mid-beta (240/60/90min) |
| **Grizzly** | **BTC** | **10** | **Low-beta (360/90/120min)** |

Bug fixes and signal improvements propagate across the family. If one
member discovers an edge, all four get it. If one has a bleed, all four
get audited.

---

## Install

```bash
mkdir -p /data/workspace/skills/grizzly-strategy/{config,scripts,state}

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/grizzly/runtime.yaml -o /data/workspace/skills/grizzly-strategy/runtime.yaml
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/grizzly/SKILL.md -o /data/workspace/skills/grizzly-strategy/SKILL.md
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/grizzly/config/grizzly-config.json -o /data/workspace/skills/grizzly-strategy/config/grizzly-config.json
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/grizzly/scripts/grizzly-scanner.py -o /data/workspace/skills/grizzly-strategy/scripts/grizzly-scanner.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/grizzly/scripts/grizzly_config.py -o /data/workspace/skills/grizzly-strategy/scripts/grizzly_config.py
```

## Configure

```bash
sed -i 's/${WALLET_ADDRESS}/<YOUR_STRATEGY_WALLET>/' /data/workspace/skills/grizzly-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<YOUR_TELEGRAM_CHAT_ID>/' /data/workspace/skills/grizzly-strategy/runtime.yaml
```

## Install runtime + create scanner cron

```bash
openclaw senpi runtime create --path /data/workspace/skills/grizzly-strategy/runtime.yaml
openclaw senpi runtime list
# Create 3-minute cron: python3 /data/workspace/skills/grizzly-strategy/scripts/grizzly-scanner.py
```

---

## Changelog

### v5.0 (2026-04-16) — COMPLETE REWRITE
- Direction logic flipped back from v4.x contrarian to trend-following
- Ported Kodiak's full 3-mode state machine (HUNTING/RIDING/STALKING)
- Added 4-timeframe alignment with 4TF_aligned bonus scoring
- Added SM-opposes HARD BLOCK
- Added move-exhaustion + tiring penalties
- Added RSI filter (70/30 BTC-tuned)
- Added volume trend + OI-proxy growth signals
- Added STALKING reload-on-dip mode
- DSL profile retuned per RatchetStop timing guide for BTC (low-beta)
- MIN_SCORE raised 8 → 10 (family standard)
- Margin tiers rebuilt with conviction scaling (30/37.5/45%)
- Inner-order success validation in execute_entry
- get_safe_leverage() clamps to BTC's HL max

### v4.x (deprecated) — contrarian fader
- Direction-inverted from family intent
- Lost $245 fighting real trends
- Replaced by v5.0

### v3.2 — trend-follower (pre-contrarian)

---

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills
