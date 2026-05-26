---
name: osprey-strategy
description: >-
  OSPREY v1.0.0 — Cross-Venue Lag (crypto leader → XYZ equity proxy). When a
  crypto leader (BTC) makes a strong move, crypto-correlated equities priced on
  Hyperliquid XYZ (Coinbase, MicroStrategy, miners) tend to follow — but on a
  different venue, with a lag. Osprey measures each proxy's catch-up gap
  (leader move × beta − the proxy's actual move) and trades the proxy in the
  leader's direction when it still owes a gap. Distinct from Mantis (crypto→
  crypto lag from cross_asset_flows); Osprey trades the cross-VENUE crypto→
  XYZ-equity lag and self-computes the gap from candles. Let-winners-run DSL.
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

# 🐟🦅 OSPREY v1.0.0 — Cross-Venue Lag (Crypto Leader → XYZ Equity Proxy)

**When BTC moves, the crypto stocks haven't caught up yet.** Coinbase, MicroStrategy and the miners trade on Hyperliquid XYZ, priced on a different venue from spot crypto — so a sharp BTC move shows up in those equities late. Osprey measures *how far behind* each proxy is and bets it closes the gap.

## Why this strategy exists

A crypto-correlated equity has a known beta to BTC (COIN ≈ 1.8×, MSTR ≈ 2.5×). When BTC jumps, the *expected* proxy move is `BTC move × beta`. But XYZ pricing can trail spot crypto — especially around trade.xyz reference windows — so the proxy's *actual* move lags. That difference is a measurable, tradeable gap. **Distinct from Mantis:** Mantis trades crypto→crypto laggards surfaced by `market_get_cross_asset_flows`. Osprey trades the **cross-VENUE** crypto→XYZ-equity lag and **self-computes** the gap from candles, because `cross_asset_flows` only surfaces crypto laggards.

## CRITICAL RULES

### RULE 1: The leader must move first
Each tick Osprey measures the leader's (BTC's) recent move over `moveLookbackBars` 1h candles. If `|move| < minLeaderMovePct` (default 2%), nothing happens — no leader move, no lag to trade.

### RULE 2: The catch-up gap is the gate
For each proxy: `expected = leader_move × beta`, `gap = expected − proxy_actual_move`. Entry requires `|gap| >= minGapPct` (default 2%) **AND** the gap shares the leader's sign (the proxy still owes catch-up *in the leader's direction*). A proxy that already moved proportionally — or overshot (gap flips sign) — is skipped; the catch-up is done.

### RULE 3: Follow the leader
Direction = the sign of the gap: leader up + proxy lagging up → **LONG** the proxy; leader down + proxy lagging down → **SHORT**. Osprey never fades the leader.

### RULE 4: Producer enters. DSL exits.
No `close_position` call site. Once a proxy starts tracking the leader the move can extend well past the modeled gap, so the DSL is the **let-winners-run** preset — wide ladder (T0 +10% / lock 0), `max_loss 18%`, time-cuts OFF except a **96h hard_timeout** (XYZ proxies can close the gap slowly across pricing windows). No `weak_peak_cut`.

## How Osprey scores a trade

**Gate:** leader move `>= minLeaderMovePct` AND a proxy gap `>= minGapPct` in the leader's direction.

**Score components** (max ~7):

| Signal | Points |
|---|---|
| Leader moved + proxy owes a gap (gate-confirmed) | +2 |
| `|gap| >= strongGapPct` (default 5%) | +2 |
| Smart Money on the proxy confirms direction (≥ `smTiltMinPct`) | +1 |
| SM strongly tilted (≥ `smStrongTiltPct`) | +1 |
| Proxy volume rising (> 15%) | +1 |

**Floor:** `minScore: 4`. SM is often sparse on XYZ equities, so SM is a **bonus, not a gate**.

## DSL preset (let-winners-run — ride the catch-up)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 18% |
| Phase 1 | retrace_threshold | 10 |
| Time cuts | hard_timeout | **96h** |
| Time cuts | weak_peak_cut | DISABLED |
| Time cuts | dead_weight_cut | DISABLED |
| Phase 2 | T0 → T4 | +10/0 · +20/45 · +35/65 · +55/78 · +90/88 |

## Scanner pattern

Extends the **Cross-asset lag detector** archetype (#9, Mantis) across venues — see `senpi-trading-runtime/references/producer-patterns.md`. Primary MCP calls: `market_get_asset_data` (1h candles for the leader **and** each proxy — the producer signature), `leaderboard_get_markets` (SM on the proxy). Stateless gap math (no extra state cache). The pure functions (`move_pct`, `catchup_gap`, `lag_direction`) are unit-tested in `tests/test_signal.py`.

**Tuning note:** the proxy list + betas are operator-tunable in `config/osprey-config.json`. Defaults are the most common crypto-proxy equities (`xyz:COIN`, `xyz:MSTR`); add/remove names as trade.xyz lists them, and calibrate each beta to its historical sensitivity to the leader. Any proxy that returns no candles is skipped gracefully.

## Operator install

See [README.md](README.md).

## Changelog

### v1.0.0 (2026-05-26) — initial release

First fleet agent to trade a **cross-VENUE** lag (crypto spot → XYZ equity), extending Mantis's cross-asset-lag archetype. Built with the let-winners-run DSL class (wide ladder, time-cuts off except a 96h hard_timeout), taker-true entry, no null numeric signal fields, self-computed catch-up gap (no dependence on cross_asset_flows), and unit-tested pure functions.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version`. See `references/skill-attribution.md`.
