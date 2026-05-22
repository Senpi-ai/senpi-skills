---
name: badger-strategy
description: >-
  BADGER v1.0.0 — OI-Divergence Breakout Anticipator. Open interest is
  the fuel of a breakout: a price push beyond the prior 24h range that is
  confirmed by RISING open interest = new money committing = real
  follow-through; a breakout on flat/declining OI is a fakeout. LONG on an
  up-breakout + rising OI + SM net long; SHORT on the mirror. Universe:
  BTC, ETH, SOL, HYPE. Wide "let winners run" DSL — Phase 1 max_loss 20%,
  Phase 2 T0 +10% / lock 0 → T5 +100% / lock 85, time-cuts disabled.
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

# 🦡 BADGER v1.0.0 — OI-Divergence Breakout Anticipator

**Only take the breakout the order flow confirms.** Badger waits for price to break its prior 24h range AND for open interest to be *rising* into that break — proof that new money is committing, not just stops getting run. A breakout on flat or declining OI is a fakeout; Badger skips it.

## Why this strategy exists

Pure price-breakout strategies (like Hawk) fire on every range break — and most range breaks fail. The single best filter for "will this breakout follow through?" is **open interest**:

- **Price breaks out + OI rising** → new positions are being opened in the breakout direction. Real conviction, real follow-through.
- **Price breaks out + OI flat/falling** → no new money; the move is shorts covering or stops triggering. Fakeout. Skip.

Badger is the OI-confirmed version of a breakout buyer. The rising-OI gate is what separates it from Hawk.

## CRITICAL RULES

### RULE 1: OI-rising is a hard gate
Open interest must be rising at least `oiRisingMinPct` (default 2%) over the 1h window. Below that, no entry — regardless of how clean the price breakout looks. This is the whole edge.

### RULE 2: OI velocity has a fallback
The runtime's `market_get_asset_data` exposes an `oi_velocity` object, but it can be `null`. When it is, Badger falls back to a **self-computed OI delta** from a persisted last-OI cache (`state/oi-state.json`). The cache warms up after one tick per asset, so a freshly-started Badger may output `WAITING` for the first tick on each asset until it has a prior OI reading to compare against.

### RULE 3: Producer enters. DSL exits.
No `close_position` call site. DSL Phase 1 max_loss + Phase 2 wide ladder + (disabled) time-cuts own all exits. A confirmed breakout can run far — the wide ladder lets it.

### RULE 4: Universe is BTC, ETH, SOL, HYPE
Operators can override via `universe` in config. Low-liquidity coins produce noisy OI readings; keep the universe liquid.

## How Badger scores a trade

**Gates** (all required):
1. Price breaks the prior 24h high (LONG) or low (SHORT)
2. OI rising >= `oiRisingMinPct` (default 2%)
3. SM direction agrees with the breakout direction
4. SM tilt >= `smTiltMinPct` (default 55%)

**Score components** (max ~10):

| Signal | Points |
|---|---|
| Breakout magnitude ≥ 1.0% | +3 |
| Breakout magnitude 0.3–1.0% | +2 |
| Breakout magnitude < 0.3% | +1 |
| OI rising (gate-confirmed) | +2 |
| OI strongly building (≥ `oiStrongPct`, default 5%) | +1 |
| SM aligned (gate-confirmed) | +2 |
| SM strongly tilted (≥ 70%) | +1 |
| 4h structure confirms breakout direction | +1 |
| Volume rising (> 10%) | +1 |

**Floor:** `minScore: 5`. Typical entry clears 6–8.

## DSL preset (wide — let winners run)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | **20%** (cuts a failed breakout) |
| Phase 1 | retrace_threshold | 8 |
| Phase 1 | consecutive_breaches | 1 |
| Time cuts | hard_timeout | **DISABLED** |
| Time cuts | weak_peak_cut | **DISABLED** |
| Time cuts | dead_weight_cut | **DISABLED** |
| Phase 2 | T0 | **+10% / lock 0** (let it breathe) |
| Phase 2 | T1 | +20% / lock 25% |
| Phase 2 | T2 | +30% / lock 40% |
| Phase 2 | T3 | +50% / lock 60% |
| Phase 2 | T4 | +75% / lock 75% |
| Phase 2 | T5 | +100% / lock 85% |

## Scanner pattern

This strategy uses the **Multi-asset whitelist scanner with OI-confirmed breakout scoring** — see `senpi-trading-runtime/references/producer-patterns.md`. Primary MCP calls: `market_get_asset_data` (candles + `asset_context.openInterest` + `oi_velocity`), `leaderboard_get_markets` (SM direction).

## Operator install

See [README.md](README.md).

## Changelog

### v1.0.0 (2026-05-21) — initial release

Gap-filler in the multi-signal whitelist family. First fleet agent to gate breakouts on open-interest velocity. Built with the 2026-05-21 HYPE post-mortem guidelines baked in: wide "let winners run" Phase 2 ladder (a confirmed breakout can run far), taker-fallback entries (a breakout entry must fill — maker-only risks CREATE_ORDER_RESTING), exit timeout 30s, and no null numeric signal fields.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version`. See `references/skill-attribution.md`.
