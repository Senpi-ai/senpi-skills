---
name: turbine-strategy
description: >-
  TURBINE v3.0 — two-mode signal emitter. One producer + two runtimes
  on a single wallet. VOLUME (7 slots × 10-min funding-fade rotation,
  XYZ-weighted 80/20) is a builder-fee-recycling volume engine on
  Hyperliquid: pure breakeven trading P&L target, alpha is the net
  spread between Senpi's builder-fee recycling (~3.5 bps RT) and HL
  maker fees (~1.2-1.4 bps RT main, ~0.6 bps XYZ). HUNT (2 slots ×
  HYPE 4H momentum, ratchet exit) rides directional moves with
  score-gated entries (>=10 floor on multi-axis confluence). Single
  long-lived producer_daemon + senpi_runtime_helpers in-process
  wrapper (no mcporter / openclaw subprocess). Sentinel sunset —
  hunt slots take over with explicit slot accounting.
license: MIT
metadata:
  author: jason-goldberg
  version: "3.0.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime>=2.0.0
    - senpi-runtime-helpers
---

# 🌪️ TURBINE v3.0 — Volume Engine + HYPE Hunt

**The bot prints volume. Senpi prints rebates. Hunt slots scoop alpha when HYPE breaks.**

## Mission

Hit **$5M/day in notional volume on Hyperliquid at <$100 net cost per $1M** while running 2 additional slots that take directional HYPE 4H momentum trades for upside.

| Metric | v2.0.x baseline | v3.0 target |
|---|---|---|
| Daily volume | ~$2-3M | $5M |
| Net cost per $1M volume | $200 | <$100 |
| Total slots | 3 (volume only) | 9 (7 volume + 2 hunt) |
| Volume cycle | 15 min | 10 min (auto-fallback to 12 min) |
| Funding | $1,500 | $6,000 |

## Architecture

**Single producer, two runtimes, one wallet.**

```
                 turbine-producer.py (long-lived daemon)
                       │
            ┌──────────┴──────────┐
            │                     │
   turbine_volume_signals    turbine_hunt_signals
            │                     │
   turbine-volume-tracker    turbine-hunt-tracker
   (runtime-volume.yaml)    (runtime-hunt.yaml)
            │                     │
            └──────────┬──────────┘
                       │
                  Strategy wallet
```

The producer manages slot accounting (which positions are VOLUME vs HUNT), enforces post-close cooldowns, and emits to the appropriate scanner. Each runtime's DSL only manages positions it opened.

## VOLUME mode — the volume engine

### Universe (tightened from v2.0.x)

| Pool | Assets | Weight |
|---|---|---|
| XYZ (deeper books, lower fee floor) | xyz:BRENTOIL, xyz:GOLD, xyz:SPX | **80%** |
| Main | BTC, ETH, SOL, HYPE | 20% |

Dropped from v2.0.x: xyz:TSLA, xyz:NVDA — wider spreads off-hours; maker fill rate is the constraint, not asset diversity.

### Direction — funding fade (preserved from v2.0.x)

```
LONG_CROWDED  → SHORT (collect funding)
SHORT_CROWDED → LONG  (collect funding)
NEUTRAL/FLAT  → alternate vs last_direction for this asset
```

### Spread gates (tightened)

| DEX | v2.0.x | v3.0 |
|---|---|---|
| main | 5 bps | **3 bps** |
| xyz | 15 bps | **10 bps** |

### Cycle length — 10 min default with auto-fallback

```
10 min default
  ↓
If realized maker fill rate (last 20 entries) < 85%:
  ↓
Fall back to 12 min until rate recovers
```

State tracked in `state/<wallet-hash>/cycle-stats.json`. Operator overrides via `cycle.*` keys in `turbine-config.json`.

### Volume cost math (target)

```
Theoretical (perfect maker on both legs):
  +3.5 bps  builder fee recycling (RT)
  −1.4 bps  HL main maker RT          → +2.1 bps net
  −0.6 bps  HL XYZ maker RT           → +2.9 bps net

Weighted (80% XYZ / 20% main): +2.7 bps net positive theoretical
```

Real-world cost includes spread crossings, taker fallthrough, and funding paid during 10-min holds. v3.0 targets **<$100/$1M actual** via tighter spread gates + 80/20 XYZ + maker-only ALO.

### Volume DSL preset (`runtime-volume.yaml`)

| Component | Setting | Rationale |
|---|---|---|
| `hard_timeout` | 10 min | Drives rotation cadence |
| `weak_peak_cut` | DISABLED | Time = volume; no early cuts |
| `dead_weight_cut` | DISABLED | Same reason |
| `phase1.max_loss_pct` | 50% | Catastrophic backstop only (50% margin ROE = 10% price at 5x) |
| `phase2` | DISABLED | Volume rotation doesn't chase peak ROE |
| Entry + exit order type | FEE_OPTIMIZED_LIMIT | Maker-only |
| `ensure_execution_as_taker` | **false** | The strategy IS maker fills — taker fallback would invert the alpha |

## HUNT mode — HYPE 4H momentum

### Why HYPE only

- Wolverine v3.0 was specced for HYPE but never shipped — fills the gap
- HYPE is a clean trend asset, not covered by any other live fleet agent
- Distinct asset from VOLUME mode = unambiguous P&L attribution
- 4-hour timescale distinct from VOLUME's 10-min = no thesis collision

### Scoring (max ~15, floor 10)

| Component | Points | Trigger |
|---|---|---|
| 4H trend structure | +4 | 3+ HH/HL closes vs prior 4 candles |
| 4H price move | +3 | ≥2% in trend direction |
| 1H momentum aligned | +2 | 1H direction matches 4H direction |
| Volume rising | +2 | Latest 4H ≥ 1.5× prior 5-candle average |
| Funding regime | +2 | NEUTRAL or against direction (fade-bonus); **−1 fighting crowd** |
| Spread depth | +2 | ≤3 bps |

Floor 10/15 means 4-5 components must fire — meaningful conviction, not first-bar-crossing.

### Hunt DSL preset (`runtime-hunt.yaml`)

| Component | Setting |
|---|---|
| `hard_timeout` | 4 h (240 min) |
| `weak_peak_cut` | 90 min @ peak < 3% |
| `dead_weight_cut` | 120 min |
| `phase1.max_loss_pct` | 30% (6% price move at 5x) |
| `phase2 tiers` | 5/0, 10/35, 20/55, 35/75, 50/85 |

### Hunt safety floor

- Per-asset cooldown: 60 min post-exit (producer mirrors runtime gate)
- Account-equity floor: hunt slots blocked when account_value < $5,500 (preserves volume capital)
- Daily entry cap: 6 (runtime guard rail)

## Risk gates summary

| Gate | VOLUME runtime | HUNT runtime |
|---|---|---|
| `daily_loss_limit_pct` | 50% | 25% |
| `max_entries_per_day` | 1500 | 6 |
| `max_consecutive_losses` | 30 | 3 |
| `drawdown_halt_pct` | 50% | 25% |
| `drawdown_reset_on_day_rollover` | true | **false** (Roach lesson) |
| `per_asset_cooldown_minutes` | 0 | 60 |

## Operator config (turbine-config.json)

```json
{
  "wallet": "0x...",
  "strategyId": "...",
  "chatId": "...",
  "slots":    { "volume": 7, "hunt": 2 },
  "margin":   { "volume": 500, "hunt": 1250 },
  "leverage": { "volume": 5, "hunt": 5 },
  "cycle":    {
    "volumeDefaultMin": 10,
    "volumeFallbackMin": 12,
    "fillRateFallbackThreshold": 0.85,
    "huntMin": 240,
    "huntCooldownMin": 60
  },
  "spread":   { "mainBps": 3, "xyzBps": 10 },
  "xyzWeight": 0.80,
  "huntMinScore": 10,
  "minAccountValueForHunt": 5500.0
}
```

## Required env vars

| Var | Purpose |
|---|---|
| `TURBINE_WALLET` | Strategy wallet (must match BOTH runtime YAMLs). **STRATEGY_ADDRESS is BANNED** per v2.0.9 contamination rule. |
| `SENPI_AUTH_TOKEN` | Bearer token for MCP + signal POST. |
| `TURBINE_VOLUME_DECISION_MODEL` | Bare LLM model name (no provider prefix) for volume gate. |
| `TURBINE_HUNT_DECISION_MODEL` | Bare LLM model name for hunt gate. (Can be same as volume.) |

## Hard rule for user-conversation Claude sessions

User-conversation Claude sessions MUST NOT call `create_position`, `close_position`, `edit_position`, `ratchet_stop_*`, `cancel_order`, `strategy_close*`. These tools are reserved for the **producer daemon** and the **DSL ratchet engine**. User-conversation sessions are read-only.

## Fleet patches incorporated

- ✓ **senpi_runtime_helpers** (in-process MCP + signal POST)
- ✓ **producer_daemon scanner_lock** (PID-aliveness auto-recovery)
- ✓ **TURBINE_WALLET only** (no STRATEGY_ADDRESS fallback per v2.0.9 rule)
- ✓ **Wallet-from-config** (no hardcoding; senpi-skills is public)
- ✓ **drawdown_reset_on_day_rollover: false** on hunt runtime (Roach lesson)
- ✓ **Slot-mode tracker** in `state/<wallet-hash>/slot-mode.json` — explicit per-position mode tagging
- ✓ **Auto-fallback cycle length** based on rolling maker fill rate
- ✓ **Account-equity hunt floor** ($5,500 default — preserves volume capital after drawdown)

## Sentinel sunset

Sentinel previously ran on the same Turbine wallet, allocating $200 per slot for "convergence-based momentum trades using leftover margin." The blended-PnL problem made it impossible to attribute performance. v3.0's HUNT mode replaces it with explicit slot accounting and clean per-mode telemetry via `audit_query`.

Sentinel should be paused or retired before Turbine v3.0 deploys.

## Operator install

See [README.md](README.md) for fresh-install + sunset-sequence commands.

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
