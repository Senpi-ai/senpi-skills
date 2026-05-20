---
name: raccoon-strategy
description: >-
  RACCOON v1.0.0 — Weekend XYZ reconciliation trader. ONLY emits signals
  during Fri 22:00 UTC → Mon 00:00 UTC, when trade.xyz has no external
  pricing for equities/commodities and uses internal oracle only.
  Captures the Mon-open snap-back when external pricing resumes. Broad
  XYZ universe (excludes IPOPs, which Lemur owns). LONG OR SHORT on
  >2% directional move + volume confirmation + SM direction. Tight DSL
  — Phase 1 12% max_loss, Phase 2 locks fast at +5%, hard_timeout 48h.
license: MIT
metadata:
  author: jason-goldberg
  version: "1.0.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime>=1.1.0
    - senpi_runtime_helpers
---

# 🦝 RACCOON v1.0.0 — Weekend XYZ Reconciliation

**The XYZ weekend is dead — except for the reconciliation gap.** Fri 17:00 ET through Sun 18:00 ET, trade.xyz has no external pricing for equities or commodities. The market trades against an internal 30-min EWMA oracle that drifts based on impact-price activity. When external pricing RESUMES Sunday evening, the internal-implied price snaps to the real external value. **That snap is the edge.**

## Why this strategy exists

Most Senpi strategies activate 24/7. Raccoon is the opposite — it ONLY runs during the weekend window (Fri 22:00 UTC → Mon 00:00 UTC). Outside that window, the producer outputs `OUTSIDE_WEEKEND_WINDOW` and does nothing.

This is the structural pricing gap documented by trade.xyz:
- **23/5 external coverage**: Sun 6pm ET - Fri 5pm ET (with 5-6pm ET daily gaps)
- **Weekend internal-only**: Fri 5pm ET → Sun 6pm ET
- **Reconciliation point**: Sun 6pm ET when external pricing resumes

When sentiment accumulates during the weekend (news, geopolitical events, related-market moves), it shows up as directional pressure on the internal oracle even without external anchoring. Raccoon positions in that direction, captures the snap-back when external pricing returns Monday open.

## CRITICAL RULES

### RULE 1: Weekend window is hard-coded
Producer checks UTC time at the start of each tick. Outside Fri 22:00 UTC → Mon 00:00 UTC, no MCP calls, no signals. Saves cost and prevents false weekday signals.

### RULE 2: IPOPs excluded
IPOPs (auto-detected by Lemur via low funding signature) trade 24/7 with their own internal mechanism. Raccoon filters them out (`min_max_leverage >= 10` excludes the 5x-leverage IPOP cap). Don't double-cover Lemur's territory.

### RULE 3: hard_timeout 48h is mandatory
Raccoon positions MUST exit before the next weekend starts. 48h cap from entry guarantees the position closes by Monday end-of-day at latest.

### RULE 4: Producer enters. DSL exits.
Phase 1 max_loss 12% + Phase 2 tight ladder + hard_timeout 48h own all exits.

## How Raccoon scores a trade

**Gates** (all required):
1. Current UTC time within Fri 22:00 → Mon 00:00
2. Instrument is xyz: with max_leverage >= 10 (excludes IPOPs) and daily vol >= $1M
3. 48h directional move >= 2%
4. SM direction agrees
5. SM tilt >= 55%

**Score components** (max ~9):

| Signal | Points |
|---|---|
| 48h move >= 4% (strong) | +3 |
| 48h move 2.5-4% (medium) | +2 |
| 48h move 2-2.5% (weak) | +1 |
| SM aligned (gate-confirmed) | +2 |
| SM strongly tilted (>= 70%) | +1 |
| Volume ratio recent/prior >= 1.5x | +1 |

## DSL preset (tight, weekend-tuned)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | **12%** |
| Phase 1 | retrace_threshold | 8 |
| Time cuts | hard_timeout | **48h** (forces Mon-open exit) |
| Time cuts | weak_peak_cut | DISABLED |
| Time cuts | dead_weight_cut | DISABLED |
| Phase 2 | T0 | **+5% / lock 30%** (lock fast on reconciliation snap) |
| Phase 2 | T1 | +10% / lock 50% |
| Phase 2 | T2 | +20% / lock 65% |
| Phase 2 | T3 | +35% / lock 75% |
| Phase 2 | T4 | +60% / lock 85% |

## Risk guardrails

| Gate | Setting |
|---|---|
| max_entries_per_day | 4 |
| per_asset_cooldown_minutes | 480 (8h) |
| Slots | 3 |
| daily_loss_limit_pct | 15 |
| drawdown_halt_pct | 20 |

## Scanner pattern

This strategy uses the **XYZ universe filter with weekend-gate activation** — see `senpi-trading-runtime/references/producer-patterns.md`. Primary MCP calls: `market_list_instruments` (universe), `market_get_asset_data`, `leaderboard_get_markets`.

## Operator install

See [README.md](README.md). Run continuously — the producer self-gates to the weekend window.

## Changelog

### v1.0.0 (2026-05-20) — initial release

First Senpi skill explicitly designed around trade.xyz's external-pricing gap. Weekend-only activation, IPOP exclusion (Lemur's territory), Mon-open hard timeout, tight DSL for the reconciliation snap.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).

## Skill Attribution

See `references/skill-attribution.md`.
