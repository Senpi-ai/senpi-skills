# 🦎 Mantis v6.0.0 — Slipstream (senpi_runtime_helpers)

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

**Plumbing-only migration from v5.0. NO thesis change.** v5.0 entry filters, confidence-tier sizing, dynamic hard_timeout, and leader-reversal veto logic preserved verbatim. Scanner flips to in-process `SenpiClient`; daemon replaces the v5.0 openclaw cron. Leader-reversal veto **now actually closes positions** (v5.0 was a silent no-op).

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

### Step 2 — Pull Mantis v6.0.0

```bash
mkdir -p /data/workspace/skills/mantis-strategy/{config,scripts,state,references}
for f in scripts/mantis-producer.py scripts/mantis_config.py scripts/mantis_state.py \
         runtime.yaml SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/mantis/$f" \
    -o "/data/workspace/skills/mantis-strategy/$f"
done

# Pull config example only if you don't have one yet:
test -f /data/workspace/skills/mantis-strategy/config/mantis-config.json || \
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/mantis/config/mantis-config.example.json" \
    -o "/data/workspace/skills/mantis-strategy/config/mantis-config.json"
```

### Step 3 — Configure wallet, strategy ID, chat ID

Edit `/data/workspace/skills/mantis-strategy/config/mantis-config.json`:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId"
}
```

### Step 4 — Required env vars

```bash
# Per fleet v2.0.9 rule: per-agent wallet env var (optional override;
# config.json is the canonical source).
export MANTIS_WALLET=<your-mantis-wallet>

export SENPI_AUTH_TOKEN=...                            # required
export MANTIS_DECISION_MODEL=<your-preferred-model>            # bare model name; NO provider prefix
```

### Step 5 — Recreate the runtime + start the daemon

If you have a v5.0 runtime installed, delete it first — v6.0 introduces new scanner/action blocks that require a fresh runtime create.

```bash
openclaw senpi runtime list | grep mantis
openclaw senpi runtime delete <old-mantis-runtime-id>

openclaw senpi runtime create --path /data/workspace/skills/mantis-strategy/runtime.yaml
openclaw senpi runtime list
```

If you were running the v5.0 cron, stop it before launching the v6.0 daemon:

```bash
openclaw cron list | grep mantis
openclaw cron delete <mantis-cron-id>

nohup python3 -u /data/workspace/skills/mantis-strategy/scripts/mantis-producer.py \
  > /tmp/mantis-producer.log 2>&1 &
```

After first launch, manage the daemon via the `senpi-helpers` CLI: `senpi-helpers list`, `senpi-helpers health <name>`, `senpi-helpers restart <name>`.

## Smoke test

```bash
tail -f /tmp/mantis-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expected: `status=ok` every tick (60s interval).

---

## Thesis (preserved from v5.0)

Cross-asset catchup hunter. When BTC (or another leader) makes a significant 4h move and a correlated alt hasn't responded yet, Mantis strikes the alt before the catchup completes. Trades the statistical lag, not the momentum itself. Hard veto if the leader reverses mid-position.

## What changed in v6.0 vs v5.0

| Layer | v5.0 | v6.0 |
|---|---|---|
| MCP transport | `mcporter` subprocess (cold-start 2.5-5s) | `SenpiClient` direct HTTPS (~280ms) |
| Signal emit | scanner called `create_position` directly | producer emits via `push_signal()` → runtime LLM gate |
| Scheduler | openclaw cron (60s) | `producer_daemon(interval_seconds=60)` |
| Reentrancy | (none) | daemon's `scanner_lock` (stale-PID auto-recovery) |
| Daily cap | Python state files | `runtime.yaml risk.guard_rails.max_entries_per_day` |
| Per-asset cooldown | `state/asset-cooldowns.json` | `runtime.yaml risk.guard_rails.per_asset_cooldown_minutes` |
| Exit fee | MARKET (taker, 0.045%) | FEE_OPTIMIZED_LIMIT (maker-first, 0.015%) |
| **Leader-reversal veto** | emitted JSON; nothing consumed it (silent no-op) | direct `close_position` call from producer (now works) |
| Telemetry | scanner stdout JSON only | runtime audit_query + DSL events + entry-log.jsonl |

NO change to entry filters (`follow_rate >= 0.85`, `confidence >= 0.75`, `|gap| >= 1.5%`, SM rotation, `lag_stddev <= 90`), sizing tiers, dynamic hard_timeout, leader-reversal threshold, or DSL preset.

## Key parameters

| Parameter | Value |
|---|---|
| Leader universe | BTC (only one with pre-computed lag data) |
| Max positions | 2 |
| Tick interval | 60s |
| Min follow_rate | 0.85 |
| Min confidence | 0.75 |
| Min gap | 1.5% (abs) |
| Max lag stddev | 90 min |
| Confidence tiers | 0.92 → 75%/8x · 0.85 → 50%/7x · 0.75 → 25%/5x |
| Max leverage | 8x |
| Hard timeout (dynamic) | `avg_lag × 1.5`, clamped [30, 240] min |
| Leader-reversal threshold | 1.0% |
| Daily entry cap | 6 |
| Per-asset cooldown | 240 min |
| Daily loss limit | 10% |
| Drawdown halt | 20% |

## State files retained in v6.0

- `state/position-metadata.json` — per-position `leader_pct_at_entry` (required for veto). The runtime cannot express "close if a separate asset's price reverses by X%."
- `state/entry-log.jsonl` — observability only (no longer used for daily-cap counting; runtime guard_rails enforce that).

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
