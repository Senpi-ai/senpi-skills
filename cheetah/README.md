# 🐆 CHEETAH v7.0.0 — Multi-Signal Confluence Sniper (senpi_runtime_helpers)

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## What changed in v7.0.0

**Plumbing-only migration. NO thesis change.** v6.1's scoring tables, leverage tiers, dedup logic, post-close cooldown, runtime DSL preset are all preserved verbatim.

- `cheetah-producer.py` and `cheetah_config.py` migrate to `senpi_runtime_helpers`:
  - MCP calls go via `SenpiClient.mcp_call()` (direct HTTPS) instead of `mcporter` subprocess
  - Signal emission goes via `SenpiClient.push_signal()` (direct HTTP POST)
  - Reentrancy lock owned by `producer_daemon.scanner_lock` (PID-aliveness auto-recovery) instead of hand-rolled `fcntl`
  - Tick scheduling owned by `producer_daemon` (long-lived process) instead of openclaw cron + `agentTurn` (per-tick LLM cost)
- Requires `senpi-trading-runtime >= 1.1.0` (provides the `{success,data,error}` envelope and `GET /state` for daemon liveness probes).
- `runtime.yaml` unchanged. `external_scanner.name: cheetah_signals` matches the producer's `client.push_signal(scanner=...)`.

## What changed in v6.x (preserved)

- v2-runtime-native: external_scanner + LLM-pass-through gate + native `risk.guard_rails`
- DSL exits via `FEE_OPTIMIZED_LIMIT` (saves ~0.020-0.030% per maker-filled close)
- Trade chain DB emits per-trade telemetry
- **MIN_SCORE 10** (v5.2's 11 produced 8 days dormant; restored to 10)
- Held-asset dedup (3-layer)
- Post-close cooldown (Pangolin v2.1.2 pattern; backstops runtime per_asset_cooldown)
- All v5.2 scoring + leverage tiers + leverage-safety clamp preserved EXACTLY

## Thesis (preserved from v5.x)

Multi-signal confluence sniper. Refuses to trade unless ALL major signals align: SM consensus + velocity + acceleration + dual price confirmation + volume spike + quality-trader alignment + rank climb. Score 10/15 floor. Top-100 SM leaderboard universe. XYZ banned. Patience is the edge.

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

### Step 2 — Pull the Cheetah skill

```bash
mkdir -p /data/workspace/skills/cheetah-strategy/{config,scripts,state}

for f in runtime.yaml SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/cheetah/$f" \
    -o "/data/workspace/skills/cheetah-strategy/$f"
done
curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/cheetah/config/cheetah-config.json" \
  -o "/data/workspace/skills/cheetah-strategy/config/cheetah-config.json"
for f in cheetah-producer.py cheetah_config.py; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/cheetah/scripts/$f" \
    -o "/data/workspace/skills/cheetah-strategy/scripts/$f"
done
mkdir -p /data/workspace/skills/cheetah-strategy/references
curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/cheetah/references/skill-attribution.md" \
  -o "/data/workspace/skills/cheetah-strategy/references/skill-attribution.md"
```

## Configure

**Set wallet, strategyId, chatId in `config/cheetah-config.json`** — canonical source.

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId",
  "minScore": 10
}
```

### Required env vars

| Env var | Purpose |
|---|---|
| `CHEETAH_WALLET` | Strategy wallet (must match runtime.yaml). Per-agent; no `STRATEGY_ADDRESS` fallback. |
| `SENPI_AUTH_TOKEN` | Bearer token for MCP + signal POST. |
| `CHEETAH_DECISION_MODEL` | Bare model name (no provider prefix), e.g. `gemini-3.1-pro-preview`. Set at runtime-create time only. |

### Optional env vars (sensible defaults)

| Env var | Default | Purpose |
|---|---|---|
| `SENPI_MCP_URL` | `https://mcp.prod.senpi.ai/mcp` | Direct MCP endpoint |
| `SENPI_RUNTIME_API_HOST` | `127.0.0.1` | Runtime signal POST host |
| `SENPI_RUNTIME_API_PORT` | `8787` | Runtime signal POST port |
| `OPENCLAW_WORKSPACE` | `/data/workspace` | Skill mount root |

## Install the runtime

```bash
openclaw senpi runtime create --path /data/workspace/skills/cheetah-strategy/runtime.yaml
openclaw senpi runtime list   # confirm status: ACTIVE
```

## Run the producer (long-lived daemon — replaces cron)

The v7.0.0 producer is a long-lived daemon. **Do NOT add an openclaw cron entry** — that would spawn duplicate daemons. If you're upgrading from v6.x, delete the existing cheetah-producer cron first:

```bash
openclaw cron list | grep cheetah
openclaw cron delete <cheetah-cron-id>
```

Start the daemon (pick one supervision style):

```bash
# Option A — supervised by tini in a docker-managed container:
exec tini -- python3 -u /data/workspace/skills/cheetah-strategy/scripts/cheetah-producer.py

# Option B — nohup background process (simple, no auto-restart):
nohup python3 -u /data/workspace/skills/cheetah-strategy/scripts/cheetah-producer.py \
  > /tmp/cheetah-producer.log 2>&1 &
```

## Smoke test after deploy

Watch the daemon log for one minute:

```bash
tail -f /tmp/cheetah-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -5
```

Expected: every line shows `status=ok`.

| Status | Meaning | What to do |
|---|---|---|
| `ok` | Tick succeeded | Healthy |
| `skipped_locked` | Lock collision (likely double-locking) | Confirm no inner `scanner_lock` was added inside `main()` |
| `error` | `fn` raised | Read the `error` field |
| `timeout` | `fn` took too long | Tune `tick_timeout` in producer's `__main__` block |

`daemon_self_terminated_no_runtime` is normal when the runtime is deleted.

## Key parameters

| Parameter | Value |
|---|---|
| Universe | Top 100 SM leaderboard (XYZ banned) |
| Max positions | 1 |
| Margin per slot | $250 (30% of starting budget) |
| Leverage | 3x / 5x / 7x / 8x (score-tiered) |
| **MIN_SCORE** | **10** (down from v5.2's 11) |
| LLM min_confidence | 7 |
| Per-asset cooldown | 240 min (4h) |
| Post-close cooldown | 240 min (producer-side backstop) |
| Daily entry cap | 8 |
| Daily loss limit | 25% |
| Drawdown halt | 25% |
| drawdown_reset_on_day_rollover | false |
| Entry order type | FEE_OPTIMIZED_LIMIT |
| Exit order type | FEE_OPTIMIZED_LIMIT |

## DSL Phase 2 ladder (v6.0 — fleet-standard T0/T1)

| Tier | Trigger (margin ROE) | Lock (% of HW) |
|---|---|---|
| T0 | +5% | 35% |
| T1 | +10% | 50% |
| T2 | +20% | 65% |
| T3 | +35% | 80% |
| T4 (apex) | +50% | 90% |

Phase 1: max_loss 15% / retrace 6 / 3 consecutive breaches.
Time cuts: hard_timeout 720min, weak_peak_cut 90min @ 3.0, dead_weight_cut 60min — all ENABLED (multi-asset rotation has opportunity cost).

## Migrating from v6.x

```bash
cd /data/workspace/skills/cheetah-strategy

# 1. Install the senpi-trading-runtime skill (one-time per host) — Step 1 above.

# 2. Pull the new producer + config files (Step 2 above curl block).

# 3. Bump the runtime plugin to >= 2.0.0 if not already on it:
cat /data/.openclaw/extensions/runtime/package.json | grep version
# Minimum required version:
#   1.1.0

# 4. Stop the old producer cron (the v7.0.0 producer is a daemon now):
openclaw cron list | grep cheetah
openclaw cron delete <cheetah-cron-id>

# 5. Start the daemon (see "Run the producer" above).

# 6. runtime.yaml unchanged — no need to drop+recreate the runtime.
#    If you DO recreate it, do so only when there are no open positions
#    (orphan-position bug: v2 runtime swap can leave baseline positions
#    without DSL coverage).
```

State files (`state/entry-log.jsonl`, `state/scan-history.json`, `state/quality-cache.json`, `state/cooldowns.json`, `state/trade-counter.json`) live under `state/<wallet-hash>/` — wallet-isolated. Migration from v6.x preserves these files.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
