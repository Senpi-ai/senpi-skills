---
name: stag-strategy
description: >-
  STAG v1.0.0 — Parabolic-Run Hunter. The entry-side pair for the
  parabolic_runner DSL preset. Enters LONG only when ALL FIVE gates pass:
  structural trend (close > 200-bar 4h SMA + 7d high within 48h), 7d move
  ≥ 25% (the parabolic threshold), 1.5x volume surge, acceleration (4d ≥ 7d/2),
  AND Smart Money LONG ≥ 60%. Built for HYPE-class runs (+60% over 16 days)
  where standard DSL trails would chop on 5-8% intraday gyrations.
  Operator-driven: most ticks return empty by design.
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

# 🦌 STAG v1.0.0 — Parabolic-Run Hunter

**Ride the HYPE-class runs without getting chopped out on the gyrations.** Stag is the entry-side pair for the new `parabolic_runner` DSL preset — enter only when there's a real parabolic-runner setup, then let the widest DSL in the catalog hold the position through the 5–8% intraday pullbacks that would kill a normal trend trail.

## Why this strategy exists

Reference setup: HYPE 2026-05, $40 → $65 over 16 days. Through the middle of that run (May 17–25) there are at least 5 distinct 5–10% intraday gyrations on the 1h chart. Standard fleet DSL trails would chop a position out on any of them, missing the rest of the run. The problem isn't the DSL — it's that the DSL is making **local-volatility decisions** when the trade calls for **structural-trend decisions**.

Stag's answer is twofold:
1. **Strict 5-gate entry** so we only fire when there's actually a parabolic to ride (not a normal 5–10% trend).
2. **Ship paired with `parabolic_runner` DSL** — max_loss 25%, retrace 18, 2 consecutive breaches required, late first lock (+15%), light early tiers, 14d outer bound. Built specifically to accept the gyrations.

The trade-off is honest: this preset **bleeds in chop**. If the parabolic doesn't materialize, Stag gives back more than `let_winners_run` would. That's why the 5-gate filter is strict — the only way the wide DSL pays is if you've correctly identified the setup.

## CRITICAL RULES

### RULE 1: All five entry gates required
Each tick, for each whitelisted asset:

| Gate | Threshold | Default | Purpose |
|---|---|---|---|
| **Structural trend** | close > 200-bar 4h SMA AND 7d high made within last 48h | — | filter chop and reversals |
| **Strength** | 7d move ≥ `minTrendPct` | 25% | defines "parabolic" vs normal trend |
| **Volume** | mean(last 6 4h bars) / mean(last 42 4h bars) ≥ `volSurgeRatio` | 1.5× | real participation, not drift |
| **Acceleration** | 4d move ≥ 7d move ÷ 2 | — | recent half keeping pace or faster |
| **SM aligned LONG** | ≥ `smTiltMinPct` | 60% | not contrarian — Stag rides crowded longs |

If **any** gate fails, the asset is dropped. Most ticks return empty — that's the design.

### RULE 2: LONG only
Parabolic crashes happen too fast for momentum-style shorts. Stag is asymmetric.

### RULE 3: Operator-driven deployment
Stag is the tool you deploy *when you've already seen the setup forming.* Typical workflow:
1. Operator notices a parabolic setting up on one asset (e.g. HYPE +30% over 7d, volume surging).
2. Deploys Stag with that single asset: `whitelist: ["HYPE"]`, 25% margin, 4x leverage.
3. Stag fires when its gates trip — could be same hour, could be next day.
4. The wide DSL holds the position through the full run, including consolidations.
5. When the run finishes (T4 tier ratchets in, or `max_loss_pct: 25` trips on a real reversal), Stag closes.
6. Operator pauses Stag until the next regime.

Running Stag on a 4-asset basket as a passive scan also works, but the strict gates mean most ticks will be silent.

### RULE 4: Producer enters. DSL exits.
No `close_position` call site. The DSL is the entire exit logic — and the WIDE DSL is the whole point of Stag's existence.

## How Stag scores a trade

**Gate:** all five entry gates passed.

**Score components** (max ~7):

| Signal | Points |
|---|---|
| All 5 gates passed (base) | +3 |
| 7d trend ≥ `strongTrendPct` (default 40%) | +2 |
| Accelerating (gate already required, but adds to score) | +1 |
| Volume ratio ≥ 2.0× (vs the 1.5× gate threshold) | +1 |

**Floor:** `minScore: 5`.

## DSL preset (`parabolic_runner` — widest in the catalog)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | **25%** (vs 20 in `let_winners_run`) |
| Phase 1 | retrace_threshold | **18** (vs 12) |
| Phase 1 | consecutive_breaches_required | **2** (vs 1) |
| Time cuts | hard_timeout | **14d** outer bound |
| Time cuts | weak_peak_cut | DISABLED |
| Time cuts | dead_weight_cut | DISABLED |
| Phase 2 | T0 → T4 | +15/0 · +30/30 · +60/55 · +120/72 · +250/85 |

See [`senpi-trading-runtime/references/dsl-presets.yaml`](https://github.com/Senpi-ai/senpi-skills/blob/main/senpi-trading-runtime/references/dsl-presets.yaml) for the canonical preset.

## Scanner pattern

Archetype #4 (Multi-asset whitelist) with strict 5-gate parabolic filtering — see `senpi-trading-runtime/references/producer-patterns.md`. Primary MCP calls: `market_get_asset_data(candle_intervals=["4h"])` per asset (needs ≥ 200 candles for the SMA), `leaderboard_get_markets` (SM gate). The pure functions (`pct_change`, `sma`, `is_above_sma`, `recent_high_bars_ago`, `volume_surge`, `is_accelerating`, `parabolic_score`) are unit-tested in `tests/test_signal.py`.

## Operator install

See [README.md](README.md).

## Changelog

### v1.0.0 (2026-05-28) — initial release

First fleet agent paired with the new `parabolic_runner` DSL preset. Strict 5-gate filter (structural trend + 25% strength + 1.5× volume surge + acceleration + SM ≥60% LONG). LONG only. Aggressive risk tier with 1 max entry/day + 24h per-asset cooldown after a bad take. Taker-true entry, disown-safe launch, 18-test pure-function suite covering all gates.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version`. See `references/skill-attribution.md`.
