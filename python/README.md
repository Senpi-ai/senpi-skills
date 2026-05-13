# 🐍 Python v2.0.0 — The Patience Hunter (senpi_runtime_helpers)

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

**Plumbing-only migration from v1.2. NO thesis change.** v1.2 multi-day hold thesis, 4h+1h+1d scoring, MACRO_TREND_GATE, MIN_SCORE 8, LONG bias, 3-7x leverage, conviction-tier margin (25/30/40%), 96h hard_timeout with weak_peak_cut DISABLED all preserved verbatim.

## Install

### Step 0 — `openclaw.json` plugin registration (one-time per host)

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

`openclaw gateway restart`. Confirm with `curl -s -m 5 http://127.0.0.1:8787/state | head -c 200`.

### Step 1 — senpi-trading-runtime skill

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
which senpi-helpers
```

### Step 2 — Pull Python v2.0.0

```bash
mkdir -p /data/workspace/skills/python-strategy/{config,scripts,state}
for f in scripts/python-producer.py scripts/python_config.py runtime.yaml SKILL.md README.md \
         config/python-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/python/$f" \
    -o "/data/workspace/skills/python-strategy/$f"
done
```

### Step 3 — Configure wallet + chat ID

Edit `/data/workspace/skills/python-strategy/config/python-config.json` with `wallet`, `strategyId`, `chatId`.

### Step 4 — Env vars

```bash
export PYTHON_WALLET=<your-python-wallet>
export SENPI_AUTH_TOKEN=...
export PYTHON_DECISION_MODEL=<your-preferred-model>
```

### Step 5 — Recreate runtime + launch daemon

```bash
openclaw senpi runtime list | grep python
openclaw senpi runtime delete <old-python-runtime-id>
openclaw senpi runtime create --path /data/workspace/skills/python-strategy/runtime.yaml

openclaw cron list | grep python
openclaw cron delete <python-cron-id>

nohup python3 -u /data/workspace/skills/python-strategy/scripts/python-producer.py \
  > /tmp/python-producer.log 2>&1 &
```

If `daemon_aborted_no_runtime: alive_check returned False`, re-register the runtime.

## Smoke test

```bash
tail -f /tmp/python-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expect `status=ok` every 10 min (600s).

## Thesis (preserved from v1.2)

Multi-day hold (96h target) on top 50 HL assets. 36% target win rate with 3.14:1 win/loss ratio — most trades small losses, occasional multi-day winners do the work. LONG-biased, 3-7x leverage. MACRO_TREND_GATE blocks counter-trend setups. DSL preserves wide retrace (Phase 1 30%) + 96h hard_timeout; weak_peak_cut disabled because patience holds cannot tolerate clock-based cuts.

## License

MIT — Built by Senpi (https://senpi.ai).
