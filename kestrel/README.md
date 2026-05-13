# 🦅 KESTREL — XYZ Macro Breakout Rider

Universe trend-follower that rides 1H breakouts on commodities, indices, and high-volume equities 24/7.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

When a macro asset moves >=1.5% in an hour with volume confirmation, the move usually continues for 1-3 hours. Ride the trend with wide DSL. 12-asset universe across commodities, indices, and high-volume equities. 24/7 trading on Hyperliquid XYZ DEX.

Unlike crypto-native rotation agents, Kestrel hunts on the XYZ DEX where stocks, commodities, metals, and indices trade around the clock — including weekends. A mandatory 1H breakout gate filters out chop, volume confirmation filters out fake-outs, and the universe-rank scanner picks the cleanest trending name available each tick.

## Key parameters

| Parameter | Value |
|---|---|
| Asset universe | 12 macro assets (XYZ DEX) |
| Tick interval | 300s (5 min — macro 1H candles change slowly) |
| MIN_SCORE | 5 (v2.0 calibration; preserved) |
| 1H breakout threshold | 1.5% (mandatory hard gate) |
| Spread gate | 0.35% |
| Max positions | 2 |
| Margin per slot | $300 (30%) |
| Leverage tiers | 3x or 5x (score-tiered) |
| Daily entry cap | dynamic (P&L-aware, 0-12) |
| Per-asset cooldown | 180 min (3h) |
| Post-close cooldown | 180 min |
| Daily loss limit | 10% |
| Drawdown halt | 25% |
| Entry order type | FEE_OPTIMIZED_LIMIT (taker fallback) |
| Exit order type | FEE_OPTIMIZED_LIMIT (taker fallback) |

## DSL Phase 2 ladder (fleet-standard)

| Tier | Trigger (margin ROE) | Lock (% of HW) |
|---|---|---|
| T0 | +5% | 35% |
| T1 | +10% | 50% |
| T2 | +20% | 65% |
| T3 | +35% | 80% |
| T4 (apex) | +50% | 90% |

Phase 1: max_loss 18% / retrace 8 / 3 consecutive breaches.
Time cuts: hard_timeout 480min, weak_peak_cut 60min @ 2.0, dead_weight_cut 45min — all ENABLED (catch false breakouts early).

## Scanner pattern

This strategy uses the **universe trend-follower** scanner pattern — see `senpi-trading-runtime/references/producer-patterns.md` for the canonical reference. Primary MCP calls: `leaderboard_get_markets` (XYZ universe), then `market_get_asset_data` per ranked candidate.

## Files

| File | Purpose |
|---|---|
| runtime.yaml | Runtime spec |
| scripts/kestrel-producer.py | Long-lived daemon |
| scripts/kestrel_config.py | SDK probe + SenpiClient wrapper |
| config/kestrel-config.json | Operator-tunable defaults |

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

### Step 2 — Pull Kestrel

```bash
mkdir -p /data/workspace/skills/kestrel-strategy/{config,scripts,state,references}
for f in scripts/kestrel-producer.py scripts/kestrel_config.py \
         runtime.yaml SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/kestrel/$f" \
    -o "/data/workspace/skills/kestrel-strategy/$f"
done
```

### Step 3 — Required env vars

```bash
export KESTREL_WALLET=<your-kestrel-wallet>      # or set wallet field in config/kestrel-config.json
export SENPI_AUTH_TOKEN=...
export KESTREL_DECISION_MODEL=<your-preferred-model>     # or any model the runtime supports
```

### Step 4 — Stop legacy cron, start the daemon

```bash
openclaw cron list | grep kestrel
openclaw cron delete <kestrel-cron-id>

nohup python3 -u /data/workspace/skills/kestrel-strategy/scripts/kestrel-producer.py \
  > /tmp/kestrel-producer.log 2>&1 &
```

## Verification

```bash
tail -f /tmp/kestrel-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expected: `status=ok` every tick (300s interval — macro 1H candles change slowly).

## Changelog

### v3.0.0 — Plumbing-only migration from v2.0 (no thesis change)

NO scoring change. NO threshold change. Producer ports onto `senpi_runtime_helpers` (in-process `SenpiClient`, no openclaw / mcporter subprocesses). Long-lived `producer_daemon` replaces the openclaw cron entry. v2.0.9 contamination rule applied: `KESTREL_WALLET` is the canonical env var (with `STRATEGY_ADDRESS` deprecation fallback).

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
