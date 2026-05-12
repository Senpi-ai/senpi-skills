---
name: spider-strategy
description: >-
  SPIDER v4.0.0 — Single-leg patient anchor sniper
  (senpi_runtime_helpers migration). Plumbing-only port from v3.0.2.
  NO thesis change. NO scoring change. NO threshold change. Producer
  ports onto `senpi_runtime_helpers` (in-process SenpiClient + direct
  HTTP POST to runtime /signals + producer_daemon long-lived loop).
  Holds one high-conviction long for 7+ days. Scored on arena leader
  alignment + SM consensus + funding favorability + 30d relative
  strength. LLM regime-aware gate vetoes during BTC catastrophe / vol
  expansion / funding flipping.
license: MIT
metadata:
  author: jason-goldberg
  version: "4.0.2"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🕷️ SPIDER v3.0 — Patient Anchor Sniper

The first Senpi agent designed for **multi-day holds with macro carry**.

## What v3.0 fixes from v2.0

v2.0 was an elaborate 2-leg portfolio operator (anchor + basket) that **never made a real trade**. Reason: the YAML referenced custom runtime types (`composite_score`, `portfolio_snapshot`, `predators_position_aggregator`, `LLM_PORTFOLIO_DECISION`) that the senpi-trading-runtime doesn't implement. The operator agent silently simulated execution in cron text prompts — the runtime ignored the unsupported types.

v3.0 trade-off: **preserve the patience thesis, drop the runtime requirements that aren't shipping**. Single-leg anchor only. Standard `external_scanner` producer + LLM gate + `OPEN_POSITION` action. Actually trades.

## Thesis

Hold one high-conviction long position for at least 7 days. Generate edge from:

1. **Multi-day trend** — top SM-leaderboard markets that are also held by arena top-10 traders tend to continue
2. **Positive carry** — prefer assets where funding rate favors longs (negative or low-positive funding = shorts paying longs)
3. **Relative strength** — assets outperforming over 30d tend to keep outperforming
4. **Fee-aware** — FEE_OPTIMIZED_LIMIT entries AND exits, 7-day hold = ~1 entry + 1 exit per trade = minimal fee drag

While 95% of the fleet churns daily on noise, Spider sits with a single position. **Patience is the edge.**

## Scoring (max ~10 pts, MIN_SCORE 7.0)

Composite score from 4 components, weights preserved from v2.0:

| Component | Weight | Max Pts | Source |
|---|---|---|---|
| Arena leaders LONG aligned | 0.40 | 4.0 | `arena_leaderboard` top 10 + `leaderboard_get_trader_positions` |
| SM consensus strength | 0.30 | 3.0 | `leaderboard_get_markets` (sm_pct, traders) |
| Funding favorability | 0.15 | 1.5 | `leaderboard_get_markets.funding_rate` |
| 30d relative strength | 0.15 | 1.5 | `leaderboard_get_markets.token_price_change_pct_30d` |

### Score-scaled leverage

| Score | Leverage |
|---|---|
| 9.0+ | 3x |
| 8.0-8.9 | 2x |
| 7.0-7.9 | 1x |

Leverage is clamped to each asset's Hyperliquid max via `strategy_get_asset_trading_limits`.

### Hard gates (reject before scoring)

- XYZ banned
- Non-LONG direction (Spider is long-only on anchor)
- SM consensus minimum: pct ≥ 8% AND traders ≥ 30
- Already held by Spider
- In post-close cooldown (7 days)
- In emit cooldown (7 days)

### LLM regime vetoes (override producer score)

The LLM gate evaluates conditions the producer doesn't see:
- BTC drawdown > 10% in 48h
- Volatility regime in expansion-spike mode
- Funding regime flipping (broad direction reversal)
- Signal asset down >15% in 7d AND >20% in 24h (caught a reversal, not continuation)

Any of these triggers `execute: false` even if score ≥ 7.0.

## DSL preset (v3.0 — patience-tuned)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 12% |
| Phase 1 | retrace_threshold | 8 |
| Phase 1 | consecutive_breaches | 3 |
| Phase 2 T0 | trigger 10% / lock 35% | wider than active agents |
| Phase 2 T1 | trigger 20% / lock 55% | |
| Phase 2 T2 | trigger 35% / lock 70% | |
| Phase 2 T3 | trigger 60% / lock 85% | |
| Phase 2 T4 | trigger 100% / lock 92% | apex |
| hard_timeout | 43200 min (30d) | enabled — ultimate fail-safe |
| weak_peak_cut | DISABLED | patience agent |
| dead_weight_cut | DISABLED | patience agent |

**No time-based cuts.** Spider's edge is holding. Phase 1 max_loss is the only early-exit path.

## Risk gates (`runtime.yaml` `risk.guard_rails`)

| Gate | Setting |
|---|---|
| daily_loss_limit_pct | 12% |
| max_entries_per_day | 2 |
| max_consecutive_losses | 3 |
| cooldown_minutes (post-loss) | 60 |
| drawdown_halt_pct | 25% |
| drawdown_reset_on_day_rollover | false (Roach lesson) |
| per_asset_cooldown_minutes | 10080 (7d, matches min-hold) |

## 7-day minimum hold enforcement

Three-layer:

1. **Producer-side gate** — when a position is open and held for less than 7 days, producer outputs `note: "riding anchor day X.Y/7 min hold"` and emits no signals
2. **Runtime per_asset_cooldown** — 10080 min (7d) after a close, runtime should reject re-entry on same asset
3. **DSL Phase 1 max_loss** — only early-exit path; weak_peak / dead_weight cuts disabled to prevent premature exits

After 7 days of holding, the position can stay open indefinitely until DSL Phase 2 trailing stops fire OR the 30-day hard_timeout activates.

## What v3.0 does NOT have (vs v2.0 design)

| Feature | v2.0 design | v3.0 reality |
|---|---|---|
| Basket of funding-driven shorts | 3-5 members, risk-parity sized | DROPPED — revisit when `dsl_portfolio` runtime ships |
| Risk-parity multi-leg sizing | yes | DROPPED |
| Funding harvest from coordinated shorts | yes | DROPPED (could partially recapture via funding-favorability scoring on anchor) |
| 7-day paper-trade warmup | required | DROPPED — gate-paused indefinitely; v3.0 trades immediately |
| Pilot protocol staged entry | 50/75/100% over 3 days | DROPPED — single full-size entry |
| Fleet concentration overlay | leverage modifier from peer-position aggregator | DROPPED — revisit |
| Forced-exit taxonomy | typed `exit_reason` per close | APPROXIMATED via DSL `closeReason` chain emission |

## Operator install

See [README.md](README.md) for fresh-install + migration commands from v2.0.

## Fleet patches incorporated

- ✓ **Held-asset dedup** (3-layer; Pangolin v2.1 pattern)
- ✓ **Post-close cooldown** (7d, matches min-hold; Pangolin v2.1.2 pattern)
- ✓ **Reentrancy lockfile** (fcntl)
- ✓ **Wallet-from-config** (no hardcoding)
- ✓ **drawdown_reset_on_day_rollover: false** (Roach lesson)
- ✓ **FEE_OPTIMIZED_LIMIT on entries AND exits**
- ✗ **FP-001 quiet hours** — deferred fleet-wide

## Hard rule for user-conversation Claude sessions

User-conversation Claude sessions MUST NOT call any of:
`create_position`, `close_position`, `edit_position`,
`ratchet_stop_add`, `ratchet_stop_edit`, `ratchet_stop_delete`,
`cancel_order`, `strategy_close`, `strategy_close_positions`.

These tools are reserved for the **producer cron** (entry path) and
the **DSL ratchet engine** (exit path). User-conversation sessions
are **read-only**. The producer cron will handle real signals on its next tick.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
