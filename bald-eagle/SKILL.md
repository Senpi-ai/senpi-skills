---
name: bald-eagle-strategy
description: >-
  BALD EAGLE v5.0 — XYZ Contrarian Fader (senpi_runtime_helpers). Counter-
  trades SM consensus on 6 high-liquidity XYZ macro assets (CL, BRENTOIL,
  GOLD, SILVER, SP500, XYZ100) when SM concentration is high AND the 4h
  move is exhausting. Plumbing-only migration from v4.1: producer pushes
  signals via SenpiClient.push_signal, runtime owns execution + daily caps
  + cooldowns + drawdown halt + DSL exits. Thesis preserved verbatim.
license: MIT
metadata:
  author: jason-goldberg
  version: "5.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime>=1.1.0
    - senpi_runtime_helpers
---

# BALD EAGLE v5.0.0 — XYZ Contrarian Fader

## v5.0.0 (2026-05-12) — plumbing-only migration

NO thesis change. v4.1 scoring, 6-asset universe, conviction-scaled
leverage (5x/7x), XYZ-tuned wide DSL, contrarian flip, spread gate,
600s stale-order auto-purge — all preserved verbatim. Migration:
mcporter → SenpiClient, scanner-side `create_position` → producer
`push_signal()` with LLM-gated runtime action, Python state files
for daily cap → `runtime.yaml risk.guard_rails`, MARKET exits →
FEE_OPTIMIZED_LIMIT, openclaw cron → `producer_daemon` (5min).

Bald Eagle is the only Senpi agent trading commodities, indices, and
equities on Hyperliquid. It fades retail/SM consensus on macro assets
when the move is exhausting — when oil is up 2%+ over 4h and SM is
piled in long, Bald Eagle shorts the exhaustion.

## Thesis

XYZ macro assets (oil, brent, gold, silver, SP500, XYZ100) on
Hyperliquid attract directional momentum traders who load up after a
clean 1-3% intraday move. By the time SM concentration crosses 10-20%
on a 4h-extended move, the trade is almost always exhausted —
mean reversion is the positive-edge play.

Bald Eagle scans the 6 deepest XYZ markets every 5 minutes. When SM
is concentrated (≥3%, ≥5 traders), and the 4h move is in the SM
direction, and contribution velocity confirms SM is still piling in,
Bald Eagle pushes a signal in the OPPOSITE direction. Wide DSL
(360-480min hard timeout, 12% retrace) gives commodities time to
mean-revert.

## Tracked assets

| Token | Surface | Why |
|---|---|---|
| CL | xyz | Crude oil — deepest SM signal (181 traders) |
| BRENTOIL | xyz | Brent crude — 166 traders |
| GOLD | xyz | 26 traders, slow but real |
| SILVER | xyz | 42 traders |
| SP500 | xyz | S&P 500 — 35 traders |
| XYZ100 | xyz | Nasdaq-100 — 50 traders |

XYZ macro assets trade 24/7 on Hyperliquid (no market-hours gating).
Oil is especially active during geopolitical risk windows.

## Pipeline

```
1. fetch SM data from leaderboard_get_markets (xyz dex only)
2. for each token in TRACKED_XYZ:
   - skip if held / on cooldown
   - score using all contributors (SM concentration, trader depth,
     contribution velocity 1h/4h, 4h price alignment, 4h move
     exhaustion, 1h momentum, 4h trend structure, volume trend,
     funding alignment)
   - if score >= 8, check spread (hard gate, <0.1%)
   - if pass: flip direction (contrarian)
3. push best candidate via SenpiClient.push_signal
4. runtime LLM gate (EAGLE_DECISION_MODEL) honors signal, opens
   FEE_OPTIMIZED_LIMIT MAKER position with conviction-tiered leverage
5. DSL manages exit (RatchetStop tiers + Phase 1 retrace 12%)
```

## Scoring (v4.1 preserved verbatim)

| Signal | Points |
|---|---|
| SM concentration ≥20% | +4 |
| SM concentration ≥10% | +3 |
| SM concentration ≥5% | +2 |
| SM concentration ≥3% | +1 |
| Trader depth ≥100 | +2 |
| Trader depth ≥30 | +1 |
| Contribution surge 1h (>5%) | +2 |
| Contribution rising 1h (>2%) | +1 |
| Contribution sustained 4h | +1 |
| 4H price aligned (≥0.5% SM direction) | +2 |
| 4H price positive in SM direction | +1 |
| 4H price opposing | -1 |
| DEEP_EXHAUSTION (4h ≥2% in SM direction) | +2 |
| EXHAUSTION (4h ≥1% in SM direction) | +1 |
| 4H trend structure aligned | +2 |
| 4H trend opposing | -1 |
| 1H momentum aligned | +1 |
| Volume rising | +1 |
| Funding aligned | +1 |

**MIN_SCORE 8.** Max plausible ~20. The spread gate (<0.1%) remains
the only hard gate — it's an execution-quality filter, not thesis.

## Leverage (conviction-scaled, XYZ caps)

| Score | Leverage |
|---|---|
| 8-9 | 5x |
| 10+ | 7x |

XYZ caps lower than crypto (no 10x). Macro reversals need room to
develop — high leverage punishes the wait.

## DSL — XYZ-tuned wide timings (preserved from v4.1)

| Setting | Value |
|---|---|
| `hard_timeout` | 480 min (8h) |
| `weak_peak_cut` | 240 min (4h), min 3.0% |
| `dead_weight_cut` | 120 min (2h) |
| `phase1.max_loss_pct` | 25% |
| `phase1.retrace_threshold` | 12% |
| `phase1.consecutive_breaches_required` | 1 (v2 single-breach) |

### Phase 2 RatchetStop ladder

| Trigger | Lock |
|---|---|
| +5% ROE | 0% |
| +10% | 20% |
| +20% | 35% |
| +30% | 50% |
| +50% | 70% |
| +75% | 85% |

Exits use FEE_OPTIMIZED_LIMIT with `ensure_execution_as_taker: true`
(60s) — exits must fill. Entries use MAKER (contrarian fades can wait).

## Key parameters

| Parameter | Value |
|---|---|
| Universe | 6 XYZ macro assets |
| Slots | 2 |
| Margin per slot | $250 |
| Margin per trade | 40% of equity |
| MIN_SCORE | 8 |
| Leverage | 5x / 7x (conviction-scaled) |
| Spread gate | <0.1% |
| Max entries/day | 4 |
| Daily loss limit | 10% |
| Per-asset cooldown | 360 min (6h) |
| Drawdown halt | 25% |
| Tick interval | 5 min |
| Entry order type | FEE_OPTIMIZED_LIMIT (MAKER) |
| Exit order type | FEE_OPTIMIZED_LIMIT (taker, 60s) |

## v4.1 hardening preserved

- `has_resting_orders()` with 600s stale-cancel auto-purge on both
  default + xyz dex (reduce-only orders skipped; those are DSL legs)
- `dex="xyz"` propagated through every market_get_asset_data call
- XYZ-prefixed asset names ("xyz:CL") in coin field

## Files

| File | Purpose |
|---|---|
| `scripts/eagle-producer.py` | v5.0 helpers-native producer (5min daemon) |
| `scripts/eagle_config.py` | SenpiClient wrapper + state helpers |
| `config/bald-eagle-config.json` | Wallet, strategyId, chatId |
| `runtime.yaml` | Plugin runtime (position tracker + eagle_signals + eagle_entry + DSL exit) |

## Critical agent rules

1. **Install path** is `/data/workspace/skills/bald-eagle-strategy/`
2. **The producer DOES NOT enter positions** — it pushes signals;
   runtime owns execution
3. **The producer DOES NOT exit positions** — DSL handles every exit
4. **Thesis is FADING, not following** — Bald Eagle trades OPPOSITE
   to SM consensus by design
5. **Spread gate (<0.1%) is the only producer-side hard gate**
6. **6-asset universe is fixed** — adding tokens without verifying
   SM depth (≥5 traders) is a thesis change
7. **Verify runtime on every session start**

## Changelog

### v5.0.0 (2026-05-12) — helpers-native migration
- Producer replaces scanner; `producer_daemon` replaces openclaw cron
- `SenpiClient.push_signal()` replaces inline `create_position`
- Runtime owns daily cap, drawdown halt, cooldowns (`risk.guard_rails`)
- Exits flipped MARKET → FEE_OPTIMIZED_LIMIT (taker, 60s)
- v2 DSL single-breach (`consecutive_breaches_required: 1`)
- fcntl reentrancy guard dropped (producer_daemon owns lock)
- Thesis, scoring, 6-asset universe, leverage tiers, DSL timings,
  spread gate, has_resting_orders: PRESERVED VERBATIM

### v4.1 (2026-05-05)
- Added `has_resting_orders()` 600s stale-cancel guard

### v4.0 (2026-04-10)
- CONTRARIAN FLIP — fade SM consensus on XYZ macro assets
- Move-exhaustion bonus tuned for XYZ (1-2% bands)
- MIN_SCORE 8, leverage capped 7x

### v3.0 (2026-04-06)
- Focused 6-asset universe, conviction-scaled leverage, wide DSL

---

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in
the call. See `references/skill-attribution.md` for details.
