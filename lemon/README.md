# 🍋 Lemon — Degen Fader

Counter-trades exhausting consensus on 12 crypto majors and 4 XYZ assets.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Lemon counter-trades CHOPPY/DEGEN consensus on 12 crypto majors plus 4 XYZ assets (BRENTOIL, CL, GOLD, SPX) — but only when the move is visibly exhausting (15m velocity ≤ 0.1). The edge is the regime filter: in trending tape, fading the consensus loses; in chop and degen-grind tape, the consensus is the wrong side. MACRO_TREND_GATE blocks crypto fades when |BTC 4h| > 3% because the fade thesis fails in trending regimes.

XYZ assets get to fire even when BTC is trending — they're decorrelated. Wide DSL gives the reversal time to mean-revert without getting shaken out by intratrend noise.

## Key parameters

| Parameter | Value |
|---|---|
| Asset universe | 12 crypto majors + 4 XYZ (BRENTOIL, CL, GOLD, SPX) |
| Tick interval | 300s (5 min) |
| MIN_SCORE | 9 |
| Leverage tiers | 5x / 7x / 10x |
| Macro gate | MACRO_TREND_GATE on crypto only (\|BTC 4h\| > 3% blocks) |
| 15m velocity gate | ≤ 0.1 (exhaustion) |
| Entry order type | FEE_OPTIMIZED_LIMIT |
| Exit order type | FEE_OPTIMIZED_LIMIT |

## Scanner pattern

This strategy uses the **Universe trend-follower** scanner pattern (Degen Fader variant) — see `senpi-trading-runtime/references/producer-patterns.md` for the canonical reference. Primary MCP call: `leaderboard_get_markets`, polled every 300s.

## Files

| File | Purpose |
|---|---|
| `runtime.yaml` | Runtime spec (scanners, actions, DSL preset, `risk.guard_rails`) |
| `scripts/lemon-producer.py` | Long-lived daemon; emits signals via `push_signal` |
| `scripts/lemon_config.py` | SDK probe + `SenpiClient` wrapper |
| `config/lemon-config.json` | Operator-tunable defaults (wallet, strategyId, chatId) |

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

### Step 2 — Pull Lemon

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
export LEMON_DECISION_MODEL=<your-preferred-model>
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

## Verification

```bash
ps aux | grep lemon-producer
senpi-helpers list
tail -f /tmp/lemon-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expect `status=ok` every 5 min (300s).

## Changelog

### v2.0.0 — helpers-native plumbing migration

Plumbing-only migration from v1.3. NO thesis change. v1.3 fade scoring, MACRO_TREND_GATE (crypto only, |BTC 4h| > 3% blocks), XYZ unban, leverage tiers (5x/7x/10x), MIN_SCORE 9 all preserved verbatim. Scanner flips to in-process `SenpiClient`; daemon replaces openclaw cron. Runtime owns execution, daily caps, cooldowns, FEE_OPTIMIZED_LIMIT exits.

## License

MIT — Built by Senpi (https://senpi.ai).
