# 🍋 Lemon v2.0.0 — Degen Fader (senpi_runtime_helpers)

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

**Plumbing-only migration from v1.3. NO thesis change.** v1.3 fade scoring, MACRO_TREND_GATE (crypto only, |BTC 4h| > 3% blocks), XYZ unban, leverage tiers (5x/7x/10x), MIN_SCORE 9 all preserved verbatim. Scanner flips to in-process `SenpiClient`; daemon replaces openclaw cron. Runtime owns execution, daily caps, cooldowns, FEE_OPTIMIZED_LIMIT exits.

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

`openclaw gateway restart` after editing. Confirm with `curl -s -m 5 http://127.0.0.1:8787/state | head -c 200`.

### Step 1 — senpi-trading-runtime skill

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
which senpi-helpers
```

### Step 2 — Pull Lemon v2.0.0

```bash
mkdir -p /data/workspace/skills/lemon-strategy/{config,scripts,state}
for f in scripts/lemon-producer.py scripts/lemon_config.py runtime.yaml SKILL.md README.md \
         config/lemon-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/lemon/$f" \
    -o "/data/workspace/skills/lemon-strategy/$f"
done
```

### Step 3 — Configure wallet + chat ID

Edit `/data/workspace/skills/lemon-strategy/config/lemon-config.json` with `wallet`, `strategyId`, `chatId`.

### Step 4 — Env vars

```bash
export LEMON_WALLET=<your-lemon-wallet>
export SENPI_AUTH_TOKEN=...
export LEMON_DECISION_MODEL=gemini-2.5-pro
```

### Step 5 — Recreate runtime + launch daemon

```bash
openclaw senpi runtime list | grep lemon
openclaw senpi runtime delete <old-lemon-runtime-id>
openclaw senpi runtime create --path /data/workspace/skills/lemon-strategy/runtime.yaml

# Stop any v1.x cron / bash loop
openclaw cron list | grep lemon
openclaw cron delete <lemon-cron-id>
pkill -f lemon-scanner.py  # if running via the v1 bash loop

nohup python3 -u /data/workspace/skills/lemon-strategy/scripts/lemon-producer.py \
  > /tmp/lemon-producer.log 2>&1 &
```

If the daemon boots with `daemon_aborted_no_runtime: alive_check returned False`, the runtime wasn't installed — re-register it via the create command above.

## Smoke test

```bash
tail -f /tmp/lemon-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expect `status=ok` every 5 min (300s).

## Thesis (preserved from v1.3)

Counter-trade CHOPPY/DEGEN consensus on 12 crypto majors + 4 XYZ assets (BRENTOIL, CL, GOLD, SPX) when the move is exhausting (15m velocity ≤ 0.1). MACRO_TREND_GATE blocks crypto fades when |BTC 4h| > 3% — fade thesis fails in trending regimes. Wide DSL gives reversals time to mean-revert.

## License

MIT — Built by Senpi (https://senpi.ai).
