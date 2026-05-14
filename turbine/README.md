# 🌪️ Turbine — Volume Engine + Runners

Run the volume play. Let winners run.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Turbine is the fleet's volume-engine / market-making specialist. ONE producer daemon manages TWO Senpi strategy wallets that both receive the SAME volume-rotation alpha — same scoring, same asset universe, same funding-fade direction — but each wallet runs a different DSL exit profile, so the same signal stream gets two different patience levels:

- **Volume wallet** ($5,400 — $4,900 active + $500 buffer): hard_timeout 10 min, no Phase 2 — pure rotation cadence, the cheapest possible way to print $3-4M/day of maker-first volume into HL.
- **Runners wallet** ($2,600 — full active, $0 buffer): hard_timeout 240 min (4h cap), Phase 2 ratchet enabled — let winners run.

Most positions on either wallet exit at small loss/win. ~5% of entries land on a real directional move and ratchet to apex on the runners wallet — that asymmetry is the alpha earlier versions were leaving on the table by force-cutting at 10 min. The economic mission is volume-cost minimization: builder-fee recycling means net wallet bleed ≈ mission cost rate (~$150/$1M target, empirically verified at $2M/day over 3 consecutive days), and the volume wallet is designed to be topped up daily as it auto-downsizes.

## Key parameters

| Parameter | Value |
|---|---|
| Asset universe | BTC / ETH / SOL / HYPE + xyz:BRENTOIL / GOLD / SPX (7 high-liquidity, tight-spread only) |
| Tick interval | Continuous (long-lived daemon; volume cycle 10 min) |
| Slots | 9 total (7 volume + 2 runners) |
| Volume wallet funding | $5,400 ($4,900 active + $500 buffer) |
| Runners wallet funding | $2,600 (full active, $0 buffer) |
| Volume margin/slot | $700 → $3,500 notional per trade at 5x leverage |
| Runners margin/slot | $1,300 → $6,500 notional per trade at 5x leverage |
| Volume DSL | hard_timeout 10 min, no Phase 2 |
| Runners DSL | hard_timeout 240 min (4h), Phase 2 ratchet enabled |
| Entry order type | FEE_OPTIMIZED_LIMIT |
| Exit order type | FEE_OPTIMIZED_LIMIT |
| Daily volume target | $3-4M/day (scaled from v3.2's $2M/day verified baseline) |
| Net cost target | ~$150 per $1M volume (empirically verified) |

See [SKILL.md](SKILL.md) for the full architecture, scoring, DSL presets, and risk-gate breakdown.

## Scanner pattern

This strategy uses the **volume-rotation / market-maker** scanner pattern — see `senpi-trading-runtime/references/producer-patterns.md` for the canonical reference. Primary MCP calls: high-frequency `cancel_order` + `create_position` driven by funding-fade scoring across the volume universe.

## Files

| File | Purpose |
|---|---|
| `runtime-volume.yaml` | Volume-wallet runtime spec |
| `runtime-runners.yaml` | Runners-wallet runtime spec |
| `scripts/turbine-producer.py` | Long-lived daemon (manages both wallets) |
| `scripts/turbine_config.py` | SDK probe + `SenpiClient` wrapper |
| `config/turbine-config.json` | Operator-tunable defaults (wallets, slots, margin, cycle, spread, xyzWeight) |
| `references/skill-attribution.md` | Attribution / provenance notes |

---

## Sunset sequence (BEFORE deploying)

If Turbine v2.0.x or Sentinel are still running, stop them first.

```bash
openclaw cron list | grep -E "turbine|sentinel"
openclaw senpi runtime list | grep -E "turbine|sentinel"

# Stop legacy crons
openclaw cron delete <turbine-v2-cron-id>
openclaw cron delete <sentinel-cron-id>     # if exists

# Wait for or close any open positions on the legacy wallet
mcp__senpi-prod__strategy_get_clearinghouse_state \
  --strategy_wallet <legacy-wallet>

# Delete the legacy runtime(s)
openclaw senpi runtime delete <turbine-tracker-id>
openclaw senpi runtime delete <sentinel-tracker-id>     # if exists
```

If you tried to deploy v3.0 or v3.1 partially, also clean up:

```bash
# v3.0/v3.1 left a partial volume runtime?
openclaw senpi runtime delete turbine-volume-tracker-XXXX
openclaw senpi runtime delete turbine-hunt-tracker-XXXX     # if exists
```

---

## Provision two strategy wallets

Create TWO new Senpi strategy wallets:

| Wallet | Purpose | Funding |
|---|---|---|
| `<volume-wallet>` | Volume rotation (fast DSL) | **$5,400** USDC on HL perps |
| `<runners-wallet>` | Volume rotation (patient DSL) | **$2,600** USDC on HL perps |

**Total: $8,000.** If you want a pure volume engine without runners, provision only the volume wallet and leave runners unset.

---

## Install

### Step 0 — Register the runtime plugin in `openclaw.json` (one-time per host)

The senpi-trading-runtime plugin won't bind its API port (`127.0.0.1:8787`) unless `plugins.entries.runtime` is present in `/data/.openclaw/openclaw.json`. Without that block the plugin logs `No plugin config found — skipping registration` and the producer daemon's `signal_post` calls fail with `[Errno 111] Connection refused`. Confirm or add:

```json
{
  "plugins": {
    "entries": {
      "runtime": {
        "enabled": true,
        "config": {
          "stateDir": "/data/.openclaw/senpi-state",
          "apiKey": "<your SENPI_AUTH_TOKEN>",
          "autoUpdate": { "enabled": false }
        }
      }
    }
  }
}
```

Restart the gateway after editing so the plugin re-registers:

```bash
openclaw gateway restart
sleep 10
curl -s -m 5 http://127.0.0.1:8787/state | head -c 200
# Expected: a JSON response with "success":true,"data":{"runtimes":[...]}
```

If `curl` returns Connection refused, the plugin still isn't registered — check `openclaw plugin list` shows the runtime entry as loaded and re-verify the JSON.

### Step 1 — Install the senpi-trading-runtime skill (one-time per host)

The Python Producer SDK (`senpi_runtime_helpers`) ships inside the senpi-trading-runtime skill. Install it once per host:

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

Skip if the senpi-trading-runtime skill is already installed on this host.

### Step 2 — Pull Turbine

```bash
mkdir -p /data/workspace/skills/turbine-strategy/{config,scripts,state,references}

for f in runtime-volume.yaml runtime-runners.yaml SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/turbine/$f" \
    -o "/data/workspace/skills/turbine-strategy/$f"
done
curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/turbine/config/turbine-config.json" \
  -o "/data/workspace/skills/turbine-strategy/config/turbine-config.json"
for f in turbine-producer.py turbine_config.py; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/turbine/scripts/$f" \
    -o "/data/workspace/skills/turbine-strategy/scripts/$f"
done
curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/turbine/references/skill-attribution.md" \
  -o "/data/workspace/skills/turbine-strategy/references/skill-attribution.md"

# Remove legacy v3.1 hunt yaml if it's still on disk:
rm -f /data/workspace/skills/turbine-strategy/runtime-hunt.yaml
```

### Step 3 — Configure

Edit `config/turbine-config.json`:

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
  "chatId": "your-telegram-chat-id"
}
```

Leave the other defaults (`slots`, `margin`, `cycle`, `spread`, `xyzWeight`) alone unless instructed.

### Step 4 — Required env vars

```bash
export TURBINE_VOLUME_WALLET=0xVolumeWallet
export TURBINE_RUNNERS_WALLET=0xRunnersWallet           # omit to disable runners
export SENPI_AUTH_TOKEN=...
export TURBINE_VOLUME_DECISION_MODEL=<your-preferred-model>
export TURBINE_RUNNERS_DECISION_MODEL=<your-preferred-model>
export TELEGRAM_CHAT_ID=<your-chat-id>

# Unset banned legacy vars
unset STRATEGY_ADDRESS
unset TURBINE_WALLET
unset TURBINE_HUNT_WALLET
unset TURBINE_HUNT_STRATEGY_ID
unset TURBINE_HUNT_DECISION_MODEL
```

### Step 5 — Fund both wallets

| Wallet | Amount |
|---|---|
| Volume | **$5,400** USDC on HL perps |
| Runners | **$2,600** USDC on HL perps |

### Step 6 — Install BOTH runtimes

```bash
# Volume runtime (attaches to volume wallet)
openclaw senpi runtime create \
  --path /data/workspace/skills/turbine-strategy/runtime-volume.yaml

# Runners runtime (attaches to runners wallet — different wallet)
openclaw senpi runtime create \
  --path /data/workspace/skills/turbine-strategy/runtime-runners.yaml

openclaw senpi runtime list   # confirm both ACTIVE
```

If you see `runtime for wallet X already running` on the second install, both YAMLs are pointing to the same wallet — confirm `${TURBINE_VOLUME_WALLET}` ≠ `${TURBINE_RUNNERS_WALLET}` and rerun env exports.

### Step 7 — Start the producer daemon

The producer is a long-lived daemon. **Do NOT add an openclaw cron entry.**

```bash
# Option A — supervised by tini:
exec tini -- python3 -u /data/workspace/skills/turbine-strategy/scripts/turbine-producer.py

# Option B — nohup:
nohup python3 -u /data/workspace/skills/turbine-strategy/scripts/turbine-producer.py \
  > /tmp/turbine-producer.log 2>&1 &
```

---

## Verification

```bash
tail -f /tmp/turbine-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -5
```

Every line should show `status=ok`.

| Status | Meaning |
|---|---|
| `ok` | Tick succeeded |
| `skipped_locked` | Lock collision (likely double-locking) |
| `error` | `fn` raised |
| `timeout` | `fn` took > 45s |

`daemon_self_terminated_no_runtime` is normal when the volume runtime is deleted.

### Verify both wallets are firing

```bash
tail -50 /tmp/turbine-producer.log | grep -v '"event"' | jq '
  .volume.wallet, .volume.account_value, .volume.slots_held, .volume.slots_effective,
  .runners.wallet, .runners.account_value, .runners.slots_held, .runners.slots_effective,
  .current_cycle_min'
```

Expected first 5 minutes:
- `volume.account_value` ≈ $5,400
- `volume.slots_held` climbing toward 7
- `volume.slots_effective` = 7 (auto-downsize hasn't kicked in yet)
- `runners.account_value` ≈ $2,600
- `runners.slots_held` climbing toward 2
- `runners.slots_effective` = 2
- `current_cycle_min == 10`

## Operating the volume wallet bleed

The volume wallet bleeds at the cost-of-volume rate. At mission target (~$150/$1M × $3-4M/day = ~$450-$600/day), the wallet drops below the 7-slot threshold within ~24 hours. The producer auto-downsizes gracefully:

| `volume.account_value` | `volume.slots_effective` |
|---|---|
| ≥ $4,900 | 7 |
| $4,200-$4,899 | 6 |
| $3,500-$4,199 | 5 |
| $2,800-$3,499 | 4 |
| < $2,800 | 3 or fewer |

Top up the volume wallet daily to keep 7 slots active. Senpi-side rebates flow separately — they don't refund into the wallet.

## What NOT to do

- **Do NOT** add an openclaw cron — the daemon supervises itself
- **Do NOT** set `STRATEGY_ADDRESS`, `TURBINE_WALLET`, or `TURBINE_HUNT_WALLET` env vars — all banned
- **Do NOT** point both runtimes at the same wallet — the senpi-trading-runtime plugin will reject the second install
- **Do NOT** run Sentinel concurrently — runners mode supersedes it
- **Do NOT** delete a runtime while positions are open — orphan-position bug

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Producer exits with `TURBINE_VOLUME_WALLET not set` | Env var missing | Export `TURBINE_VOLUME_WALLET` (NOT `TURBINE_WALLET`) |
| Volume wallet's `slots_effective` drops over time | Cost-of-volume bleed (expected) | Top up volume wallet |
| **`slots_effective` collapses from 7 to 1-2 in hours, not days** | **DSL exit engine is down — positions miss their 10-min hard cuts, available margin drains** | **Verify both runtimes are healthy: `curl -s http://127.0.0.1:8787/state`. If a runtime is unregistered or dead, positions stack up and starve slot capacity. Re-register the runtime BEFORE topping up the wallet.** |
| Stale ALO orders persist after restart | Orphaned limit orders from prior daemon — the v3.2.1 sweep clears them but only on next tick | After ANY runtime swap / daemon restart, run `strategy_get_open_orders` on both wallets; cancel any non-reduce-only ALO orders older than 10 min before relaunching the daemon |
| `push_signal rejected ... NOT_FOUND` | Runtime not registered to that wallet | `openclaw senpi runtime list`; confirm scanner names match |
| `runtime for wallet X already running` on install | Both YAMLs pointing at same wallet | Confirm volume + runners env vars are different |
| Volume slots never fill past 3-4 | Spread gates too tight, fill rate low | Check `current_cycle_min`; check per-asset spread distribution |

### Restart procedure (always do this on daemon restart)

Operator learning from v3.2 prod operation: any runtime swap or daemon restart can leave behind orphaned resting ALO orders that the new daemon doesn't own but that count against held-slot accounting. Always run this BEFORE relaunching the daemon:

```bash
# 1. Verify both runtimes are alive and registered
curl -s http://127.0.0.1:8787/state | jq '.data.runtimes[].name'

# 2. Cancel any stale (>10min) ALO orders on both wallets
#    Use strategy_get_open_orders → cancel_order on each non-reduce-only entry
#    older than 10 min on both volume and runners wallets

# 3. Confirm clean state
#    strategy_get_open_orders should show only fresh orders (<10min)
#    or empty arrays after step 2

# 4. NOW launch the daemon
bash run-producer.sh
```

## Changelog

- **v3.3** — Budget upgrade for $3-4M/day volume target. Volume wallet $4,000 → $5,400 (margin/slot $500 → $700). Runners wallet $1,900 → $2,600 (margin/slot $950 → $1,300). Total $8,000. Maintains the verified <$150/$1M cost efficiency by doubling notional size on the same 7-asset universe instead of expanding into lower-tier coins. Auto-downsize thresholds updated. Two new troubleshooting entries documenting v3.2 prod learnings: DSL-exit-down → slot starvation, and orphaned ALO order restart hygiene.
- **v3.2** — Runners wallet redesigned: gets the SAME volume-rotation alpha as the volume wallet, just with a patient DSL profile (4h cap, Phase 2 ratchet). Replaces v3.1's HYPE-only hunt specialist, which fired only 1-3 times/day and left the second wallet idle ~90% of the time. Mission targets: $2M/day volume verified at <$150/$1M cost over 3 consecutive days, 9 total slots (7 volume + 2 runners), 10-min volume cycle, $5,900 total funding.
- **v3.1** — Added "hunt" mode on a second wallet (HYPE-only 4H breakout, score ≥ 10). Deprecated in v3.2.
- **v3.0** — Two-wallet architecture introduced.
- **v2.0.x** — Single-wallet volume engine; ~$2-3M/day, $200 per $1M cost.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
