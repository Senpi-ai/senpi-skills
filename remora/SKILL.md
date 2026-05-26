---
name: remora-strategy
description: >-
  REMORA v1.0.0 — Whale Single-Position Mirror. Rides a small, hand-picked set
  of whale traders: each tick it pulls each whale's open positions, takes their
  highest-conviction (largest-notional) position, and mirrors the strongest
  (asset, direction) candidate — boosting the score when multiple whales agree
  (consensus) and when a whale is ELITE/RELIABLE tier. Distinct from the broad
  trader-followers (Raptor/Jackal/Spider) that scan a leaderboard universe;
  Remora mirrors whales YOU choose. Let-winners-run DSL with a staleness cap.
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

# 🐟 REMORA v1.0.0 — Whale Single-Position Mirror

**Ride the whales you choose.** A remora fish attaches to a shark and goes where it goes. Remora attaches to a small, hand-picked set of whale traders — it finds each whale's biggest-conviction bet and mirrors the strongest one, with a consensus boost when several whales pile into the same trade.

## Why this strategy exists

The broad trader-followers (Raptor, Jackal, Spider) scan a *leaderboard universe* and synthesize a pick. That's powerful but opaque — you don't control whom you're following. Remora is the opposite: **you name the whales.** It mirrors their single highest-conviction position, so your exposure tracks specific traders you trust, and it leans hardest when those whales *agree*.

## CRITICAL RULES

### RULE 1: You pick the whales
Remora trades nothing until `config.whales` is set to a list of `trader_id`s / wallets. There is no default whale set — these are *your* choices.

### RULE 2: Highest conviction = largest notional
For each whale, Remora takes their single **largest-notional** open position (`size × entry`, falling back to margin), above `minNotionalUsd` (default $5,000, to skip dust). That's the whale's strongest live bet.

### RULE 3: Consensus is the edge multiplier
Top positions are aggregated into `(asset, direction)` candidates across whales. The more whales independently hold the same asset+direction, the higher the score (2 whales → +2, 3+ → +3). One whale can be wrong; three agreeing is a much stronger signal.

### RULE 4: Producer enters. DSL exits.
No `close_position` call site. Whales hold winners, so the DSL is the **let-winners-run** preset — wide ladder (T0 +10% / lock 0), `max_loss 18%`, time-cuts OFF except a **120h hard_timeout** staleness cap. ⚠️ Remora does **not yet mirror the whale's EXIT** — the hard_timeout prevents holding a stale mirror indefinitely. A whale-exit mirror is a planned v1.1 enhancement.

## How Remora scores a trade

**Gate:** at least one configured whale holds a qualifying top position (above `minNotionalUsd`).

**Score components** (max ~7):

| Signal | Points |
|---|---|
| A tracked whale's top conviction position | +3 |
| Consensus: 2 whales agree | +2 |
| Consensus: 3+ whales agree | +3 |
| Any agreeing whale is ELITE / RELIABLE tier | +1 |

**Floor:** `minScore: 4` (so a single whale's top position clears the floor only if it is ELITE-tier; otherwise consensus is required).

## DSL preset (let-winners-run — with a staleness cap)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 18% |
| Phase 1 | retrace_threshold | 10 |
| Time cuts | hard_timeout | **120h (staleness cap)** |
| Time cuts | weak_peak_cut | DISABLED |
| Time cuts | dead_weight_cut | DISABLED |
| Phase 2 | T0 → T4 | +10/0 · +20/45 · +35/65 · +55/78 · +90/88 |

## Scanner pattern

A focused variant of the **Trader-follower / hot-streak** archetype (#5) — see `senpi-trading-runtime/references/producer-patterns.md`. Primary MCP calls: `leaderboard_get_trader_positions` (per whale — the producer signature; unwraps the nested `data.positions.positions` shape), `discovery_get_trader_state` (whale quality tier, optional). The pure functions (`position_notional`, `mirror_direction`, `top_position`, `consensus_bonus`) are unit-tested in `tests/test_signal.py`.

## Operator install

See [README.md](README.md).

## Changelog

### v1.0.0 (2026-05-26) — initial release

A focused single-position mirror of a hand-picked whale set, with a consensus multiplier — a deliberate contrast to the universe-scanning trader-followers. Let-winners-run DSL class (wide ladder, time-cuts off except a 120h staleness cap), taker-true entry, no null numeric signal fields, defensive nested-shape unwrapping on `leaderboard_get_trader_positions`, and unit-tested pure functions. Whale-exit mirroring is a planned v1.1 enhancement.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version`. See `references/skill-attribution.md`.
