# 🦖 Raptor v4.0.0 — Hot Streak Follower (senpi_runtime_helpers)

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

**Plumbing-only migration from v3.4. NO thesis change.** v3.4 quality-first pipeline (ELITE/RELIABLE weekly winners → strongest position → SM alignment), whale entry-discipline 5% threshold, nested-positions parser, MIN_SCORE 6, conviction-tier leverage all preserved verbatim.

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

### Step 2 — Pull Raptor v4.0.0

```bash
mkdir -p /data/workspace/skills/raptor-strategy/{config,scripts,state}
for f in scripts/raptor-producer.py scripts/raptor_config.py runtime.yaml SKILL.md README.md \
         config/raptor-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/raptor/$f" \
    -o "/data/workspace/skills/raptor-strategy/$f"
done
```

### Step 3 — Configure wallet + chat ID

Edit `/data/workspace/skills/raptor-strategy/config/raptor-config.json` with `wallet`, `strategyId`, `chatId`.

### Step 4 — Env vars

```bash
export RAPTOR_WALLET=<your-raptor-wallet>
export SENPI_AUTH_TOKEN=...
export RAPTOR_DECISION_MODEL=<your-preferred-model>
```

### Step 5 — Recreate runtime + launch daemon

```bash
openclaw senpi runtime list | grep raptor
openclaw senpi runtime delete <old-raptor-runtime-id>
openclaw senpi runtime create --path /data/workspace/skills/raptor-strategy/runtime.yaml

openclaw cron list | grep raptor
openclaw cron delete <raptor-cron-id>

nohup python3 -u /data/workspace/skills/raptor-strategy/scripts/raptor-producer.py \
  > /tmp/raptor-producer.log 2>&1 &
```

If `daemon_aborted_no_runtime: alive_check returned False`, re-register the runtime.

## Smoke test

```bash
tail -f /tmp/raptor-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expect `status=ok` every 3 min.

## Thesis (preserved from v3.4)

Find ELITE/RELIABLE traders currently winning weekly. Pick their strongest position by |delta_pnl|. Confirm SM alignment + 4h/1h price agreement. **Apply whale entry-discipline (5% threshold)** — if the asset has already run >5% in the whale's favor from their entry, skip; we'd be buying their top. Bonus +1/+2 points if we'd get a better fill than the whale.

## License

MIT — Built by Senpi (https://senpi.ai).
