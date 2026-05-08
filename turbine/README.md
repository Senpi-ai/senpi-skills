# 🌪️ Turbine v3.1 — Volume Engine + HYPE Hunt (two wallets)

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

**ONE producer daemon manages TWO Senpi strategy wallets, each with its own runtime.** This is required because the runtime-phase-2 plugin enforces one runtime per wallet — v3.0's "two runtimes on one wallet" architecture got blocked at deploy and was rewritten as v3.1.

The wallet boundary IS the mode boundary. Volume wallet runs the rotation engine; hunt wallet rides HYPE 4H momentum. Single producer reads both, emits to both. `audit_query` filters per-wallet for clean P&L attribution.

## What changed in v3.1 (from v3.0)

- **Two strategy wallets** instead of one — runtime-phase-2 plugin enforces one runtime per wallet
- **Funding split** — Volume wallet $3,500 (7 × $500) + Hunt wallet $2,400 (2 × $1,200) = $5,900 total
- **Env var schema** — `TURBINE_VOLUME_WALLET` + `TURBINE_HUNT_WALLET` (split from v3.0's single `TURBINE_WALLET`)
- **Config schema** — `volume.{wallet,strategyId}` + `hunt.{wallet,strategyId}` (was flat `wallet`/`strategyId`)
- **Slot-mode tracker dropped** — wallet boundary makes it redundant
- **Hunt is optional** — leave `TURBINE_HUNT_WALLET` unset to run a pure volume engine

## Mission targets

| Metric | v2.0.x | v3.1 |
|---|---|---|
| Daily volume | ~$2-3M | $5M |
| Net cost per $1M volume | $200 | <$100 |
| Total slots | 3 | 9 (7 vol + 2 hunt) |
| Volume cycle | 15 min | 10 min |
| Total funding | $1,500 | **$5,900** ($3,500 vol + $2,400 hunt) |

See [SKILL.md](SKILL.md) for the full thesis, scoring tables, DSL presets, and risk-gate breakdown.

---

## Sunset sequence (BEFORE deploying v3.1)

If Turbine v2.0.x or Sentinel are still running on the legacy wallet, stop them first.

```bash
# 1. Confirm what's running
openclaw cron list | grep -E "turbine|sentinel"
openclaw senpi runtime list | grep -E "turbine|sentinel"

# 2. Stop legacy producers/scanners
openclaw cron delete <turbine-v2-cron-id>
openclaw cron delete <sentinel-cron-id>     # if exists

# 3. Wait for or close any open positions on the legacy wallet
mcp__senpi-prod__strategy_get_clearinghouse_state \
  --strategy_wallet <legacy-wallet>

# 4. Delete the legacy runtime(s)
openclaw senpi runtime delete <turbine-tracker-id>
openclaw senpi runtime delete <sentinel-tracker-id>     # if exists

# 5. Withdraw funds (or leave; v3.1 uses TWO new wallets)
```

---

## Provision two strategy wallets

Create TWO new Senpi strategy wallets:

| Wallet | Purpose | Funding |
|---|---|---|
| `<volume-wallet>` | Volume engine runtime | $3,500 USDC on HL perps |
| `<hunt-wallet>` | HYPE momentum runtime | $2,400 USDC on HL perps |

If you want a pure volume engine without hunt mode, provision only the volume wallet and leave hunt unset.

---

## Install

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

### Step 2 — Pull Turbine v3.1

```bash
mkdir -p /data/workspace/skills/turbine-strategy/{config,scripts,state,references}

for f in runtime-volume.yaml runtime-hunt.yaml SKILL.md README.md; do
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
```

### Step 3 — Configure

Edit `config/turbine-config.json`:

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
  "chatId": "your-telegram-chat-id"
}
```

Leave the other defaults (`slots`, `margin`, `cycle`, `spread`, etc.) alone unless instructed.

### Step 4 — Required env vars

```bash
export TURBINE_VOLUME_WALLET=0xVolumeWallet
export TURBINE_HUNT_WALLET=0xHuntWallet                # omit to disable hunt
export SENPI_AUTH_TOKEN=...
export TURBINE_VOLUME_DECISION_MODEL=gemini-3.1-pro-preview
export TURBINE_HUNT_DECISION_MODEL=gemini-3.1-pro-preview

# OpenClaw runtime substitution
export TELEGRAM_CHAT_ID=<your-chat-id>
```

❌ Do NOT set `STRATEGY_ADDRESS` (banned per v2.0.9) or `TURBINE_WALLET` (legacy v3.0 var; v3.1 ignores it).

### Step 5 — Fund both wallets

| Wallet | Amount |
|---|---|
| Volume | $3,500 USDC on HL perps |
| Hunt | $2,400 USDC on HL perps |

### Step 6 — Install BOTH runtimes

Each runtime points to its own wallet via `${TURBINE_VOLUME_WALLET}` / `${TURBINE_HUNT_WALLET}`:

```bash
# Volume runtime
openclaw senpi runtime create \
  --path /data/workspace/skills/turbine-strategy/runtime-volume.yaml

# Hunt runtime (skip if running pure volume engine)
openclaw senpi runtime create \
  --path /data/workspace/skills/turbine-strategy/runtime-hunt.yaml

openclaw senpi runtime list   # confirm both ACTIVE
```

Each runtime attaches to its OWN wallet, so the runtime-phase-2 "one runtime per wallet" rule is satisfied.

### Step 7 — Start the producer daemon

The v3.1 producer is a long-lived daemon. **Do NOT add an openclaw cron entry.**

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

`daemon_self_terminated_no_runtime` is normal when the volume runtime is deleted (the daemon's alive_check tracks the volume runtime; hunt can be deleted independently without stopping the daemon).

## Verify both wallets are firing

```bash
tail -50 /tmp/turbine-producer.log | grep -v '"event"' | jq '
  .volume_wallet, .volume_account_value, .slots,
  .hunt_enabled, .hunt_wallet, .hunt_account_value,
  .volume_emitted, .hunt_emitted, .current_cycle_min'
```

Expected first 5 minutes:
- `volume_account_value` ≈ $3,500
- `hunt_account_value` ≈ $2,400 (or `null` if hunt disabled)
- `slots.volume.held` climbing toward 7
- `slots.hunt.held` typically 0 (HYPE 4H breakouts are rare)
- `volume_emitted` populating with funding-fade signals
- `hunt_emitted` typically empty
- `current_cycle_min == 10`

## What NOT to do

- ❌ **Do NOT** add an openclaw cron — the daemon supervises itself
- ❌ **Do NOT** set `STRATEGY_ADDRESS` or `TURBINE_WALLET` env vars — both banned
- ❌ **Do NOT** point both runtimes at the same wallet — runtime-phase-2 will reject the second install
- ❌ **Do NOT** run Sentinel concurrently — hunt mode supersedes it
- ❌ **Do NOT** lower `huntMinScore` below 10 — admits noise
- ❌ **Do NOT** delete a runtime while positions are open — orphan-position bug

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Producer exits with `TURBINE_VOLUME_WALLET not set` | Env var missing | Export `TURBINE_VOLUME_WALLET` (NOT `TURBINE_WALLET`) |
| Hunt slots never fire | `TURBINE_HUNT_WALLET` not set OR score floor 10 is selective | Check daemon's per-tick `hunt_enabled` field; check `hunt_skipped_reason` |
| `volume push_signal rejected ... NOT_FOUND` | Volume runtime not registered to the volume wallet | `openclaw senpi runtime list`; confirm `external_scanner.name: turbine_volume_signals` matches producer |
| `runtime for wallet X already running` on install | Volume + hunt runtime YAMLs both pointing at the same wallet | Confirm `${TURBINE_VOLUME_WALLET}` ≠ `${TURBINE_HUNT_WALLET}`; rerun env exports |
| Volume slots never fill past 3-4 | Spread gates too tight, fill rate low | Check `current_cycle_min`; check per-asset spread distribution |
| Account drops below `minHuntWalletBalance` | Hunt wallet ate a drawdown | Hunt auto-pauses; volume continues. Top up hunt wallet OR lower `minHuntWalletBalance`. |

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
