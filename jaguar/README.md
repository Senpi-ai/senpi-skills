# 🐆 Jaguar v4.0.0 — Striker / Rank-Jump Detector (senpi_runtime_helpers)

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

**Plumbing-only migration from v3.7. NO thesis change.** v3.x striker
scoring + DSL preset + risk.guard_rails preserved verbatim. Producer
flips to in-process `SenpiClient`, daemon replaces cron, runtime owns
execution.

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

### Step 2 — Pull Jaguar v4.0.0

```bash
mkdir -p /data/workspace/skills/jaguar-strategy/{config,scripts,state,references}
for f in scripts/jaguar-producer.py scripts/jaguar_config.py \
         runtime.yaml SKILL.md README.md \
         config/jaguar-config.json references/skill-attribution.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/jaguar/$f" \
    -o "/data/workspace/skills/jaguar-strategy/$f"
done
```

### Step 3 — Configure wallet / strategyId / chatId

Edit `/data/workspace/skills/jaguar-strategy/config/jaguar-config.json`:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourJaguarStrategyWallet",
  "chatId": "YourTelegramChatId"
}
```

This is the canonical source of truth. Producer reads `wallet` from
here on every tick; runtime reads at startup.

### Step 4 — Required env vars

```bash
export SENPI_AUTH_TOKEN=...                       # Bearer for MCP + signal POST
export JAGUAR_DECISION_MODEL=gemini-2.5-pro       # bare model name; NO provider prefix
# Optional override (defaults to config.wallet):
# export JAGUAR_WALLET=0x...
```

### Step 5 — Install runtime

```bash
openclaw senpi runtime create --path /data/workspace/skills/jaguar-strategy/runtime.yaml
openclaw senpi runtime list   # jaguar-tracker must appear ACTIVE
```

### Step 6 — Stop v3.x cron, start v4.0.0 daemon

```bash
openclaw cron list | grep jaguar
openclaw cron delete <jaguar-cron-id>

nohup python3 -u /data/workspace/skills/jaguar-strategy/scripts/jaguar-producer.py \
  > /tmp/jaguar-producer.log 2>&1 &
```

### Step 7 — Legacy cleanup (if migrating from v3.x)

```bash
# Producer no longer tracks these — runtime owns them now.
rm -f /data/workspace/skills/jaguar-strategy/state/trade-counter.json
rm -f /data/workspace/skills/jaguar-strategy/state/cooldowns.json
rm -f /data/workspace/skills/jaguar-strategy/state/asset-cooldowns.json
# scan-history.json is still used by the producer — leave it.
```

## Smoke test

```bash
tail -f /tmp/jaguar-producer.log | jq -c 'select(.status=="ok")' | head -3
```

Expected: `status=ok` every tick (180s interval). Heartbeat fields:
`scanned`, `candidates`, `signals_pushed`, `min_score`, `elapsed_sec`,
`_jaguar_producer_version`.

---

## Thesis (preserved from v3.7)

Violent rank-jump explosions only. When an asset rockets from rank 20+
into the top 10 with a ≥10 rank jump AND 15m contribution velocity is
actively building AND 4h price is aligned with SM direction, that's a
Striker. Rare but high-conviction. "One amazing trade per day"
discipline; capped at 3 entries/day on RED days, unlimited on GREEN.

## What changed structurally in v4.0.0

- `jaguar-producer.py` (NEW) replaces `jaguar-scanner.py` (DELETED).
  Pure producer — no `create_position` calls, no trade counters, no
  cooldown state, no held+pending dedup, no daily-cap state file.
- MCP calls flip from mcporter subprocess to in-process
  `SenpiClient.mcp_call()` via `jaguar_config.mcporter_call` shim.
- Cron → long-lived daemon via `producer_daemon` (180s tick).
- fcntl reentrancy guard removed — `producer_daemon` owns the per-tick
  scanner_lock with stale-PID auto-recovery.
- runtime.yaml declares `jaguar_signals` external_scanner + an
  LLM-pass-through `jaguar_entry` action; risk.guard_rails owns
  daily caps, per-asset cooldown, drawdown halt, consecutive-loss halts.
- Trade chain DB emits `LIFECYCLE_RUNTIME_STARTED → DECISION_EXECUTED
  → ACTION_RESULT → DSL_CREATED → DSL_CLOSED` for every trade —
  per-trade telemetry restored.

## Configure

**Set wallet, strategy ID, and chat ID in `config/jaguar-config.json`** — this is the canonical source of truth. Producer reads from here on every tick; runtime reads from here at startup.

Set the LLM decision model via env var at runtime-create time (resolved once into runtime.yaml's `${JAGUAR_DECISION_MODEL}` placeholder):

```bash
export JAGUAR_DECISION_MODEL=gemini-2.5-pro    # bare model name; NO provider prefix
```

## Key parameters

| Parameter | Value |
|---|---|
| Mode | Striker only (rank-jump detector) |
| Universe | All Hyperliquid perps on leaderboard (XYZ banned) |
| Max positions | 2 concurrent |
| Leverage | 7x (conviction) / 10x (apex) — score-scaled, per-asset HL-clamped |
| Margin per slot | 50% of account |
| Entry order type | FEE_OPTIMIZED_LIMIT (30s timeout, ALO-then-taker) |
| Exit order type | FEE_OPTIMIZED_LIMIT (60s timeout, ALO-then-taker) |
| MIN_SCORE (producer) | 9 |
| LLM min_confidence | 7 |
| Rank jump floor | ≥10 |
| Prev-rank floor | ≥20 |
| Day-notional liquidity floor | $3M |
| Producer tick interval | 180s |
| hard_timeout | 45 min |
| weak_peak_cut | 25 min @ 3% min |
| dead_weight_cut | 12 min |
| Per-asset cooldown | 120 min |
| Daily entry cap (RED day) | 3 |
| Daily entry cap (GREEN day) | unlimited (bypass on profit) |
| Daily loss limit | 10% |
| Drawdown halt | 25% |

## DSL Phase 2 ladder

| Tier | Trigger (margin ROE) | Lock (% of HW) |
|---|---|---|
| T0 | +7% | 40% |
| T1 | +12% | 55% |
| T2 | +15% | 75% |
| T3 | +20% | 85% |

## License

MIT — Built by Senpi (https://senpi.ai).
