# 🦉 OWL v8.0.0 — Pure Contrarian Crowding-Unwind Hunter (senpi_runtime_helpers)

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## What changed in v8.0.0

**Plumbing-only migration. NO thesis change.** v7.1's crowding/exhaustion scoring, persistence gates, MACRO_TREND_GATE, conviction leverage tiers (7/8/10), MIN_COMBINED_SCORE 12, 6h post-loss cooldown, dynamic daily cap, XYZ ban, DSL preset are all preserved verbatim.

- `owl-producer.py` and `owl_config.py` migrate to `senpi_runtime_helpers`:
  - MCP calls go via `SenpiClient.mcp_call()` (direct HTTPS) instead of `mcporter` subprocess
  - Signal emission goes via `SenpiClient.push_signal()` (direct HTTP POST) instead of `openclaw senpi external-scanner ingest` subprocess
  - Reentrancy lock owned by `producer_daemon.scanner_lock` instead of hand-rolled `fcntl` on `producer.lock`
  - Tick scheduling owned by `producer_daemon` (long-lived process) instead of openclaw cron + `agentTurn`
- Requires the `senpi-trading-runtime` skill (preinstalled on the OpenClaw host).
- `runtime.yaml` schema version bumped 1.7.1 → 1.8.0. Scanner `owl_signals` and action `owl_entry` unchanged.
- `signal_type="OWL_CONTRARIAN_FADE"` passed explicitly to `push_signal()` so audit logs + LLM decision context stay correctly tagged (avoids relying on the runtime YAML's `defaultSignalType` fallback).

## Thesis (preserved from v7.1)

Counter-trade crowded crypto perps. Wait for crowding to persist 1+ hour AND exhaustion signals to fire (volume declining, price stalling, RSI divergence). Crowded trades unwind violently; Owl's edge is timing the unwind by waiting for both the crowd state AND the exhaustion trigger.

- Crowding score: funding extremity (0-4) + SM tilt (0-3, +1 if confirms funding) + OI concentration (0-2). Floor 6, persisted ≥ 1h.
- Exhaustion score: volume declining + price stalling + capitulation wick + RSI divergence. ≥ 2 distinct signals, score ≥ 5.
- Combined score ≥ 12 to fire.
- **MACRO_TREND_GATE:** block crypto fades when `|BTC 4h move|` > 3% (v7.1). Mean-reversion fails in trending regimes.
- XYZ banned (Owl's scoring is calibrated on crypto funding + SM shape; XYZ unban deferred).
- Per-asset 6h post-loss cooldown.

Entry direction is OPPOSITE of crowd direction. Owl is the only fleet agent that fades crowding.

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

### Step 2 — Pull Owl v8.0.0

```bash
mkdir -p /data/workspace/skills/owl-strategy/{config,scripts,state,references}
for f in scripts/owl-producer.py scripts/owl_config.py \
         runtime.yaml config/owl-config.json \
         SKILL.md README.md references/skill-attribution.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/owl/$f" \
    -o "/data/workspace/skills/owl-strategy/$f"
done

# Remove the v7.x scanner-style producer state lockfile if present (replaced
# by producer_daemon's scanner_lock — different lockfile location).
rm -f /data/workspace/skills/owl-strategy/state/*/producer.lock
```

### Step 3 — Required env vars

```bash
export OWL_WALLET=<your-owl-strategy-wallet>          # MUST match runtime.yaml's wallet
export SENPI_AUTH_TOKEN=...                            # required for MCP + signal POST
export OWL_DECISION_MODEL=gemini-3.1-pro-preview       # bare model name; NO provider prefix
export WALLET_ADDRESS=$OWL_WALLET                      # for runtime YAML substitution
export TELEGRAM_CHAT_ID=...                            # optional notifications
```

### Step 4 — (Re)create the runtime, stop any v7.x cron, start the v8.0.0 daemon

```bash
# Delete old runtime if you're migrating from v7.x
openclaw senpi runtime list | grep owl
openclaw senpi runtime delete owl-tracker

# Recreate from v8.0.0 runtime.yaml
openclaw senpi runtime create --path /data/workspace/skills/owl-strategy/runtime.yaml

# Drop any v7.x cron — v8.0.0 is daemon-driven
openclaw cron list | grep owl
openclaw cron delete <owl-cron-id>

# Start daemon (long-lived, no cron)
nohup python3 -u /data/workspace/skills/owl-strategy/scripts/owl-producer.py \
  > /tmp/owl-producer.log 2>&1 &
```

## Smoke test

```bash
tail -f /tmp/owl-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expected: `status=ok` every tick (900s interval = 15 min). Tick `duration_ms` should drop from ~30-60s (v7.x mcporter + ingest subprocess) to ~1-5s.

Inspect producer state:

```bash
ls -la /data/workspace/skills/owl-strategy/state/<wallet-hash>/
# Expect: crowding-history.json + (after first emit) asset-cooldowns.json + trade-counter.json
```

## Configure

**Set wallet, strategyId, chatId in `config/owl-config.json`** — canonical source. Producer reads `OWL_WALLET` env first, then `config.wallet`.

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourOwlStrategyWallet",
  "chatId": "YourTelegramChatId"
}
```

LLM model env var (only at runtime-create time):

```bash
export OWL_DECISION_MODEL=gemini-2.5-pro    # bare model name; NO provider prefix
```

## Key parameters

| Parameter | Value |
|---|---|
| Asset universe | All crypto perps with OI > $3M (XYZ banned) |
| Max positions (slots) | 2 |
| Margin per slot | 25% of account value (default ~$250 on $1k) |
| Default leverage | 8x |
| Leverage tiers | 7x @ score 12-13 / 8x @ 14-15 / 10x @ 16+ |
| MIN_CROWDING_SCORE | 6 |
| MIN_PERSIST_HOURS | 1 |
| MIN_EXHAUSTION_SIGNALS | 2 |
| MIN_EXHAUSTION_SCORE | 5 |
| MIN_COMBINED_SCORE | 12 |
| Per-asset cooldown | 360 min (6h post-loss) |
| Daily entry cap | 4 (runtime); producer dynamic cap by PnL |
| Daily loss limit | 10% |
| Drawdown halt | 25% |
| MACRO_GATE_BTC_4H_PCT | 3.0% |
| Entry order type | FEE_OPTIMIZED_LIMIT (maker-only, 60s) |
| Exit order type | FEE_OPTIMIZED_LIMIT (maker-first 60s, taker fallback) |
| Producer cadence | 900s (15 min) |

## DSL exits (preserved verbatim from v6.2)

Wide tolerance for contrarian retrace, fast-capture on the unwind.

| Tier | Trigger (margin ROE) | Lock (% of HW) |
|---|---|---|
| T0 | +5% | 20% |
| T1 | +10% | 35% |
| T2 | +20% | 50% |
| T3 | +35% | 65% |
| T4 | +50% | 78% |
| T5 | +75% | 88% |

Phase 1: max_loss 35% / retrace 15 / **single breach**.

Time cuts: `hard_timeout` 480m, `weak_peak_cut` 120m @ 2.0, `dead_weight_cut` 30m.

## Migrating from v7.x

```bash
cd /data/workspace/skills/owl-strategy

# Pull new files (curl above in Step 2)

# Stop the v7.x cron
openclaw cron list | grep owl
openclaw cron delete <owl-cron-id>

# Recreate the runtime (schema bumped 1.7.1 → 1.8.0)
openclaw senpi runtime delete owl-tracker
openclaw senpi runtime create --path runtime.yaml

# Launch the v8.0.0 daemon
nohup python3 -u scripts/owl-producer.py > /tmp/owl-producer.log 2>&1 &
```

State files (`state/<wallet-hash>/crowding-history.json`, `asset-cooldowns.json`, `trade-counter.json`) are preserved across the migration — the producer's wallet hashing is unchanged.

## License

Apache-2.0 — Built by Senpi (https://senpi.ai). Attribution required for derivative works.
Source: https://github.com/Senpi-ai/senpi-skills
