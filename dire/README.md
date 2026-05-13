# 🦡 DIRE v2.0.0 — BRENTOIL XYZ Specialist (helpers-native)

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## What changed in v2.0.0

**Plumbing-only migration. NO thesis change.** v1.7.0's 4TF / SM / OI / volume / cleanliness gates, MIN_SCORE 11, FP-001 quiet hours, FP-003 require-all-confirmations, conviction-scaled sizing, DSL preset with T0/T1 patch — all preserved verbatim.

- `dire-producer.py` and `dire_config.py` migrate to `senpi_runtime_helpers`:
  - MCP calls go via `SenpiClient.mcp_call()` (direct HTTPS) instead of `mcporter` subprocess
  - Signal emission goes via `SenpiClient.push_signal()` (direct HTTP POST)
  - Reentrancy lock owned by `producer_daemon.scanner_lock` instead of hand-rolled `fcntl`
  - Tick scheduling owned by `producer_daemon` (long-lived process) instead of openclaw cron + `agentTurn`
- Requires the `senpi-trading-runtime` skill (preinstalled on the OpenClaw host).
- `runtime.yaml` rewritten — `external_scanner.name: dire_signals` matches the producer's `push_signal(scanner=...)`. Risk guard rails (daily cap, drawdown halt, consecutive losses, per-asset cooldown) replace Python-side state files. Old `dire-scanner.py` removed.
- `signal_type="DIRE_BRENTOIL_TREND"` passed explicitly to `push_signal()` so audit logs + LLM decision context stay correctly tagged.

## Thesis (preserved from v1.7.0)

Single-asset BRENTOIL XYZ specialist. News-driven momentum breakouts on oil with tight DSL protection against sharp geopolitical reversals. Six-gate entry validation: 4TF alignment (5m/15m/1h/4h), SM HARD BLOCK on mark/oracle premium direction, SM conviction tier, OI velocity scoring, volume spike scoring, price cleanliness check. MIN_SCORE 11 + all-five-confirmations gate (FP-003). FP-001 quiet hours block sub-apex entries during 00-04 UTC. Conviction-scaled leverage (3x cautious / 5x standard / 7x conviction / 10x apex). DSL Phase 2 trailing owns all winner exits with v1.7 T0/T1 patch.

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

### Step 2 — Pull Dire v2.0.0

```bash
mkdir -p /data/workspace/skills/dire-strategy/{config,scripts,references}
for f in scripts/dire-producer.py scripts/dire_config.py \
         config/dire-config.json runtime.yaml \
         SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/dire/$f" \
    -o "/data/workspace/skills/dire-strategy/$f"
done
```

### Step 3 — Required env vars

```bash
export DIRE_WALLET=<your-dire-wallet>
export SENPI_AUTH_TOKEN=...
export DIRE_DECISION_MODEL=gemini-2.5-pro          # bare model name; NO provider prefix
export TELEGRAM_CHAT_ID=<your-tg-chat-id>
```

### Step 4 — Stop the v1.x cron, register the v2.0.0 runtime, start the daemon

```bash
# Drop any v1.x cron (was self-executing)
openclaw cron list | grep dire
openclaw cron delete <dire-cron-id>

# Register the v2.0 runtime
openclaw senpi runtime delete dire-tracker 2>/dev/null || true
openclaw senpi runtime create --path /data/workspace/skills/dire-strategy/runtime.yaml

# Start daemon (long-lived, no cron)
nohup python3 -u /data/workspace/skills/dire-strategy/scripts/dire-producer.py \
  > /tmp/dire-producer.log 2>&1 &
```

## Smoke test

```bash
tail -f /tmp/dire-producer.log | jq -c 'select(.event=="daemon_tick_finished" or ._dire_producer_version)' | head -5
```

Expected: `status=ok` every tick (180s interval). Most ticks will show `signals_pushed=0` with a `note` documenting which gate blocked (BLOCKED:4TF_MISALIGNED, score_low, QUIET_HOURS, confirmations_incomplete). When all gates clear: `signals_pushed=1` with `note=PUSHED`.

## Configure

**Set wallet, strategyId, chatId in `config/dire-config.json`** — canonical source. Producer reads from here on every tick.

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId",
  "minScore": 11,
  "requireAllConfirmations": true,
  "quietHoursStartUtc": 0,
  "quietHoursEndUtc": 4,
  "quietHoursApexBypassScore": 12
}
```

LLM model env var (only at runtime-create time):

```bash
export DIRE_DECISION_MODEL=gemini-2.5-pro          # bare model name; NO provider prefix
```

## Key parameters

| Parameter | Value |
|---|---|
| Asset | xyz:BRENTOIL (single-asset, XYZ DEX) |
| Max positions | 1 |
| Margin per slot | $250 |
| Leverage | 3x / 5x / 7x / 10x (score-tiered: 9 / 10 / 11 / 12+) |
| MIN_SCORE | 11 |
| LLM min_confidence | 7 |
| Per-asset cooldown | 120 min (2h) |
| Daily entry cap | 2 |
| Daily loss limit | 10% |
| Drawdown halt | 15% (tighter than crypto siblings — oil tail risk) |
| Quiet hours | 00:00-04:00 UTC (apex score 12+ bypasses) |
| Require all confirmations | true (FP-003) |
| Entry order type | FEE_OPTIMIZED_LIMIT (maker-first, 30s timeout) |
| Exit order type | FEE_OPTIMIZED_LIMIT (maker-first, 60s timeout, taker-fallback) |
| Leverage type | ISOLATED (mandatory for XYZ DEX) |

## DSL Phase 2 ladder (preserved verbatim with v1.7 T0/T1 patch)

BRENTOIL-tuned. All time-based cuts disabled — exits 100% price-action.

| Tier | Trigger ROE | Lock (% of HW) |
|---|---|---|
| T0 | +5% | 35% (v1.7 patch: 25→35) |
| T1 | +8% | 50% (v1.7 patch: trigger 10→8) |
| T2 | +20% | 70% |
| T3 | +35% | 80% |
| T4 | +50% | 90% |

Phase 1: max_loss 20%, retrace 8%, 1 consecutive breach.
**Time-cuts:** `hard_timeout` / `weak_peak_cut` / `dead_weight_cut` all DISABLED (single-asset family pattern preserved from v1.3 / v1.4 / v1.5).

## Migrating from v1.x

```bash
cd /data/workspace/skills/dire-strategy
rm -f scripts/dire-scanner.py                       # replaced by dire-producer.py
# Pull new files (curl above)
# Stop any v1.x cron: openclaw cron list | grep dire ; openclaw cron delete <id>
# Re-register runtime: openclaw senpi runtime delete dire-tracker; openclaw senpi runtime create --path runtime.yaml
# Launch the v2.0+ daemon per Step 4 above
```

Old Python state files (`state/trade-counter.json`, `state/state.json`) are vestigial in v2.0+ and can be deleted — `risk.guard_rails` owns drawdown / daily cap / consecutive-loss tracking now.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
