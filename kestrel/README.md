# 🦅 KESTREL v3.0.0 — XYZ Macro Breakout Rider. senpi_runtime_helpers.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

**Plumbing-only migration from v2.0. NO thesis change. NO scoring change. NO threshold change.** Producer ports onto `senpi_runtime_helpers` (in-process `SenpiClient`, no openclaw / mcporter subprocesses). Long-lived `producer_daemon` replaces the openclaw cron entry. Fleet-fix #214 applied (no `wallet=`/`scanner=` daemon kwargs). v2.0.9 contamination rule applied: `KESTREL_WALLET` is the canonical env var (with `STRATEGY_ADDRESS` deprecation fallback).

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

### Step 1 — Pull the helpers package (one-time per host)

```bash
mkdir -p /data/workspace/skills/_helpers/senpi_runtime_helpers
for f in __init__.py _config.py _logging.py cache.py client.py \
         daemon.py lock.py parallel.py SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/_helpers/senpi_runtime_helpers/$f" \
    -o "/data/workspace/skills/_helpers/senpi_runtime_helpers/$f"
done
```

### Step 2 — Pull Kestrel v3.0.0

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
export KESTREL_DECISION_MODEL=gemini-2.5-pro     # or any model the runtime supports
```

### Step 4 — Stop v2.x cron, start v3.0.0 daemon

```bash
openclaw cron list | grep kestrel
openclaw cron delete <kestrel-cron-id>

nohup python3 -u /data/workspace/skills/kestrel-strategy/scripts/kestrel-producer.py \
  > /tmp/kestrel-producer.log 2>&1 &
```

## Smoke test

```bash
tail -f /tmp/kestrel-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expected: `status=ok` every tick (300s interval — macro 1H candles change slowly).

---

## Thesis (preserved)

When a macro asset moves >=1.5% in an hour with volume confirmation, the move usually continues for 1-3 hours. Ride the trend with wide DSL. 12-asset universe across commodities, indices, and high-volume equities. 24/7 trading on Hyperliquid XYZ DEX.

## Key parameters

| Parameter | Value |
|---|---|
| Universe | 12 macro assets (XYZ DEX) |
| Max positions | 2 |
| Margin per slot | $300 (30%) |
| Leverage | 3x or 5x (score-tiered) |
| **MIN_SCORE** | **5** (v2.0 calibration; preserved in v3.0.0) |
| 1H breakout threshold | 1.5% (mandatory hard gate) |
| Spread gate | 0.35% |
| Per-asset cooldown | 180 min (3h) |
| Post-close cooldown | 180 min |
| Daily entry cap | dynamic (P&L-aware, 0-12) |
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

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
