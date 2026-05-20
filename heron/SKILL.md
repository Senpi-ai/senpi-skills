---
name: heron-strategy
description: >-
  HERON v1.0.0 — Onboarding-tier ETH trend follower. Long OR short
  based on the confluence of 4h trend structure + Senpi Smart-Money
  leaderboard direction. Single asset (ETH), simple 5-component
  scoring (max ~9), wide Bison-pattern DSL Phase 2 ladder so winning
  trends can run multi-day. Designed for first-time Senpi users.
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

# 🐦 HERON v1.0.0 — ETH Trend Follower (SM-confirmed)

**Onboarding tier. ETH only. Long OR short.** When ETH is in a 4h trend AND Senpi's Smart-Money leaderboard agrees with that direction, Heron opens a single conviction position and lets the DSL ratchet manage it.

## Why this strategy exists

Heron is the **default first-trade strategy** for new Senpi users:

- **One asset** — ETH. No multi-asset whitelist to tune, no XYZ universe to filter, no funding regime to interpret.
- **One question** — "Is ETH trending?" Heron answers by looking at the 4h candle structure (higher-lows = bullish, lower-highs = bearish).
- **One direction gate** — "Does Smart Money agree?" Heron pulls Senpi's leaderboard concentration and requires top-trader positioning to align with the trend before firing.
- **One exit mechanism** — DSL. Phase 1 max_loss 20% floor + Phase 2 wide ratchet ladder. Time-cuts all disabled — winning trends ride.

If both gates pass and the score clears the floor, Heron emits ONE signal per qualifying setup. Max 2 entries per day. 4h cooldown per asset.

## CRITICAL RULES

### RULE 1: Producer enters. DSL exits. Producer NEVER closes.
The producer has no `close_position` call site. All exits flow through the DSL Phase 1 max_loss / Phase 2 ratchet retrace. This is the same structural guarantee as Bison v3.0.1 and Wolverine v6.0.0.

### RULE 2: SM direction gate is hard, not soft
If `leaderboard_get_markets` returns NEUTRAL or returns a direction OPPOSITE to the 4h trend, the producer outputs `WAITING — no qualifying setup` and does nothing. There is no override.

### RULE 3: Producer emits signals via push_signal(); runtime opens
The runtime's LLM-gated `heron_entry` action receives the signal, validates it against the schema, and opens via FEE_OPTIMIZED_LIMIT with `ensure_execution_as_taker: false` (ALO patience on entry). Exit orders use `ensure_execution_as_taker: true` so closes always happen.

### RULE 4: Race-window dedup is mandatory
The producer writes to `state/recent-signals.json` after every successful `push_signal()` and checks the cache BEFORE building a thesis. 240s TTL covers the gap between push_signal returning OK and the resulting position appearing in the next-tick `clearinghouseState`. This is the Bison v3.0.1 fix applied at construction-time.

### RULE 5: Onboarding defaults — don't tune them aggressively
The default config is:
- `minScore: 5` (out of ~9 max)
- `smTiltMinPct: 60`
- `smStrongTiltPct: 70`
- 25% margin / 5x leverage
- 2 entries/day, 4h cooldown

These are tuned for **new users learning the platform**, not for aggressive alpha extraction. Don't push minScore higher (it starves the strategy); don't push leverage higher than 5x (operator capital risk).

## How Heron scores a trade

**Direction gate** (all required):
1. 4h trend ≠ NEUTRAL (higher-lows ≥ 60% bullish OR lower-highs ≥ 60% bearish)
2. SM direction agrees with 4h trend
3. SM tilt ≥ smTiltMinPct (default 60%)

**Score components** (max ~9):

| Signal | Points |
|---|---|
| 4h trend aligned with direction | +3 |
| 1h trend confirms 4h | +2 |
| SM aligned (gate-confirmed) | +2 |
| SM strongly tilted (≥ smStrongTiltPct) | +1 |
| Volume rising (>10% lookback delta) | +1 |

**Floor:** `minScore: 5`. Typical entry clears 5-7. Strong setups clear 7-8. Apex (rare) clears 9.

## DSL preset (Bison-pattern wide)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 20% |
| Phase 1 | retrace_threshold | 8% |
| Phase 1 | consecutive_breaches | 1 |
| Time cuts | hard_timeout | **DISABLED** |
| Time cuts | weak_peak_cut | **DISABLED** |
| Time cuts | dead_weight_cut | **DISABLED** |
| Phase 2 | T0 | +10% / lock 0 |
| Phase 2 | T1 | +20% / lock 25% |
| Phase 2 | T2 | +30% / lock 40% |
| Phase 2 | T3 | +50% / lock 60% |
| Phase 2 | T4 | +75% / lock 75% |
| Phase 2 | T5 | +100% / lock 85% (apex) |

The T0 lock at 0% is the key shift — a +10% mover can retrace fully without locking, giving real trends room to develop. This is the same ladder that produced Bison's SOL SHORT +71.1% closed win.

## Risk guardrails (`runtime.yaml` `risk.guard_rails`)

| Gate | Setting | Why |
|---|---|---|
| max_entries_per_day | 2 | Onboarding: fewer-stronger |
| per_asset_cooldown_minutes | 240 | 4h between ETH attempts |
| daily_loss_limit_pct | 15 | Cap a bad day |
| max_consecutive_losses | 3 | Halt on losing streak |
| cooldown_minutes (post-loss) | 60 | Cool off after a stop |
| drawdown_halt_pct | 25 | Catastrophic-floor circuit breaker |
| drawdown_reset_on_day_rollover | false | No grace at midnight |

## Hardcoded constants (not configurable)

- `ASSET`: "ETH" (Heron/Hummingbird are separate skills with ETH/HYPE)
- `MAX_LEVERAGE`: 5
- `RECENT_SIGNAL_TTL_SEC`: 240

## Hard rule for user-conversation Claude sessions

User-conversation Claude sessions MUST NOT call:
`create_position`, `close_position`, `edit_position`,
`ratchet_stop_add`, `ratchet_stop_edit`, `ratchet_stop_delete`,
`cancel_order`, `strategy_close`, `strategy_close_positions`.

These tools are reserved for the **producer daemon** (entry path) and the **DSL ratchet engine** (exit path). User-conversation sessions are **read-only**.

## Scanner pattern

This strategy uses the **Single-asset trend follower (Kodiak family)** scanner pattern with an added Smart-Money direction gate — see `senpi-trading-runtime/references/producer-patterns.md` (Pattern 1) for the canonical reference. Primary MCP calls: `market_get_asset_data` (ETH candles), `leaderboard_get_markets` (SM direction).

## Operator install

See [README.md](README.md) for fresh-install + env-var setup.

## Changelog

### v1.0.0 (2026-05-20) — initial release

First skill in the onboarding-tier trio (Heron = ETH, Heron = ETH, Hummingbird = HYPE). Shares scaffold + DSL preset; differs only in the `asset` config field. Designed as the default "I'm new to Senpi, where do I start?" answer.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).

## Skill Attribution

When creating a strategy from this skill, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.
