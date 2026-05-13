# 🐍 Python — The Patience Hunter

Multi-day LONG-biased trend holds on top-50 HL assets.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Python holds for days, not minutes. Target hold is 96h on the top 50 HL assets, with a 36% win rate paired to a 3.14:1 win/loss ratio — most trades are small losses, occasional multi-day winners do all the work. LONG-biased and 3-7x leverage, conviction-tier margin (25/30/40%). MACRO_TREND_GATE blocks counter-trend setups so we don't fight the regime.

The edge is patience. Wide DSL Phase 1 retrace (30%) lets winners breathe through normal intra-trend pullbacks. `weak_peak_cut` is explicitly DISABLED — patience holds cannot tolerate clock-based cuts; the whole edge dies if the runtime trims mid-thesis. The 96h hard_timeout is the only time-based exit.

## Key parameters

| Parameter | Value |
|---|---|
| Asset universe | Top 50 HL assets |
| Tick interval | 600s (10 min) |
| MIN_SCORE | 8 |
| Leverage tiers | 3-7x (conviction-scaled) |
| Margin tiers | 25% / 30% / 40% (conviction) |
| Direction bias | LONG |
| Macro gate | MACRO_TREND_GATE blocks counter-trend |
| `hard_timeout` | 96h |
| `weak_peak_cut` | DISABLED (patience holds incompatible with clock-based cuts) |
| DSL Phase 1 retrace | 30% (wide) |
| Entry order type | FEE_OPTIMIZED_LIMIT |
| Exit order type | FEE_OPTIMIZED_LIMIT |

## Scanner pattern

This strategy uses the **Universe trend-follower** scanner pattern (Patience Hunter variant — slow tick, multi-day hold) — see `senpi-trading-runtime/references/producer-patterns.md` for the canonical reference. Primary MCP call: `leaderboard_get_markets`, polled every 600s.

## Files

| File | Purpose |
|---|---|
| `runtime.yaml` | Runtime spec (scanners, actions, DSL preset, `risk.guard_rails`) |
| `scripts/python-producer.py` | Long-lived daemon; emits signals via `push_signal` |
| `scripts/python_config.py` | SDK probe + `SenpiClient` wrapper |
| `config/python-config.json` | Operator-tunable defaults (wallet, strategyId, chatId) |

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

### Step 2 — Pull Python

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

## Verification

```bash
ps aux | grep python-producer
senpi-helpers list
tail -f /tmp/python-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expect `status=ok` every 10 min (600s).

## Changelog

### v2.0.0 — helpers-native plumbing migration

Plumbing-only migration from v1.2. NO thesis change. v1.2 multi-day hold thesis, 4h+1h+1d scoring, MACRO_TREND_GATE, MIN_SCORE 8, LONG bias, 3-7x leverage, conviction-tier margin (25/30/40%), 96h `hard_timeout` with `weak_peak_cut` DISABLED all preserved verbatim.

## License

MIT — Built by Senpi (https://senpi.ai).
