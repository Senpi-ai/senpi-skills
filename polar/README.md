# 🐻‍❄️ POLAR — ETH Alpha Hunter

Single-asset alpha hunter for ETH. Combines Hyperfeed Smart Money acceleration with multi-timeframe structural alignment for high-conviction directional entries.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Polar hunts ETH directional moves where Smart Money is actively accelerating and structure agrees across 4h / 1h / 15m. Entry requires Hyperfeed SM gates (pct ≥ 5%, traders ≥ 30, cc_15m ≥ 0.3 acceleration), structural gates (4h trend != NEUTRAL, full 4h-1h-15m alignment, RSI not extreme), and a multi-factor score ≥ 12. Leverage is conviction-tiered (5x / 7x / 10x at scores 14 / 15 / 17+) so size scales with confluence.

Polar is the ETH member of the Kodiak family — same architecture as Wolverine (HYPE), Grizzly (BTC), Kodiak (SOL), Dire (BRENTOIL). FP-001 quiet hours block sub-apex entries from 00:00-04:00 UTC, with apex score 17+ bypassing. Exits are owned by the DSL Phase 2 ladder; all time-based cuts are disabled so winners only close on price action.

## Key parameters

| Parameter | Value |
|---|---|
| Asset universe | ETH (single-asset) |
| Tick interval | 180s |
| MIN_SCORE | 14 |
| Leverage tiers | 5x / 7x / 10x (score-tiered: 14 / 15 / 17+) |
| Max entries per day | 4 |
| Per-asset cooldown | 240 min (4h) |
| Daily loss limit | 10% |
| Drawdown halt | 25% |
| Quiet hours | 00:00-04:00 UTC (apex score 17+ bypasses) |
| LLM min_confidence | 7 |
| Margin per slot | $500 |
| Max positions | 1 |
| Entry order type | FEE_OPTIMIZED_LIMIT |
| Exit order type | FEE_OPTIMIZED_LIMIT |

### DSL Phase 2 ladder

ETH-tuned, leverage-aware. All time-based cuts disabled — exits 100% price-action.

| Tier | Trigger (margin ROE) | Lock (% of HW) |
|---|---|---|
| T0 | +8% | 25% |
| T1 | +15% | 50% |
| T2 | +25% | 65% |
| T3 | +35% | 80% |
| T4 (apex) | +50% | 85% |

Phase 1: max_loss 25% / retrace 8% / 3 consecutive breaches.
**Time-cuts:** `hard_timeout` / `weak_peak_cut` / `dead_weight_cut` all DISABLED.

## Scanner pattern

This strategy uses the **Single-asset alpha hunter (Kodiak family)** scanner pattern — see `senpi-trading-runtime/references/producer-patterns.md` for the canonical reference. Primary MCP call: `market_get_asset_data` for ETH.

## Files

| File | Purpose |
|---|---|
| `runtime.yaml` | Runtime spec (external_scanner, risk guard rails, DSL) |
| `scripts/polar-producer.py` | Long-lived daemon emitting ETH entry signals |
| `scripts/polar_config.py` | SDK probe + SenpiClient wrapper |
| `config/polar-config.json` | Operator-tunable defaults (wallet, minScore, quiet hours) |

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

### Step 2 — Pull Polar

```bash
mkdir -p /data/workspace/skills/polar-strategy/{config,scripts,state,references}
for f in scripts/polar-producer.py scripts/polar_config.py \
         SKILL.md README.md references/skill-attribution.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/polar/$f" \
    -o "/data/workspace/skills/polar-strategy/$f"
done
```

### Step 3 — Required env vars

```bash
export POLAR_WALLET_ADDRESS=<your-polar-wallet>   # NOT STRATEGY_ADDRESS
export SENPI_AUTH_TOKEN=...
export POLAR_DECISION_MODEL=<your-preferred-model>
```

### Step 4 — Start the daemon

```bash
# Stop any prior cron
openclaw cron list | grep polar
openclaw cron delete <polar-cron-id>

# Start daemon (long-lived, no cron)
nohup python3 -u /data/workspace/skills/polar-strategy/scripts/polar-producer.py \
  > /tmp/polar-producer.log 2>&1 &
```

## Configure

**Set wallet, strategy ID, and chat ID in `config/polar-config.json`** — this is the canonical source of truth. Producer reads from here on every tick; runtime reads from here at startup.

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId",
  "minScore": 14,
  "quietHours": { "startUtc": 0, "endUtc": 4, "apexBypassScore": 17 }
}
```

Set the LLM decision model env var at runtime-create time only:

```bash
export POLAR_DECISION_MODEL=<your-preferred-model>    # bare model name; NO provider prefix
```

## Verification

```bash
tail -f /tmp/polar-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expected: `status=ok` every tick (180s interval).

## Changelog

### v5.0.0 — `senpi_runtime_helpers` migration

Plumbing-only migration. NO thesis change. v4.2.0's scoring tables, leverage tiers, MIN_SCORE 12, quiet hours, DSL preset all preserved verbatim.

- `polar-producer.py` and `polar_config.py` migrate to `senpi_runtime_helpers`:
  - MCP calls go via `SenpiClient.mcp_call()` (direct HTTPS) instead of `mcporter` subprocess
  - Signal emission goes via `SenpiClient.push_signal()` (direct HTTP POST)
  - Reentrancy lock owned by `producer_daemon.scanner_lock` instead of hand-rolled `fcntl`
  - Tick scheduling owned by `producer_daemon` (long-lived process) instead of openclaw cron + `agentTurn`
- Requires the `senpi-trading-runtime` skill (preinstalled on the OpenClaw host).
- `runtime.yaml` unchanged. `external_scanner.name: polar_signals` matches the producer's `client.push_signal(scanner=...)`.
- Dead fields stripped from signal payload; `signal_type="POLAR_ETH_HYBRID"` passed explicitly to `push_signal()` so audit logs + LLM decision context stay correctly tagged (avoids relying on the runtime YAML's `defaultSignalType` fallback).

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
