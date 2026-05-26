---
name: meerkat-strategy
description: >-
  MEERKAT v1.0.0 — Momentum-Event Sniper. Watches the Senpi momentum-event feed
  (leaderboard_get_momentum_events — the 4h rolling-window momentum / rank-jump
  events) and snipes the freshest, highest-tier events the moment they fire,
  entering in the momentum direction before the move is broadly known. Tier
  (event magnitude) + freshness gate the entry; SM alignment + rising volume
  are confirmation bonuses. Distinct from the Striker / rank-jump agents
  (Jaguar/Orca/Roach) which score a leaderboard universe. Let-winners-run DSL
  with a short hard_timeout.
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

# 🦦 MEERKAT v1.0.0 — Momentum-Event Sniper

**Stand sentry, pop the instant something moves.** A meerkat watches the horizon and reacts the moment it spots motion. Meerkat watches the Senpi momentum-event feed and snipes the freshest, highest-tier momentum events the instant they fire — entering in the move's direction before it's broadly known.

## Why this strategy exists

The momentum-event feed (`leaderboard_get_momentum_events`) surfaces assets that just made a sharp move in the 4h rolling window — the earliest, cleanest read on *fresh* momentum. The edge decays fast: an event that's already 30+ minutes old is half-known. Meerkat's whole job is **speed + selectivity** — only the strongest tiers, only while they're fresh.

## CRITICAL RULES

### RULE 1: Tier is the backbone
Each event's `|momentum|` is classified into a tier: **3** (strongest, `>= tier3MinPct`, default 10%), **2** (`>= tier2MinPct`, default 5%), else **1**. Entry requires `tier >= minTier` (default 2; set to 3 for a pure tier-3 sniper). Weak events never fire.

### RULE 2: Freshness is the sniper edge
Entry requires the event to be fresh — `age <= maxEventAgeMinutes` (default 30). An event with no timestamp is treated as fresh (the feed only returns current-window events). Stale momentum is somebody else's trade.

### RULE 3: Confirmation is a bonus, not a gate
SM alignment (top-trader tilt on the same side) and rising 1h volume each add to the score but are **not required** — momentum events fire faster than SM data updates, so Meerkat won't wait for them.

### RULE 4: Producer enters. DSL exits.
No `close_position` call site. A fresh momentum burst can extend, so the DSL is the **let-winners-run** preset — wide ladder (T0 +10% / lock 0), `max_loss 18%`. But momentum is time-bounded: a **short 36h hard_timeout** cuts a burst that hasn't paid out (a stalled momentum trade is a failed one). No `weak_peak_cut`.

## How Meerkat scores a trade

**Gate:** `tier >= minTier` AND fresh.

**Score components** (max ~7):

| Signal | Points |
|---|---|
| Tier (1 / 2 / 3) | +1 / +2 / +3 |
| Fresh (age ≤ max) | +2 |
| Smart Money confirms direction (≥ `smTiltMinPct`) | +1 |
| SM strongly tilted (≥ `smStrongTiltPct`) | +1 |
| 1h volume rising (> 15%) | +1 |

**Floor:** `minScore: 4` (so a tier-2 fresh event clears; a tier-1 event never does).

## DSL preset (let-winners-run — momentum-tuned)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 18% |
| Phase 1 | retrace_threshold | 10 |
| Time cuts | hard_timeout | **36h (short — momentum is time-bounded)** |
| Time cuts | weak_peak_cut | DISABLED |
| Time cuts | dead_weight_cut | DISABLED |
| Phase 2 | T0 → T4 | +10/0 · +20/45 · +35/65 · +55/78 · +90/88 |

## Scanner pattern

A focused, event-driven variant of the **Striker / rank-jump detector** archetype (#6) — see `senpi-trading-runtime/references/producer-patterns.md`. Where the strikers (Jaguar/Orca/Roach) score a universe of leaderboard rank-jumps, Meerkat reads the momentum-event feed directly. Primary MCP calls: `leaderboard_get_momentum_events` (the producer signature; defensive multi-key unwrap), `leaderboard_get_markets` (SM), `market_get_asset_data` (volume confirmation). The pure functions (`event_age_minutes`, `event_direction`, `momentum_tier`, `event_score`) are unit-tested in `tests/test_signal.py`.

## Operator install

See [README.md](README.md).

## Changelog

### v1.0.0 (2026-05-26) — initial release

First fleet agent driven directly off the momentum-event feed — a tier + freshness sniper, contrasting the universe-scanning rank-jump strikers. Let-winners-run DSL class (wide ladder, short 36h hard_timeout since momentum is time-bounded), taker-true entry, no null numeric signal fields, defensive multi-key shape unwrapping on `leaderboard_get_momentum_events`, and unit-tested pure functions (including epoch-seconds-vs-milliseconds handling).

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version`. See `references/skill-attribution.md`.
