# 🦬 Bison v3.0.0 — Conviction Holder (senpi_runtime_helpers)

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

**Plumbing-only migration from v2.1. NO thesis change.** v2.1 asset whitelist (BTC/ETH/SOL), MIN_SCORE 11, conviction-scaled margin (25%/31%/37%), 9-component scoring, and wide DSL with time-cuts disabled all preserved verbatim. Scanner flips to in-process `SenpiClient`; daemon replaces openclaw cron. Runtime now owns execution, daily caps, cooldowns, and FEE_OPTIMIZED_LIMIT exits.

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

### Step 1 — Install the senpi-trading-runtime skill (one-time per host)

The Python Producer SDK (`senpi_runtime_helpers`) ships inside the senpi-trading-runtime skill. Install it once per host:

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Bison v3.0.0

```bash
mkdir -p /data/workspace/skills/bison-strategy/{config,scripts,state,references}
for f in scripts/bison-producer.py scripts/bison_config.py \
         runtime.yaml SKILL.md README.md references/skill-attribution.md \
         config/bison-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/bison/$f" \
    -o "/data/workspace/skills/bison-strategy/$f"
done
```

### Step 3 — Configure wallet, strategy ID, chat ID

Edit `/data/workspace/skills/bison-strategy/config/bison-config.json`:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId"
}
```

This is the canonical source of truth — producer reads from here on every tick; runtime reads at startup.

### Step 4 — Required env vars

```bash
# Per fleet v2.0.9 rule: per-agent wallet env var (optional override;
# config.json is the canonical source).
export BISON_WALLET=<your-bison-wallet>

export SENPI_AUTH_TOKEN=...                           # required
export BISON_DECISION_MODEL=<your-preferred-model>            # bare model name; NO provider prefix
```

### Step 5 — Recreate the runtime + start the daemon

If you have a v2.x runtime installed, delete it first — v3.0 introduces new scanner/action blocks that require a fresh runtime create.

```bash
openclaw senpi runtime list | grep bison
openclaw senpi runtime delete <old-bison-runtime-id>

openclaw senpi runtime create --path /data/workspace/skills/bison-strategy/runtime.yaml
openclaw senpi runtime list
```

If you were running the v2.x cron, stop it before launching the v3.0 daemon:

```bash
openclaw cron list | grep bison
openclaw cron delete <bison-cron-id>

nohup python3 -u /data/workspace/skills/bison-strategy/scripts/bison-producer.py \
  > /tmp/bison-producer.log 2>&1 &
```

After first launch, manage the daemon via the `senpi-helpers` CLI: `senpi-helpers list`, `senpi-helpers health <name>`, `senpi-helpers restart <name>`.

## Smoke test

```bash
tail -f /tmp/bison-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expected: `status=ok` every tick (300s / 5min interval).

---

## Thesis (preserved from v2.1)

Conviction holder. Few trades, longer holds, bigger moves. Asset whitelist (BTC/ETH/SOL by default) eliminates the small-cap-volume-spike failure mode. MIN_SCORE 11 demands real conviction across 5+ score components — not "first thing past the bar after midnight UTC."

## What changed in v3.0 vs v2.1

| Layer | v2.1 | v3.0 |
|---|---|---|
| MCP transport | `mcporter` subprocess (cold-start 2.5-5s) | `SenpiClient` direct HTTPS (~280ms) |
| Signal emit | scanner called `create_position` directly | producer emits via `push_signal()` → runtime LLM gate |
| Scheduler | openclaw cron (5min) | `producer_daemon(interval_seconds=300)` |
| Reentrancy | (none) | daemon's `scanner_lock` (stale-PID auto-recovery) |
| Daily cap | Python state files + dynamic-slots tiers | `runtime.yaml risk.guard_rails.max_entries_per_day` |
| Per-asset cooldown | `state/asset-cooldowns.json` | `runtime.yaml risk.guard_rails.per_asset_cooldown_minutes` |
| Exit fee | MARKET (taker, 0.045%) | FEE_OPTIMIZED_LIMIT (maker-first, 0.015%) |
| DSL state | hardcoded template in scanner output | runtime YAML `dsl_preset` is single source of truth |

NO change to asset whitelist, MIN_SCORE, scoring components, direction waterfall, conviction-scaled margin, leverage tiers, or DSL preset.

## Key parameters

| Parameter | Value |
|---|---|
| Universe | BTC, ETH, SOL (override via `allowedAssets`) |
| Max positions | 3 |
| Tick interval | 300s (5 min) |
| MIN_SCORE (producer) | 11 |
| LLM min_confidence | 7 |
| Margin tiers | 25% (score 8-9) / 31% (10-11) / 37% (12+) |
| Leverage | 10x default (MIN 7x, MAX 10x) |
| Entry order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: false) |
| Exit order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: true) |
| Per-asset cooldown | 120 min |
| Daily entry cap | 3 |
| Daily loss limit | 10% |
| Drawdown halt | 25% |

## DSL preset (wide, patient — preserved from v2.1)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 30% |
| Phase 1 | retrace_threshold | 8 |
| Phase 1 | consecutive_breaches | 3 |
| Time cuts | hard_timeout | **DISABLED** |
| Time cuts | weak_peak_cut | 60 min, min 3.0% (self-limiting) |
| Time cuts | dead_weight_cut | **DISABLED** |
| Phase 2 | T0 | +10% / 0% lock |
| Phase 2 | T1 | +20% / 25% lock |
| Phase 2 | T2 | +30% / 40% lock |
| Phase 2 | T3 | +50% / 60% lock |
| Phase 2 | T4 | +75% / 75% lock |
| Phase 2 | T5 | +100% / 85% lock (apex) |

## License

Apache-2.0 — attribution required for derivative works. Copyright 2026 Senpi (https://senpi.ai).
