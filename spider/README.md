# 🕷️ SPIDER — Patient Anchor Sniper

Hold one high-conviction long anchor for at least 7 days while the rest of the fleet churns.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Hold one high-conviction long anchor for at least 7 days. Generate edge from:

1. **Multi-day trend** — top SM markets that are also held by arena top-10 traders
2. **Positive carry** — prefer assets where funding favors longs
3. **Relative strength** — assets outperforming over 30d
4. **Fee-aware** — FEE_OPTIMIZED_LIMIT entries + exits, ~1 entry + 1 exit per trade

While 95% of the fleet churns daily on noise, Spider sits with a single position. **Patience is the edge.**

## Key parameters

| Parameter | Value |
|---|---|
| Asset universe | Top 15 SM leaderboard (XYZ banned, LONG-only) |
| Tick interval | 3600s (60 min) |
| MIN_SCORE | 5.5 (v3.0.2 calibration; preserved) |
| Max positions | 1 (anchor only) |
| Margin per slot | $1000 (100% of equity, single anchor) |
| Leverage tiers | 1x / 2x / 3x (score-tiered, capped at 3x) |
| Min hold | 7 days |
| Post-close cooldown | 7 days (matches min-hold) |
| Per-asset cooldown | 10080 min (7d) |
| Daily loss limit | 12% |
| Drawdown halt | 25% |
| Entry order type | FEE_OPTIMIZED_LIMIT |
| Exit order type | FEE_OPTIMIZED_LIMIT |

## DSL Phase 2 ladder (patience-tuned, wider than active agents)

| Tier | Trigger (margin ROE) | Lock (% of HW) |
|---|---|---|
| T0 | +10% | 35% |
| T1 | +20% | 55% |
| T2 | +35% | 70% |
| T3 | +60% | 85% |
| T4 (apex) | +100% | 92% |

Phase 1: max_loss 12% / retrace 8 / 3 consecutive breaches.
Time cuts: hard_timeout 30d (43200 min) — fail-safe only. weak_peak_cut and dead_weight_cut DISABLED — patience agent.

## Scanner pattern

This strategy uses the **specialty / portfolio operator** scanner pattern — see `senpi-trading-runtime/references/producer-patterns.md` for the canonical reference. Primary MCP calls: `arena_leaderboard`, `strategy_list` (resolve M-IDs to wallets), `leaderboard_get_trader_positions`, plus `leaderboard_get_markets` for the SM universe.

## Files

| File | Purpose |
|---|---|
| runtime.yaml | Runtime spec |
| scripts/spider-producer.py | Long-lived daemon |
| scripts/spider_config.py | SDK probe + SenpiClient wrapper |
| config/spider-config.json | Operator-tunable defaults |

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

### Step 2 — Pull Spider

```bash
mkdir -p /data/workspace/skills/spider-strategy/{config,scripts,state,references}
for f in scripts/spider-producer.py scripts/spider_config.py \
         runtime.yaml SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/spider/$f" \
    -o "/data/workspace/skills/spider-strategy/$f"
done
```

### Step 3 — Required env vars

```bash
export SPIDER_WALLET=<your-spider-wallet>      # or set wallet field in config/spider-config.json
export SENPI_AUTH_TOKEN=...
export SPIDER_DECISION_MODEL=<your-preferred-model>    # or any model the runtime supports
```

### Step 4 — Stop legacy cron (if present), start the daemon

```bash
openclaw cron list | grep spider
openclaw cron delete <spider-cron-id>

nohup python3 -u /data/workspace/skills/spider-strategy/scripts/spider-producer.py \
  > /tmp/spider-producer.log 2>&1 &
```

## Verification

```bash
tail -f /tmp/spider-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expected: `status=ok` every tick (3600s interval — Spider's hourly cadence reflects the 7-day-min-hold thesis horizon).

## Changelog

### v4.0.0 — Plumbing-only migration from v3.0.2 (no thesis change)

NO scoring change. NO threshold change. Producer ports onto `senpi_runtime_helpers` (in-process `SenpiClient`, no openclaw / mcporter subprocesses). Long-lived `producer_daemon` replaces the openclaw cron entry. v2.0.9 contamination rule applied: `SPIDER_WALLET` is the canonical env var (with `STRATEGY_ADDRESS` deprecation fallback).

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
