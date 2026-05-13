# 🦡 WOLVERINE — HYPE Alpha Hunter

Single-asset alpha hunter for HYPE. Trades with multi-timeframe trend alignment when 4h structure, 1h confirmation, and 15m momentum line up in the same direction.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Wolverine hunts HYPE-specific directional moves where multi-timeframe momentum has just aligned. Entry is gated by six independent checks: 4h trend structure, 4h structural strength ≥ 0.65, 1h-to-4h alignment, 15m momentum ≥ 0.15, a base-tech floor, and a 4h magnitude ≥ 1.0% threshold. Multi-factor scoring (~17 max points) feeds a conviction-tiered leverage ladder — modest size at the floor, larger size only when score reaches apex.

The strategy is part of the Kodiak family (same architecture as Grizzly/Polar/Kodiak/Dire) but tuned to HYPE's faster cadence and thinner overnight liquidity. FP-001 quiet hours block sub-apex entries from 00:00-04:00 UTC. Exits are owned by the DSL Phase 2 ladder — all time-based cuts are disabled so winners only close on price action.

## Key parameters

| Parameter | Value |
|---|---|
| Asset universe | HYPE (single-asset) |
| Tick interval | 180s |
| MIN_SCORE | 9 |
| Leverage tiers | 3x standard (score 9) / 5x apex (score 11+) |
| Max entries per day | 4 |
| Per-asset cooldown | 120 min (2h) |
| Daily loss limit | 10% |
| Drawdown halt | 25% |
| Quiet hours | 00:00-04:00 UTC (apex score 11+ bypasses) |
| LLM min_confidence | 7 |
| Margin per slot | $250 |
| Max positions | 1 |
| Entry order type | FEE_OPTIMIZED_LIMIT |
| Exit order type | FEE_OPTIMIZED_LIMIT |

### DSL Phase 2 ladder

HYPE-tuned. All time-based cuts disabled — exits 100% price-action.

| Tier | Trigger (margin ROE) | Lock (% of HW) |
|---|---|---|
| T0 | +10% | 15% |
| T1 | +20% | 35% |
| T2 | +35% | 55% |
| T3 | +55% | 70% |
| T4 (apex) | +80% | 85% |

Phase 1: max_loss 20% / retrace 8% / 3 consecutive breaches.
**Time-cuts:** `hard_timeout` / `weak_peak_cut` / `dead_weight_cut` all DISABLED.

## Scanner pattern

This strategy uses the **Single-asset alpha hunter (Kodiak family)** scanner pattern — see `senpi-trading-runtime/references/producer-patterns.md` for the canonical reference. Primary MCP call: `market_get_asset_data` for HYPE.

## Files

| File | Purpose |
|---|---|
| `runtime.yaml` | Runtime spec (external_scanner, risk guard rails, DSL) |
| `scripts/wolverine-producer.py` | Long-lived daemon emitting HYPE entry signals |
| `scripts/wolverine_config.py` | SDK probe + SenpiClient wrapper |
| `config/wolverine-config.json` | Operator-tunable defaults (wallet, minScore, quiet hours) |

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

Skip if the senpi-trading-runtime skill is already installed on this host.

### Step 2 — Pull Wolverine

```bash
mkdir -p /data/workspace/skills/wolverine-strategy/{config,scripts,state,references}
for f in scripts/wolverine-producer.py scripts/wolverine_config.py \
         SKILL.md README.md references/skill-attribution.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/wolverine/$f" \
    -o "/data/workspace/skills/wolverine-strategy/$f"
done
```

### Step 3 — Required env vars

```bash
export WOLVERINE_WALLET_ADDRESS=<your-wolverine-wallet>
export SENPI_AUTH_TOKEN=...
export WOLVERINE_DECISION_MODEL=<your-preferred-model>
```

### Step 4 — Start the daemon

```bash
# Stop any prior cron
openclaw cron list | grep wolverine
openclaw cron delete <wolverine-cron-id>

# Start daemon (long-lived, no cron)
nohup python3 -u /data/workspace/skills/wolverine-strategy/scripts/wolverine-producer.py \
  > /tmp/wolverine-producer.log 2>&1 &
```

## Configure

**Set wallet, strategyId, chatId in `config/wolverine-config.json`** — canonical source. Producer reads from here on every tick.

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId",
  "minScore": 9,
  "quietHours": { "startUtc": 0, "endUtc": 4, "apexBypassScore": 11 }
}
```

LLM model env var (only at runtime-create time):

```bash
export WOLVERINE_DECISION_MODEL=<your-preferred-model>    # bare model name; NO provider prefix
```

## Verification

```bash
tail -f /tmp/wolverine-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expected: `status=ok` every tick (180s interval). Tick `duration_ms` should be ~1-3s.

## Changelog

### v5.0.0 — `senpi_runtime_helpers` migration

Plumbing-only migration. NO thesis change. v4.2.0's six-gate validation, scoring, leverage tiers, MIN_SCORE 9, quiet hours, DSL preset all preserved verbatim.

- `wolverine-producer.py` and `wolverine_config.py` migrate to `senpi_runtime_helpers`:
  - MCP calls go via `SenpiClient.mcp_call()` (direct HTTPS) instead of `mcporter` subprocess
  - Signal emission goes via `SenpiClient.push_signal()` (direct HTTP POST)
  - Reentrancy lock owned by `producer_daemon.scanner_lock` instead of hand-rolled `fcntl`
  - Tick scheduling owned by `producer_daemon` (long-lived process) instead of openclaw cron + `agentTurn`
- Requires the `senpi-trading-runtime` skill (preinstalled on the OpenClaw host).
- `runtime.yaml` unchanged. `external_scanner.name: wolverine_signals` matches the producer's `client.push_signal(scanner=...)`.
- Dead fields stripped from signal payload; `signal_type="WOLVERINE_HYPE_HYBRID"` passed explicitly to `push_signal()` so audit logs + LLM decision context stay correctly tagged (avoids relying on the runtime YAML's `defaultSignalType` fallback).

### Migrating from v3.x

```bash
cd /data/workspace/skills/wolverine-strategy
rm -f scripts/wolverine-scanner.py                    # replaced by wolverine-producer.py
# Pull new files (curl above)
# Stop any v3.x cron: openclaw cron list | grep wolverine ; openclaw cron delete <id>
# Launch the v5.0+ daemon per Step 4 above (nohup python3 -u ...)
# Reload runtime: openclaw senpi runtime delete <old>; openclaw senpi runtime create --path runtime.yaml
```

State files (`state/trade-counter.json`, `state/cooldowns.json`) are vestigial in v5.0+ and can be deleted.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
