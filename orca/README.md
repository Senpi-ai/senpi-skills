# 🐋 Orca v4.0.0 — Gen-1 Vanilla Striker (senpi_runtime_helpers)

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

**Plumbing-only migration from v3.0. NO thesis change.** v3.0 Gen-1 vanilla Striker logic (FIRST_JUMP + base scoring + volume confirmation) preserved verbatim. Scanner flips to in-process `SenpiClient`; daemon replaces openclaw cron. Runtime now owns execution, daily caps, cooldowns, and FEE_OPTIMIZED_LIMIT exits.

## Install

### Step 0 — Register the runtime plugin in `openclaw.json` (one-time per host)

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

Restart the gateway: `openclaw gateway restart`. Confirm with `curl -s -m 5 http://127.0.0.1:8787/state | head -c 200`.

### Step 1 — Install the senpi-trading-runtime skill

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
which senpi-helpers   # should return a path
```

### Step 2 — Pull Orca v4.0.0

```bash
mkdir -p /data/workspace/skills/orca-strategy/{config,scripts,state}
for f in scripts/orca-producer.py scripts/orca_config.py runtime.yaml SKILL.md README.md \
         config/orca-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/orca/$f" \
    -o "/data/workspace/skills/orca-strategy/$f"
done
```

### Step 3 — Configure wallet + chat ID

Edit `/data/workspace/skills/orca-strategy/config/orca-config.json` with `wallet`, `strategyId`, `chatId`.

### Step 4 — Required env vars

```bash
export ORCA_WALLET=<your-orca-wallet>
export SENPI_AUTH_TOKEN=...
export ORCA_DECISION_MODEL=gemini-2.5-pro    # bare model name
```

### Step 5 — Recreate runtime + launch daemon

```bash
# Delete old v3.x runtime (new YAML has scanner/action blocks v3.x lacks)
openclaw senpi runtime list | grep orca
openclaw senpi runtime delete <old-orca-runtime-id>

openclaw senpi runtime create --path /data/workspace/skills/orca-strategy/runtime.yaml

# Stop any v3.x cron
openclaw cron list | grep orca
openclaw cron delete <orca-cron-id>

# Launch the v4.0 daemon
nohup python3 -u /data/workspace/skills/orca-strategy/scripts/orca-producer.py \
  > /tmp/orca-producer.log 2>&1 &
```

**If the daemon boots with `daemon_aborted_no_runtime: alive_check returned False`**, the runtime wasn't installed — re-register: `openclaw senpi runtime create --path /data/workspace/skills/orca-strategy/runtime.yaml`.

## Smoke test

```bash
tail -f /tmp/orca-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expect `status=ok` every 90s.

## Thesis (preserved from v3.0)

Vanilla Striker — FIRST_JUMP from #25+ with rank jump ≥ 15, score ≥ 9 with 4+ reasons, 4h trend aligned, 15m velocity > 0, volume ≥ 1.5x. Single API call per scan (`leaderboard_get_markets`). DSL exit managed by plugin runtime.

## License

MIT — Built by Senpi (https://senpi.ai).
