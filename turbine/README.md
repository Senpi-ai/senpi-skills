# 🌪️ Turbine v3.0 — Volume Engine + HYPE Hunt

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

**Single producer, two runtimes, one wallet.** Combines a 7-slot volume engine (10-min rotation, funding-fade direction, 80% XYZ weighted for the lower fee floor) with a 2-slot HYPE 4H momentum hunt (Phase 2 ratchet exit). Targets **$5M/day volume at <$100 net cost per $1M** while opportunistically scooping HYPE breakouts.

This is a **complete rewrite from v2.0.x** — not a migration. The rebuild lands on a fresh strategy wallet with clean state. Legacy Turbine MUST be paused before v3.0 deploys.

## What changed in v3.0

- **Two runtimes on one wallet** (`turbine-volume-tracker` + `turbine-hunt-tracker`) — gives mode-distinct DSL profiles without losing slot-pool fungibility.
- **9 total slots** (7 volume + 2 hunt). New funding requirement: **$6,000**.
- **Volume cycle 10 min default** with auto-fallback to 12 min when realized maker fill rate drops below 85%. Was fixed 15 min in v2.0.x.
- **Tighter spread gates** — main 5→3 bps, XYZ 15→10 bps.
- **Tighter universe** — dropped TSLA/NVDA from XYZ pool. 80/20 XYZ/main weighting (was 70/30).
- **HYPE hunt mode** — multi-axis 4H breakout score (max 15, floor 10) on 2 dedicated slots.
- **Sentinel sunset** — hunt slots take over with explicit slot accounting.
- **`senpi_runtime_helpers` integration** — no `mcporter` / `openclaw` subprocess. Long-lived `producer_daemon`.
- **`STRATEGY_ADDRESS` env var BANNED** — only `TURBINE_WALLET` honored per v2.0.9 rule.

## Mission targets

| Metric | v2.0.x | v3.0 |
|---|---|---|
| Daily volume | ~$2-3M | $5M |
| Net cost per $1M volume | $200 | <$100 |
| Total slots | 3 | 9 |
| Volume cycle | 15 min | 10 min |
| Funding | $1,500 | $6,000 |

See [SKILL.md](SKILL.md) for the full thesis, scoring tables, DSL presets, and risk-gate breakdown.

---

## Sunset sequence (BEFORE deploying v3.0)

**Critical: legacy Turbine v2.0.x and Sentinel both write to the SAME wallet.** Both must be stopped before v3.0 deploys to a fresh wallet.

```bash
# 1. Confirm what's running
openclaw cron list | grep -E "turbine|sentinel"
openclaw senpi runtime list | grep -E "turbine|sentinel"

# 2. Stop legacy producers/scanners
openclaw cron delete <turbine-v2-cron-id>
openclaw cron delete <sentinel-cron-id>     # if exists

# 3. Wait for or close any open positions on the legacy wallet
openclaw senpi strategy_get_clearinghouse_state --strategy_wallet <legacy-wallet>

# 4. Delete the legacy runtimes
openclaw senpi runtime delete <turbine-tracker-id>
openclaw senpi runtime delete <sentinel-tracker-id>     # if exists

# 5. Withdraw funds (or leave them; v3.0 uses a NEW wallet)
```

---

## Install

### Step 1 — Pull the helpers package (one-time per host)

```bash
mkdir -p /data/workspace/skills/_helpers/senpi_runtime_helpers/references
mkdir -p /data/workspace/skills/_helpers/senpi_runtime_helpers/tests

for f in __init__.py _config.py _logging.py cache.py client.py daemon.py lock.py parallel.py SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/_helpers/senpi_runtime_helpers/$f" \
    -o "/data/workspace/skills/_helpers/senpi_runtime_helpers/$f"
done
```

### Step 2 — Pull Turbine v3.0

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

Set `wallet`, `strategyId`, `chatId` in `config/turbine-config.json`.

### Step 4 — Required env vars

```bash
export TURBINE_WALLET=0xYourFreshTurbineWallet
export SENPI_AUTH_TOKEN=...
export TURBINE_VOLUME_DECISION_MODEL=gemini-3.1-pro-preview
export TURBINE_HUNT_DECISION_MODEL=gemini-3.1-pro-preview
```

Optional: `SENPI_MCP_URL`, `SENPI_RUNTIME_API_HOST`, `SENPI_RUNTIME_API_PORT`, `OPENCLAW_WORKSPACE` — sensible defaults.

### Step 5 — Fund the wallet

Fund the new strategy wallet with **$6,000** in USDC (Hyperliquid perps account).

### Step 6 — Install BOTH runtimes

```bash
openclaw senpi runtime create --path /data/workspace/skills/turbine-strategy/runtime-volume.yaml
openclaw senpi runtime create --path /data/workspace/skills/turbine-strategy/runtime-hunt.yaml
openclaw senpi runtime list   # confirm BOTH ACTIVE
```

### Step 7 — Start the producer daemon

The v3.0 producer is a long-lived daemon. **Do NOT add an openclaw cron entry.**

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

| Status | Meaning | What to do |
|---|---|---|
| `ok` | Tick succeeded | Healthy |
| `skipped_locked` | Lock collision | Confirm no inner `scanner_lock` was added inside `main()` |
| `error` | `fn` raised | Read the `error` field |
| `timeout` | `fn` took > 45s | Tune `tick_timeout` or check MCP latency |

`daemon_self_terminated_no_runtime` is normal when the volume runtime is deleted.

## Verify volume + hunt are firing

```bash
tail -50 /tmp/turbine-producer.log | grep -v '"event"' | jq '.slots, .volume_emitted, .hunt_emitted, .current_cycle_min'
```

Expected first 5 minutes:
- `volume_emitted` populating with funding-fade signals
- `slots.volume.held` climbing toward 7
- `hunt_emitted` typically empty (HYPE 4H breakouts are rare)
- `current_cycle_min == 10` (or 12 if fill rate is low)

## What NOT to do

- ❌ **Do NOT** add a second openclaw cron — the daemon supervises itself
- ❌ **Do NOT** set `STRATEGY_ADDRESS` env var — banned per v2.0.9
- ❌ **Do NOT** run Sentinel concurrently — hunt mode supersedes it
- ❌ **Do NOT** lower `huntMinScore` below 10 without changing the scoring system
- ❌ **Do NOT** delete a runtime while positions are open — orphan-position bug

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Producer exits with `TURBINE_WALLET not set` | Env var missing | Export `TURBINE_WALLET` (NOT `STRATEGY_ADDRESS`) |
| `volume push_signal rejected ... INVALID_REQUEST` | Scanner schema mismatch | Confirm runtime-volume.yaml is latest from main |
| `volume push_signal rejected ... NOT_FOUND` | Volume runtime not registered | `openclaw senpi runtime list` |
| Volume slots never fill past 3-4 | Spread gates too tight, fill rate low | Check `current_cycle_min` and per-asset spread distribution |
| Hunt never fires | Score floor 10 is selective by design | Watch `state/<wallet-hash>/hunt-history.json` |
| Account drops below `minAccountValueForHunt` | Volume side ate a drawdown | Hunt auto-pauses; volume continues. Top up OR lower the floor. |

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
