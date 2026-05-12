# 🌪️ Turbine v3.2 — Volume Rotation + Runners (two wallets)

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

**Run the volume play. Let winners run.**

ONE producer daemon manages TWO Senpi strategy wallets. Both wallets receive the SAME volume-rotation alpha (same scoring, same asset universe, same funding-fade direction). The DSL preset on each wallet picks the exit profile:

- **Volume wallet** ($4,000): hard_timeout 10min, no Phase 2 — pure rotation cadence.
- **Runners wallet** ($1,900): hard_timeout 240min (4h cap), Phase 2 ratchet enabled — let winners run.

Most positions on either wallet exit at small loss/win. ~5% of entries land on a real directional move and ratchet to apex on the runners wallet — that asymmetry is the alpha v3.0/v3.1 was leaving on the table by force-cutting at 10 min.

## v3.2 vs v3.1 (the redesign)

v3.1's "hunt mode" was a HYPE-only momentum specialist — wrong abstraction. With HYPE-only and 4h holds, hunt fired ~1-3 times per day, leaving the second wallet idle ~90% of the time and contributing zero volume. v3.2 fixes this by giving the runners wallet the SAME volume rotation as the volume wallet, just with a patient DSL profile.

| | v3.1 hunt | v3.2 runners |
|---|---|---|
| Asset universe | HYPE only | Same as volume (BTC/ETH/SOL/HYPE + xyz:BRENTOIL/GOLD/SPX) |
| Scoring | HYPE 4H breakout, score >= 10 floor | Volume rotation (same as volume wallet) |
| Trigger frequency | 1-3 entries/day | Constant rotation (same as volume) |
| Idle time | ~90% | <5% |
| Volume contribution | Negligible | Meaningful (smaller scale than volume wallet but always working) |
| DSL profile | Same as v3.2 | Same as v3.2 (4h cap, Phase 2 ratchet) |

## Mission targets

| Metric | v2.0.x | v3.2 |
|---|---|---|
| Daily volume | ~$2-3M | $5M+ |
| Net cost per $1M volume | $200 | <$100 |
| Total slots | 3 | 9 (7 vol + 2 runners) |
| Volume cycle | 15 min | 10 min |
| Total funding | $1,500 | **$5,900** ($4,000 vol + $1,900 runners) |

See [SKILL.md](SKILL.md) for the full architecture, scoring, DSL presets, and risk-gate breakdown.

---

## Sunset sequence (BEFORE deploying v3.2)

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
| `<volume-wallet>` | Volume rotation (fast DSL) | **$4,000** USDC on HL perps |
| `<runners-wallet>` | Volume rotation (patient DSL) | **$1,900** USDC on HL perps |

**Total: $5,900.** If you want a pure volume engine without runners, provision only the volume wallet and leave runners unset.

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

### Step 1 — Pull the helpers package (one-time per host)

```bash
mkdir -p /data/workspace/skills/_helpers/senpi_runtime_helpers
for f in __init__.py _config.py _logging.py cache.py client.py \
         daemon.py lock.py parallel.py SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/_helpers/senpi_runtime_helpers/$f" \
    -o "/data/workspace/skills/_helpers/senpi_runtime_helpers/$f"
done
```

Skip if already pulled for Cheetah v7.0.0 or another v3 skill.

### Step 2 — Pull Turbine v3.2

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
export TURBINE_VOLUME_DECISION_MODEL=gemini-3.1-pro-preview
export TURBINE_RUNNERS_DECISION_MODEL=gemini-3.1-pro-preview
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
| Volume | **$4,000** USDC on HL perps |
| Runners | **$1,900** USDC on HL perps |

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

The v3.2 producer is a long-lived daemon. **Do NOT add an openclaw cron entry.**

```bash
# Option A — supervised by tini:
exec tini -- python3 -u /data/workspace/skills/turbine-strategy/scripts/turbine-producer.py

# Option B — nohup:
nohup python3 -u /data/workspace/skills/turbine-strategy/scripts/turbine-producer.py \
  > /tmp/turbine-producer.log 2>&1 &
```

---

## Smoke test after deploy

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

## Verify both wallets are firing

```bash
tail -50 /tmp/turbine-producer.log | grep -v '"event"' | jq '
  .volume.wallet, .volume.account_value, .volume.slots_held, .volume.slots_effective,
  .runners.wallet, .runners.account_value, .runners.slots_held, .runners.slots_effective,
  .current_cycle_min'
```

Expected first 5 minutes:
- `volume.account_value` ≈ $4,000
- `volume.slots_held` climbing toward 7
- `volume.slots_effective` = 7 (auto-downsize hasn't kicked in yet)
- `runners.account_value` ≈ $1,900
- `runners.slots_held` climbing toward 2
- `runners.slots_effective` = 2
- `current_cycle_min == 10`

## Operating the volume wallet bleed

The volume wallet bleeds at the cost-of-volume rate. At mission target ($100/$1M × $5M/day = ~$500/day), the wallet drops below the 7-slot threshold ($3,500) within ~24 hours. The producer auto-downsizes gracefully:

| `volume.account_value` | `volume.slots_effective` |
|---|---|
| ≥ $3,500 | 7 |
| $3,000-$3,499 | 6 |
| $2,500-$2,999 | 5 |
| $2,000-$2,499 | 4 |

Top up the volume wallet daily-to-weekly to keep 7 slots active. Senpi-side rebates flow separately — they don't refund into the wallet.

## What NOT to do

- ❌ **Do NOT** add an openclaw cron — the daemon supervises itself
- ❌ **Do NOT** set `STRATEGY_ADDRESS`, `TURBINE_WALLET`, or `TURBINE_HUNT_WALLET` env vars — all banned
- ❌ **Do NOT** point both runtimes at the same wallet — the senpi-trading-runtime plugin rejects a second install on the same wallet
- ❌ **Do NOT** run Sentinel concurrently — runners mode supersedes it
- ❌ **Do NOT** delete a runtime while positions are open — orphan-position bug

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Producer exits with `TURBINE_VOLUME_WALLET not set` | Env var missing | Export `TURBINE_VOLUME_WALLET` (NOT `TURBINE_WALLET`) |
| Volume wallet's `slots_effective` drops over time | Cost-of-volume bleed (expected) | Top up volume wallet |
| `push_signal rejected ... NOT_FOUND` | Runtime not registered to that wallet | `openclaw senpi runtime list`; confirm scanner names match |
| `runtime for wallet X already running` on install | Both YAMLs pointing at same wallet | Confirm volume + runners env vars are different |
| Volume slots never fill past 3-4 | Spread gates too tight, fill rate low | Check `current_cycle_min`; check per-asset spread distribution |

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
