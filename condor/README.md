# 🦅 Condor v4.0.0 — One Amazing Trade per Day (senpi_runtime_helpers)

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

**Plumbing-only migration from v3.4. NO thesis change.** v3.4 scoring tables, hard gates (3TF alignment, MACRO_TREND_GATE, SM consensus 70%), score-scaled sizing (50%/70%/80% margin), 10x leverage cap, and 6-tier DSL ladder (Kodiak SOL empirical) all preserved verbatim. Scanner flips to in-process `SenpiClient`; daemon replaces openclaw cron. Runtime now owns execution, daily caps, cooldowns, and FEE_OPTIMIZED_LIMIT exits.

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

### Step 2 — Pull Condor v4.0.0

```bash
mkdir -p /data/workspace/skills/condor-strategy/{config,scripts,state,references}
for f in scripts/condor-producer.py scripts/condor_config.py \
         runtime.yaml SKILL.md README.md \
         config/condor-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/condor/$f" \
    -o "/data/workspace/skills/condor-strategy/$f"
done
```

### Step 3 — Configure wallet, strategy ID, chat ID

Edit `/data/workspace/skills/condor-strategy/config/condor-config.json`:

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
export CONDOR_WALLET=<your-condor-wallet>

export SENPI_AUTH_TOKEN=...                           # required
export CONDOR_DECISION_MODEL=<your-preferred-model>           # bare model name; NO provider prefix
```

### Step 5 — Recreate the runtime + start the daemon

If you have a v3.x runtime installed, delete it first — v4.0 introduces new scanner/action blocks that require a fresh runtime create.

```bash
openclaw senpi runtime list | grep condor
openclaw senpi runtime delete <old-condor-runtime-id>

openclaw senpi runtime create --path /data/workspace/skills/condor-strategy/runtime.yaml
openclaw senpi runtime list
```

If you were running the v3.x cron, stop it before launching the v4.0 daemon:

```bash
openclaw cron list | grep condor
openclaw cron delete <condor-cron-id>

nohup python3 -u /data/workspace/skills/condor-strategy/scripts/condor-producer.py \
  > /tmp/condor-producer.log 2>&1 &
```

After first launch, manage the daemon via the `senpi-helpers` CLI: `senpi-helpers list`, `senpi-helpers health <name>`, `senpi-helpers restart <name>`.

## Smoke test

```bash
tail -f /tmp/condor-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expected: `status=ok` every 3 minutes (180s tick).

---

## Thesis (preserved from v3.4)

Top 50 HL assets. Pure trend continuation. Apex confluence only. One trade a day.

Hard gates: XYZ + stablecoins banned, OI ≥ $1M, trader_count ≥ 50, 3TF alignment (4h + 1h + 15m velocity all in same direction with magnitude floors), MACRO_TREND_GATE (block if |4h_move| > 10% opposite direction), SM consensus ≥ 70%.

Scoring: 4h magnitude + 1h confirmation + 15m velocity + 3TF bonus + SM tier + trader depth + funding + BTC macro + peak session. MIN_SCORE 12.

## What changed in v4.0 vs v3.4

| Layer | v3.4 | v4.0 |
|---|---|---|
| MCP transport | `mcporter` subprocess (cold-start 2.5-5s) | `SenpiClient` direct HTTPS (~280ms) |
| Signal emit | scanner called `create_position` directly | producer emits via `push_signal()` → runtime LLM gate |
| Scheduler | openclaw cron (3min) | `producer_daemon(interval_seconds=180)` |
| Reentrancy | (none) | daemon's `scanner_lock` (stale-PID auto-recovery) |
| Daily cap | Python `get_dynamic_daily_cap()` state | `runtime.yaml risk.guard_rails.max_entries_per_day` |
| Post-exit cooldown | `state/trade-counter.json` `last_entry_ts` | `runtime.yaml risk.guard_rails.per_asset_cooldown_minutes` |
| Exit fee | MARKET (taker, 0.045%) | FEE_OPTIMIZED_LIMIT (maker-first, 0.015%) |

NO change to hard gates, scoring components, MIN_SCORE, sizing tiers, leverage cap, or DSL preset.

## Key parameters

| Parameter | Value |
|---|---|
| Universe | Top 50 HL assets by 24h notional volume |
| Max positions | 1 |
| Tick interval | 180s (3 min) |
| MIN_SCORE (producer) | 12 |
| LLM min_confidence | 7 |
| Margin tiers | 50% (score 11-12) / 70% (13-14) / 80% (15+ APEX) |
| Leverage | 10x (auto-clamped to asset max) |
| Entry order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: false) |
| Exit order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: true) |
| Per-asset cooldown | 120 min |
| Daily entry cap | 1 |
| Daily loss limit | 15% |
| Drawdown halt | 25% |

## License

MIT — Built by Senpi (https://senpi.ai).
