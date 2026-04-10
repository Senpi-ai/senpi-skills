---
name: kestrel-strategy
description: >-
  KESTREL v1.0 — XYZ Macro Breakout Rider. Detects significant hourly price
  breakouts on commodities, precious metals, indices, and high-volume equities
  24/7 on Hyperliquid. Rides the macro trend. Price action is the primary
  signal; Smart Money is confirmation only. Conservative 3-5x leverage.
license: MIT
metadata:
  author: jason-goldberg
  version: "1.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🦅 KESTREL v1.0 — XYZ Macro Breakout Rider

A fundamentally new approach to XYZ trading on Hyperliquid. Where Bald Eagle used SM consensus (thin, lagging on XYZ), Kestrel uses price action as the primary signal and SM as confirmation.

## Thesis

When a commodity or equity moves >1.5% in 1 hour 24/7 on Hyperliquid, something macro happened. Unlike crypto where big moves exhaust quickly, commodity and equity macro moves tend to continue for hours or days. Kestrel catches the breakout and rides the trend.

## Why this is different from Bald Eagle

| | Bald Eagle | Kestrel |
|---|---|---|
| Primary signal | SM consensus | Price action (breakout) |
| SM role | Trigger | Confirmation only |
| Direction | Contrarian (v4.0) | Momentum (ride the trend) |
| Trigger condition | SM concentration >3% | 1H price move >1.5% |
| Asset universe | 6 assets (CL, BRENTOIL, GOLD, SILVER, SP500, XYZ100) | 13 assets (adds AAPL, NVDA, GOOGL, TSLA, AMZN, META, MSFT) |

## Assets

Commodities: CL, BRENTOIL. Metals: GOLD, SILVER. Indices: SP500, XYZ100.
Equities: AAPL, NVDA, GOOGL, TSLA, AMZN, META, MSFT.

## Scoring

- 1H breakout magnitude (2-4 pts) — the core signal
- 4H trend alignment (0-2 pts)
- Volume surge (0-2 pts)
- SM confirmation (0-2 pts, can be -1 if SM is heavily opposing)
- Funding alignment (0-1 pts)
- Spread gate (hard gate — don't trade if spread >0.2%)

MIN_SCORE: 6. Runs 24/7 — Hyperliquid XYZ trades around the clock.

## Files

| File | Purpose |
|---|---|
| `scripts/kestrel-scanner.py` | Breakout detection scanner |
| `scripts/kestrel_config.py` | Config helper |
| `runtime.yaml` | Wide DSL for macro trend riding |
