---
name: turbine-strategy
description: >-
  TURBINE v3.1 — two-wallet, two-runtime, single-producer architecture.
  ONE producer daemon manages BOTH wallets. The wallet boundary IS the
  mode boundary, which gives clean per-mode P&L attribution at the
  account level (no slot-mode tagging needed). VOLUME wallet (7 slots
  × 10-min funding-fade rotation, XYZ-weighted 80/20) is a builder-fee-
  recycling volume engine — pure breakeven trading P&L target, alpha
  is the spread between Senpi's builder-fee recycling (~3.5 bps RT)
  and HL maker fees (~1.2-1.4 bps RT main, ~0.6 bps XYZ). HUNT wallet
  (2 slots × HYPE 4H momentum, ratchet exit) rides directional moves
  with score-gated entries (>=10 floor on multi-axis confluence).
  Hunt is optional — leave TURBINE_HUNT_WALLET unset to run a pure
  volume engine. Sentinel sunset.
license: MIT
metadata:
  author: jason-goldberg
  version: "3.1.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime>=2.0.0
    - senpi-runtime-helpers
---

# 🌪️ TURBINE v3.1 — Volume Engine + HYPE Hunt (two wallets)

**The bot prints volume. Senpi prints rebates. Hunt slots scoop alpha when HYPE breaks. Two wallets keep the books clean.**

## Why two wallets

The runtime-phase-2 plugin enforces **one runtime per wallet**. v3.0 attempted to attach `turbine-volume-tracker` AND `turbine-hunt-tracker` to a single wallet and got blocked at deploy. v3.1 splits cleanly:

| Wallet | Runtime | Funding | Slots |
|---|---|---|---|
| Volume | `turbine-volume-tracker` | $3,500 | 7 × $500 |
| Hunt | `turbine-hunt-tracker` | $2,400 | 2 × $1,200 |
| **Total** | | **$5,900** | 9 |

The wallet boundary is the mode boundary. `audit_query` filters per-wallet without needing a `signalType` join. HL margin is wallet-isolated, so volume side can't bleed into hunt and vice-versa — that's a feature, not a bug.

## Mission

Hit **$5M/day in notional volume on Hyperliquid at <$100 net cost per $1M** while running 2 dedicated slots that take directional HYPE 4H momentum trades for upside.

| Metric | v2.0.x baseline | v3.1 target |
|---|---|---|
| Daily volume | ~$2-3M | $5M |
| Net cost per $1M volume | $200 | <$100 |
| Total slots | 3 | 9 (7 vol + 2 hunt) |
| Volume cycle | 15 min | 10 min (auto-fallback to 12 min) |
| Total funding | $1,500 | $5,900 |

## Architecture

```
              turbine-producer.py (long-lived daemon)
                     │
            ┌────────┴────────┐
            │                 │
     reads volume       reads hunt
       wallet            wallet
            │                 │
            ▼                 ▼
     emits to:         emits to:
   turbine_volume     turbine_hunt
     _signals          _signals
            │                 │
   turbine-volume-     turbine-hunt-
        tracker           tracker
            │                 │
            ▼                 ▼
      VOLUME WALLET     HUNT WALLET
      ($3,500)          ($2,400)
```

ONE producer, TWO `cfg.get_*_wallet_and_strategy()` calls per tick, TWO independent slot accountings. Each wallet has its own runtime; each runtime's DSL only manages its own positions.

## VOLUME mode

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

| DEX | v2.0.x | v3.1 |
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

State tracked in `state/<volume-wallet-hash>/cycle-stats.json`. Operator overrides via `cycle.*` keys in `turbine-config.json`.

### Volume cost math

```
Theoretical (perfect maker on both legs):
  +3.5 bps  builder fee recycling (RT)
  −1.4 bps  HL main maker RT          → +2.1 bps net
  −0.6 bps  HL XYZ maker RT           → +2.9 bps net

Weighted (80% XYZ / 20% main): +2.7 bps net positive theoretical
```

Real-world cost includes spread crossings, taker fallthrough, and funding paid during 10-min holds. v3.1 targets **<$100/$1M actual** via tighter spread gates + 80/20 XYZ + maker-only ALO.

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

## HUNT mode

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

### Hunt safety

- **Per-asset cooldown:** 60 min post-exit (producer mirrors runtime gate)
- **Hunt wallet balance floor:** $2,000 default. Producer skips hunt emission if hunt wallet's own balance falls below this. Volume capital is naturally protected by the wallet boundary — no need for the cross-mode equity floor v3.0 had.
- **Daily entry cap:** 6 (runtime guard rail)

## Hunt is optional

If `TURBINE_HUNT_WALLET` is unset, hunt mode is disabled gracefully:

- Producer only queries volume wallet
- Producer only emits volume signals
- Operator runs a pure volume engine with $5,900 in one wallet (or whatever they fund volume with)

This is the simplest deploy path for operators who only want the volume engine.

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
  "volume": {
    "wallet": "0xVolumeWallet...",
    "strategyId": "volume-strategy-id"
  },
  "hunt": {
    "wallet": "0xHuntWallet...",
    "strategyId": "hunt-strategy-id"
  },
  "chatId": "...",
  "slots":    { "volume": 7, "hunt": 2 },
  "margin":   { "volume": 500, "hunt": 1200 },
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
  "minHuntWalletBalance": 2000.0
}
```

To run pure volume engine (no hunt), leave `hunt.wallet` and `hunt.strategyId` empty strings, and don't export `TURBINE_HUNT_WALLET`.

## Required env vars

| Var | Purpose |
|---|---|
| `TURBINE_VOLUME_WALLET` | Volume strategy wallet (REQUIRED). |
| `TURBINE_HUNT_WALLET` | Hunt strategy wallet (optional; omit to disable hunt mode). |
| `SENPI_AUTH_TOKEN` | Bearer token for MCP + signal POST. |
| `TURBINE_VOLUME_DECISION_MODEL` | Bare LLM model name (no provider prefix) for volume gate. |
| `TURBINE_HUNT_DECISION_MODEL` | Bare LLM model name for hunt gate. (Only used if hunt wallet set.) |

**`STRATEGY_ADDRESS` and `TURBINE_WALLET` are BANNED.** STRATEGY_ADDRESS per the v2.0.9 contamination rule (a generic env var is a fleet-wide vector). TURBINE_WALLET was v3.0's single-wallet env var; v3.1 splits it explicitly. If you have either set from older testing, unset.

## Hard rule for user-conversation Claude sessions

User-conversation Claude sessions MUST NOT call `create_position`, `close_position`, `edit_position`, `ratchet_stop_*`, `cancel_order`, `strategy_close*`. These tools are reserved for the **producer daemon** and the **DSL ratchet engine**. User-conversation sessions are read-only.

## Fleet patches incorporated

- ✓ **senpi_runtime_helpers** (in-process MCP + signal POST)
- ✓ **producer_daemon scanner_lock** (PID-aliveness auto-recovery)
- ✓ **Per-wallet env vars** (TURBINE_VOLUME_WALLET / TURBINE_HUNT_WALLET; STRATEGY_ADDRESS + TURBINE_WALLET BANNED per v2.0.9 rule)
- ✓ **Wallet-from-config** (no hardcoding; senpi-skills is public)
- ✓ **drawdown_reset_on_day_rollover: false** on hunt runtime (Roach lesson)
- ✓ **Wallet-isolated state dirs** (`state/<wallet-hash>/...` per wallet)
- ✓ **Auto-fallback cycle length** based on rolling maker fill rate (volume side)
- ✓ **Hunt wallet balance floor** (pauses hunt emission if hunt wallet draws down)
- ✓ **`signal_type=` passed explicitly** per Rachin's review of Cheetah PR #209

## Sentinel sunset

Sentinel previously co-ran on the legacy Turbine wallet, allocating $200 per slot for "convergence-based momentum trades using leftover margin." The blended-PnL problem made attribution impossible. v3.1's HUNT mode with its own wallet replaces it cleanly.

Sentinel should be paused or retired before Turbine v3.1 deploys.

## Operator install

See [README.md](README.md) for fresh-install + sunset-sequence commands.

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
