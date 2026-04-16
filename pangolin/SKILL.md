---
name: pangolin-strategy
description: >-
  PANGOLIN v1.1 — Extreme Funding Rate Fader. Enters opposite to elevated
  funding rates (>0.015%/8h = ~20% annualized, recalibrated down from
  the v1.0 40% threshold that never fired in the current market regime),
  collecting funding while waiting for crowded positions to mean-revert.
  Conservative 3-5x leverage, very wide DSL (12h hard timeout). Top 20
  crypto assets by volume.
license: MIT
metadata:
  author: jason-goldberg
  version: "1.1"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🦔 PANGOLIN v1.1 — Extreme Funding Rate Fader

An entirely new strategy archetype for the Predators fleet. No other agent trades on funding rate signals.

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
