# 🦅 CONDOR v3.0 — One Amazing Trade per Day

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

**Pure trend continuation, never counter-trend.** Scans top 50 Hyperliquid assets by 24h notional volume for apex setups: 4h + 1h + 15m + SM direction all aligned + macro trend gate + BTC macro aligned. Goes big on apex confluence (50-80% margin, 10x leverage) and holds ONE TRADE per day while DSL manages exits.

Built from Kodiak's top 3 lifetime winners (+$133 / +$87 / +$78 on SOL) + Wolverine's HYPE SHORT post-mortem (-$160 loss stepping in front of a 32% uptrend).

## Key Settings

| Setting | Value |
|---|---|
| Universe | Top 50 HL perps by 24h volume (crypto only) |
| Min OI | $1M |
| Min trader_count | 50 |
| Min SM consensus | 65% |
| **3TF alignment** | **HARD GATE** (4h + 1h + 15m) |
| **Macro trend gate** | **HARD GATE** (no counter-trend >10%) |
| BTC macro alignment | HARD GATE (alts) |
| Min score | 11 |
| Max positions | 1 (one amazing trade) |
| Leverage cap | 10x (hard) |
| Margin | 50% / 70% / 80% by score tier |
| Daily cap | 1 entry per 24h |
| Post-exit cooldown | 120 min |
| DSL | Mid-beta profile (Kodiak-calibrated) |

## Key changes from v2.0

- **Universe:** 4 majors → top 50 by volume
- **Thesis:** multi-asset picker → single apex sniper
- **Added hard gates:** 3TF alignment, macro trend, BTC macro
- **Discipline:** 1 trade per day (was 3)
- **Leverage:** empirical 10x cap (was 7x)
- **DSL:** calibrated from Kodiak's SOL win exit tiers

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
