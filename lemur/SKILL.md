---
name: lemur-strategy
description: >-
  LEMUR v1.0.0 — Pre-IPO Perpetual (IPOP) trend follower on Hyperliquid
  XYZ. Auto-discovers IPOPs via the trade.xyz funding signature (1%
  funding multiplier = ~100x smaller funding rates) + leverage cap
  (≤5x pre-listing). Today: xyz:SPCX. Auto-expands when trade.xyz lists
  more IPOPs. Long OR short on 4h trend + Smart Money agreement.
  Moderate DSL — Discovery Bounds throttle velocity so positions don't
  snap as fast as normal perps.
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

# 🦤 LEMUR v1.0.0 — Pre-IPO Perpetual Trend Follower

**Trade pre-IPO companies as perpetuals on Hyperliquid XYZ.** Today that's only SpaceX (`xyz:SPCX`). When trade.xyz lists Anthropic, OpenAI, Stripe, Databricks, etc., Lemur picks them up automatically via the structural IPOP signature.

## Why this strategy exists

Hyperliquid XYZ is one of the only venues where retail can perp-trade private companies. IPOPs (Pre-IPO Perpetuals) are a unique product:
- **24/7 trading** (vs 23/5 for equity perps)
- **Internal oracle** until the company actually IPOs (30-min EWMA price discovery)
- **Funding multiplier 0.005** (vs 0.5 for standard XYZ perps — 100x smaller funding cost)
- **Discovery Bounds** throttle price velocity to prevent gap moves

Lemur captures the directional alpha on these markets while the standard equity perp space lacks them.

## Auto-discovery: how Lemur finds IPOPs

Per the trade.xyz design docs, IPOPs have two structural signatures:

```python
def is_ipop(inst):
    return (
        inst["name"].startswith("xyz:")
        and not inst["is_delisted"]
        and abs(float(inst["context"]["funding"])) <= 1e-7   # 100x smaller funding
        and int(inst["max_leverage"]) <= 5                    # pre-listing leverage cap
        and float(inst["context"]["dayNtlVlm"]) >= 100_000   # liquidity floor
    )
```

The funding-rate signature is robust because it directly reflects the IPOP product spec (0.005 multiplier vs 0.5). When an IPOP CONVERTS to a normal equity perp after the company actually IPOs, its funding rate jumps back to the standard formula — Lemur auto-drops it from the universe.

Today this returns `[xyz:SPCX]`. No hardcoded ticker list, no config update needed when trade.xyz lists new IPOPs.

## CRITICAL RULES

### RULE 1: Producer enters. DSL exits.
DSL Phase 1 max_loss 10% + Phase 2 Bison-pattern wide ladder own all exits. Time-cuts all disabled (IPOPs trade 24/7).

### RULE 2: Leverage auto-capped per instrument
The config has `leverage: 3` default, but each IPOP's own `max_leverage` (e.g., SPCX's 5x cap) is the hard ceiling. Producer takes the MIN of config-leverage and instrument-leverage.

### RULE 3: SM data may be sparse for pre-IPO names
Few traders, less concentration data. If `leaderboard_get_markets` returns no entry for the IPOP, the producer falls back to "4h trend only" and tags the signal `sm_data_sparse_assumed_aligned`. Once a company has been listed and accumulates trader history, SM data fills in.

### RULE 4: 15-min tick cadence (vs 5-min for normal trend strategies)
Discovery Bounds throttle IPOP price moves to limited velocity. Polling every 5 min is wasteful — the underlying isn't moving fast enough to justify the MCP cost.

## How Lemur scores a trade

**Gates** (all required):
1. Asset matches IPOP signature (funding + leverage + liquidity)
2. 4h trend non-neutral
3. SM direction agrees with 4h trend (OR SM data unavailable → assume aligned)
4. SM tilt >= 55% (if SM data available)

**Score components** (max ~9):

| Signal | Points |
|---|---|
| 4h trend aligned (with strength %) | +3 |
| 1h trend confirms 4h | +2 |
| SM aligned (gate-confirmed; "assumed_aligned" if SM data sparse) | +2 |
| SM strongly tilted (>= 70%) | +1 |

## DSL preset (moderate, IPOP-tuned)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | **10%** (moderate — Discovery Bounds limit downside velocity) |
| Phase 1 | retrace_threshold | 8 |
| Phase 1 | consecutive_breaches | 1 |
| Time cuts | hard_timeout | **DISABLED** (24/7 trading, multi-day theses welcome) |
| Time cuts | weak_peak_cut | DISABLED |
| Time cuts | dead_weight_cut | DISABLED |
| Phase 2 | T0 | +10% / 0% lock |
| Phase 2 | T1 | +20% / 25% lock |
| Phase 2 | T2 | +30% / 40% lock |
| Phase 2 | T3 | +50% / 60% lock |
| Phase 2 | T4 | +75% / 75% lock |
| Phase 2 | T5 | +100% / 85% lock (apex) |

## Risk guardrails

| Gate | Setting |
|---|---|
| max_entries_per_day | 3 |
| per_asset_cooldown_minutes | 360 (6h, IPOPs move slow) |
| daily_loss_limit_pct | 15 |
| max_consecutive_losses | 3 |
| drawdown_halt_pct | 20 |
| Slots | 2 (small universe today) |

## Scanner pattern

This strategy uses the **XYZ universe filter scanner with funding-signature discovery** — see `senpi-trading-runtime/references/producer-patterns.md` (extends Pattern 4, Bison-style multi-asset, with XYZ-specific filters). Primary MCP calls: `market_list_instruments` (universe discovery), `market_get_asset_data`, `leaderboard_get_markets`.

## Operator install

See [README.md](README.md).

## Changelog

### v1.0.0 (2026-05-20) — initial release

First Senpi skill explicitly designed for trade.xyz IPOPs. Funding-rate signature heuristic future-proofs against universe expansion. Universe = `[xyz:SPCX]` today.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).

## Skill Attribution

See `references/skill-attribution.md`.
