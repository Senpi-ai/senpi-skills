---
name: turbine-strategy
description: >-
  TURBINE v3.2 — Volume rotation on TWO wallets, ONE producer daemon.
  Both wallets receive the same volume-rotation alpha (same scoring,
  same asset universe, same funding-fade direction selection). The
  wallet boundary just selects DSL behavior: VOLUME wallet's runtime
  has hard_timeout 10min and no Phase 2 (pure rotation cadence);
  RUNNERS wallet's runtime has hard_timeout 240min and Phase 2 ratchet
  enabled (let winners run). Most positions exit at small loss/win
  on either wallet; ~5% land on a real directional move and ratchet
  to apex on the runners wallet — that asymmetry is the alpha v3.0/v3.1
  was leaving on the table by force-cutting at 10 min.
license: MIT
metadata:
  author: jason-goldberg
  version: "3.2.3"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime>=2.0.0
    - senpi-runtime-helpers
---

# 🌪️ TURBINE v3.2 — Volume Rotation + Runners

**Run the volume play. Let winners run.**

## What changed in v3.2.2 vs v3.2.1 (patch — 2026-05-09)

**Dex-aware slot keys.** `normalize_coin_key()` previously stripped the `xyz:` prefix from coin names, collapsing main and xyz versions of the same symbol into one held_keys entry. When a main position and an xyz resting order on the same symbol both existed (or vice versa), the producer undercounted slots → over-emitted signals → runtime rejected the surplus on margin. v3.2.2 preserves the prefix: `main:HYPE` and `xyz:HYPE` are now distinct keys.

Symptom in v3.2.1 (verified from operator's tick logs): `slots_held = 4` while `positions + resting = 5` on ticks where main and xyz versions of the same coin coexisted. Producer over-emitted by 1 on those ticks.

NO change to maker/taker placement. NO change to fee economics. NO change to YAMLs. One-time minor regression: 90s post-close cooldown skipped for the first 1-2 cycles after upgrade as `last_closed` repopulates with new-format keys. Trivial impact.

## What changed in v3.2.1 vs v3.2.0 (patch — 2026-05-09)

**Stale-order hygiene.** The producer now sweeps non-reduce-only ALO orders older than 300s before computing `held_keys`. Each runtime swap (helpers migration, runtime-phase-2 redeploy, daemon restart) leaves resting maker orders behind that the new runtime instance does not own; the producer's ghost-trade fix (v2.0.4) correctly counts those orphans as slot occupiers, which means orphans can starve real fills until cleared. The runtime's own `execution_timeout_seconds: 180` cancels orders it placed — anything still resting well past that is by definition abandoned.

NO change to maker/taker placement. NO change to fee economics. NO change to YAMLs. Cancellation is free; HL only charges on fills. Symptom in v3.2.0: volume wallet showed `slots_held = 6/7` while `clearinghouse` only reported 4 actual positions, throttling emissions to 1 signal/tick instead of 3-4.

Tick output now includes `volume.stale_swept[]` and `runners.stale_swept[]` arrays for telemetry — empty list = no orphans on this tick.

## What changed in v3.2 vs v3.1

v3.1 modeled the second wallet as a **HYPE-only momentum specialist**. Wrong abstraction:

- Hunt mode (HYPE 4H breakouts) fired ~1-3 times per day at most
- Hunt wallet's $2,400 sat idle ~90% of the time
- No volume contribution from the second wallet
- A single wallet can't hold the same HYPE position twice, so 2 hunt slots was effectively 1

v3.2 fixes the design intent. Both wallets run the **same volume rotation** (same scoring, same assets, same funding-fade direction). The DSL preset on each wallet picks the exit profile:

| | Volume wallet | Runners wallet |
|---|---|---|
| Entry signals | Volume rotation | **Volume rotation (same)** |
| Asset universe | BTC/ETH/SOL/HYPE + xyz:BRENTOIL/GOLD/SPX | **Same** |
| Direction | Funding fade | **Same** |
| Leverage | 5x | 5x |
| `hard_timeout` | **10 min** | 240 min (4h cap) |
| Phase 2 ratchet | DISABLED | **ENABLED** (5/0, 10/35, 20/55, 35/75, 50/85) |
| `weak_peak_cut` | DISABLED | 90 min @ peak < 3% |
| `dead_weight_cut` | DISABLED | 120 min |
| Phase 1 max_loss | 50% (catastrophic only) | 30% |

The same rotation alpha goes to both wallets. The runners wallet's DSL gives positions room to ratchet through tier-1/2/3/4 instead of being force-cut at 10 min. Most positions on the runners wallet still exit early (Phase 1 max_loss or weak_peak_cut). The ~5% that land on a real move are what justify the patient DSL.

## Why two wallets at all

The runtime-phase-2 plugin enforces **one runtime per wallet**. v3.0 attempted to attach two runtimes to a single wallet and got blocked at deploy. v3.1 split into two wallets but rebuilt them with the wrong thesis (HYPE specialist). v3.2 keeps the two-wallet split and rewires both to the right thesis (same volume rotation, different DSL).

## Mission

Hit **$5M/day in notional volume on Hyperliquid at <$100 net cost per $1M** while letting occasional winners ride for bigger ROE.

| Metric | v2.0.x baseline | v3.2 target |
|---|---|---|
| Daily volume | ~$2-3M | $5M+ |
| Net cost per $1M volume | $200 | <$100 |
| Total slots | 3 | 9 (7 vol + 2 runners) |
| Volume cycle | 15 min | 10 min (auto-fallback to 12 min) |
| Total funding | $1,500 | **$5,900** ($4,000 vol + $1,900 runners) |

## Architecture

```
              turbine-producer.py (long-lived daemon)
                     │
            ┌────────┴────────┐
            │                 │
     reads volume       reads runners
       wallet             wallet
            │                 │
            ▼                 ▼
     emits to:         emits to:
   turbine_volume     turbine_runners
     _signals          _signals
            │                 │
   turbine-volume-     turbine-runners-
        tracker           tracker
            │                 │
            ▼                 ▼
      VOLUME WALLET     RUNNERS WALLET
      ($4,000)          ($1,900)
```

ONE producer reads both wallets, emits the same kind of volume-rotation signals to each. Each runtime attaches to its OWN wallet (constraint satisfied) and applies its own DSL preset to positions it opened.

## Volume rotation (shared alpha on both wallets)

### Universe

| Pool | Assets | Weight |
|---|---|---|
| XYZ (deeper books, lower fee floor) | xyz:BRENTOIL, xyz:GOLD, xyz:SPX | **80%** |
| Main | BTC, ETH, SOL, HYPE | 20% |

### Direction — funding fade

```
LONG_CROWDED  → SHORT (collect funding)
SHORT_CROWDED → LONG  (collect funding)
NEUTRAL/FLAT  → coin-flip per cycle
```

### Spread gates

| DEX | Setting |
|---|---|
| main | 3 bps |
| xyz | 10 bps |

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

Real-world cost includes spread crossings, taker fallthrough, funding paid during 10-min holds. v3.2 targets **<$100/$1M actual**.

### Auto-downsize on tight margin

The volume wallet bleeds at the cost-of-volume rate. As `account_value` drops, fewer slots fit. Producer auto-downsizes:

```python
effective_slots = min(config_max, int(account_value / margin_per_slot))
```

At $4,000: 7 slots fit (start). At $3,200: 6 slots fit. At $2,500: 5 slots fit. Graceful degradation, no `insufficient_margin` rejections. Operator tops up the volume wallet daily-to-weekly to keep 7 slots active.

## Risk gates summary

| Gate | VOLUME runtime | RUNNERS runtime |
|---|---|---|
| `daily_loss_limit_pct` | 50% | 25% |
| `max_entries_per_day` | 1500 | 50 |
| `max_consecutive_losses` | 30 | 6 |
| `drawdown_halt_pct` | 50% | 25% |
| `drawdown_reset_on_day_rollover` | true | **false** (Roach lesson) |
| `per_asset_cooldown_minutes` | 0 | 10 |

## Operator config (turbine-config.json)

```json
{
  "volume": {
    "wallet": "0xVolumeWallet...",
    "strategyId": "volume-strategy-id"
  },
  "runners": {
    "wallet": "0xRunnersWallet...",
    "strategyId": "runners-strategy-id"
  },
  "chatId": "...",
  "slots":    { "volume": 7, "runners": 2 },
  "margin":   { "volume": 500, "runners": 950 },
  "leverage": { "volume": 5, "runners": 5 },
  "cycle":    {
    "volumeDefaultMin": 10,
    "volumeFallbackMin": 12,
    "fillRateFallbackThreshold": 0.85
  },
  "spread":   { "mainBps": 3, "xyzBps": 10 },
  "xyzWeight": 0.80
}
```

To run pure volume engine (no runners), leave `runners.wallet` and `runners.strategyId` empty strings, and don't export `TURBINE_RUNNERS_WALLET`.

## Required env vars

| Var | Purpose |
|---|---|
| `TURBINE_VOLUME_WALLET` | Volume strategy wallet (REQUIRED). |
| `TURBINE_RUNNERS_WALLET` | Runners strategy wallet (optional; omit to disable runners mode). |
| `SENPI_AUTH_TOKEN` | Bearer token for MCP + signal POST. |
| `TURBINE_VOLUME_DECISION_MODEL` | Bare LLM model name (no provider prefix) for volume gate. |
| `TURBINE_RUNNERS_DECISION_MODEL` | Bare LLM model name for runners gate. (Only used if runners wallet set.) |

**`STRATEGY_ADDRESS`, `TURBINE_WALLET`, and `TURBINE_HUNT_WALLET` are all BANNED.** STRATEGY_ADDRESS per the v2.0.9 contamination rule. TURBINE_WALLET was v3.0's single-wallet env var. TURBINE_HUNT_WALLET was v3.1's now-renamed runners var. Producer ignores all three.

## Hard rule for user-conversation Claude sessions

User-conversation Claude sessions MUST NOT call `create_position`, `close_position`, `edit_position`, `ratchet_stop_*`, `cancel_order`, `strategy_close*`. These tools are reserved for the **producer daemon** and the **DSL ratchet engine**.

## Fleet patches incorporated

- ✓ **senpi_runtime_helpers** (in-process MCP + signal POST)
- ✓ **producer_daemon scanner_lock** (PID-aliveness auto-recovery)
- ✓ **Per-wallet env vars** (TURBINE_VOLUME_WALLET / TURBINE_RUNNERS_WALLET; STRATEGY_ADDRESS + TURBINE_WALLET + TURBINE_HUNT_WALLET BANNED)
- ✓ **Wallet-from-config** (no hardcoding; senpi-skills is public)
- ✓ **drawdown_reset_on_day_rollover: false** on runners runtime (Roach lesson)
- ✓ **Wallet-isolated state dirs** (`state/<wallet-hash>/...` per wallet)
- ✓ **Auto-fallback cycle length** based on rolling maker fill rate
- ✓ **Auto-downsize on tight margin** — both wallets (graceful slot reduction as wallet bleeds)
- ✓ **`signal_type=` passed explicitly** per Rachin's review of Cheetah PR #209

## Sentinel sunset

Sentinel previously co-ran on the legacy Turbine wallet. Replaced by the runners wallet pattern in v3.1+; v3.2 refines the runners thesis to "volume rotation with patient DSL" instead of v3.1's flawed "HYPE momentum specialist."

## Operator install

See [README.md](README.md).

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
