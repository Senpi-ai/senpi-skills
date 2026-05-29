---
name: koala-strategy
description: >-
  KOALA v1.0.0 — Set-and-Forget Trail HODL. The simplest possible Senpi
  agent. Pick an asset (default BTC). Fire LONG once on deploy. Hold with an
  ultra-wide DSL trail (max_loss 30%, retrace 25, 90d hard_timeout). Done.
  No scoring, no scheduling, no multi-timeframe analysis. For users whose
  entire trading thesis is "I want to own BTC and have a safety net."
  Onboarding tier.
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

# 🐨 KOALA v1.0.0 — Set-and-Forget Trail HODL

**The simplest possible Senpi agent.** Pick an asset. Fire LONG once. Hold. Forever-ish.

## Why this strategy exists

Tortoise is close to "buy and hold" but it has a cadence — every 24h it might buy more. Beaver/Heron/Hummingbird are trend followers — they read the market and decide. Hedgehog holds a basket — multiple decisions. None of them give the user the answer to *"I just want to own BTC and have a safety net, and I don't want anything else."*

Koala is that answer. **One asset, one entry, one stop.**

## CRITICAL RULES

### RULE 1: One asset, configured by the operator
Default is BTC. Operators set `asset: "ETH"` or `asset: "SOL"` if they want something else. There's no whitelist scan — Koala doesn't pick.

### RULE 2: LONG only, fixed sizing
Margin 50% of equity (default). Leverage 2x (default, max 3x). No scoring, no conviction tiers — *the choice to deploy Koala IS the bet*.

### RULE 3: Fire-once mode is the default
Koala persists a `koala-state.json` file that records `first_entry_at`. In **fire-once mode** (the default), Koala fires exactly one entry per lifetime — even if the DSL eventually exits, the producer stays silent forever after.

Operators who want a cycling deploy (buy / DSL-exits / buy again after a cooldown) can set `fireOnceMode: false` and a `reEntryCooldownHours` (default 7 days). Koala then re-enters once the configured cooldown has elapsed since the last detected exit.

### RULE 4: Ultra-wide DSL is the entire exit logic
The widest DSL in the catalog:

| Component | Setting |
|---|---|
| `max_loss_pct` | **30%** (vs `let_winners_run`'s 20, `parabolic_runner`'s 25) |
| `retrace_threshold` | **25** (vs `parabolic_runner`'s 18) |
| `consecutive_breaches_required` | **3** (vs 1-2 elsewhere) |
| `hard_timeout` | **90d** (3 months) |
| `weak_peak_cut` | DISABLED |
| `dead_weight_cut` | DISABLED |
| Phase 2 ladder | +20/0 → +50/30 → +100/50 → +200/70 → +500/85 |

This is deliberately wider than `parabolic_runner` because Koala's job is to *not* cut on normal corrections. It only releases on catastrophic reversal or after 3 months.

### RULE 5: Producer NEVER closes
DSL owns exits. Always. If the DSL trail trips, the position closes, and (in fire-once mode) Koala never re-enters. In re-entry mode, Koala waits for the cooldown.

## How Koala works internally

1. **Tick 1** (30 min after deploy): `koala-state.json` doesn't exist. `should_enter` returns True. Fires LONG. Writes `first_entry_at = now`. Records entry.
2. **Subsequent ticks while holding**: `koala-state.json` has `first_entry_at`. The asset is currently in `held_assets`. Producer outputs `HOLDING` and does nothing.
3. **If the DSL eventually exits** (say 60 days in): Position disappears from `held_assets`. The producer's next tick detects the change, records `last_exit_at` in state.
4. **After exit, fire-once mode**: `should_enter` returns False (because `first_entry_at` is set). Producer outputs `WAITING — fire-once mode AND already entered once`. Stays silent forever.
5. **After exit, re-entry mode**: `should_enter` returns False until `reEntryCooldownHours` elapses, then returns True on the next tick.

## Scanner pattern

A **state-trigger variant** of archetype #2 (Single-asset alpha hunter) — see `senpi-trading-runtime/references/producer-patterns.md`. Unlike Beaver/Heron/Hummingbird/Wolverine which score price action per tick, Koala's "scanner" is a state-file check. No `market_get_asset_data` call. The pure functions (`should_enter`, `record_entry`, `record_exit`) are unit-tested in `tests/test_signal.py`.

## Operator install

See [README.md](README.md).

## Changelog

### v1.0.0 (2026-05-29) — initial release

First fleet agent with **no price-side scoring at all**. State-file-driven single-shot entry, ultra-wide custom DSL (max_loss 30%, retrace 25, 90d hard_timeout, super-late Phase 2 ladder), fire-once mode by default. Maker-preferred entry (no urgency). Disown-safe launch, 8/8 unit tests covering both fire-once and re-entry-cooldown branches.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version`. See `references/skill-attribution.md`.
