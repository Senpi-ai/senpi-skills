---
name: pangolin-strategy
description: >-
  PANGOLIN v1.2 — Extreme Funding Rate Fader (universe expansion).
  Enters opposite to elevated funding rates (>0.015%/8h = ~20% annualized),
  collecting funding while waiting for crowded positions to mean-revert.
  Conservative 3-5x leverage, very wide DSL (12h hard timeout). v1.2
  replaces the hardcoded top-20 ALLOWED_ASSETS whitelist with a dynamic
  liquidity floor (OI > $3M) — now scans ~60 assets instead of 20,
  unlocking mid-cap unwind setups (ZEC/MON/LIT hit >1000% annualized
  at probe time but were filtered out pre-v1.2).
license: MIT
metadata:
  author: jason-goldberg
  version: "1.2"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🦔 PANGOLIN v1.2 — Extreme Funding Rate Fader (universe expansion)

An entirely new strategy archetype for the Predators fleet. No other agent trades on funding rate signals.

## v1.2 changelog (fleet-fix batch 5, 2026-04-16)

- **UNIVERSE EXPANSION.** Removed the hardcoded `ALLOWED_ASSETS` set
  of 20 top-volume majors. Now scans every instrument with OI > $3M
  (~60 assets). This addresses Owl's diagnostic finding that extreme
  funding signals (>1000% annualized on ZEC/MON/LIT at probe time)
  were happening on mid-cap assets Pangolin was filtering out.
- XYZ DEX remains banned.

## v1.1 changelog (fleet-fix batch 4)

- `MIN_FUNDING_RATE` lowered from 0.0003 (0.03%/8h = ~40% annualized) to
  0.00015 (0.015%/8h = ~20% annualized). v1.0 never fired: peak funding
  in the current regime was only 9% annualized. Recalibrated threshold
  re-engages the signal.

## Thesis

When funding rates are elevated (>0.015%/8h = ~20% annualized), the crowd is paying to maintain their position. These extremes mean-revert within 24-48h as the cost of carry forces liquidation or position reduction. Pangolin enters opposite to the funding direction, collecting funding every 8 hours while waiting for the crowd to capitulate.

Two edge sources:
1. **Funding collection** — paid every 8h while holding the position
2. **Price reversal** — when the overcrowded side unwinds, price moves in our direction

## How it differs from other fleet agents

- **Barracuda** follows the trend and collects funding as a bonus. Pangolin fades funding extremes.
- **Lemon** fades individual degen traders. Pangolin fades aggregate market crowding.
- **Vulture** fades SM consensus exhaustion on price. Pangolin fades funding rate extremes.
- **Owl** fades crowding via SM concentration. Pangolin uses the quantitative funding rate threshold.

## Files

| File | Purpose |
|---|---|
| `scripts/pangolin-scanner.py` | Funding rate scanner |
| `scripts/pangolin_config.py` | Config helper |
| `runtime.yaml` | DSL exit config (very wide for funding mean-reversion) |


---

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.
