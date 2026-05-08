# 🦡 WOLVERINE v5.0.0 — HYPE Alpha Hunter (senpi_runtime_helpers)

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## What changed in v5.0.0

**Plumbing-only migration. NO thesis change.** v4.2.0's six-gate validation, scoring, leverage tiers, MIN_SCORE 9, quiet hours, DSL preset are all preserved verbatim.

- `wolverine-producer.py` and `wolverine_config.py` migrate to `senpi_runtime_helpers`:
  - MCP calls go via `SenpiClient.mcp_call()` (direct HTTPS) instead of `mcporter` subprocess
  - Signal emission goes via `SenpiClient.push_signal()` (direct HTTP POST) instead of `openclaw senpi external-scanner ingest` subprocess
  - Reentrancy lock owned by `producer_daemon.scanner_lock` instead of hand-rolled `fcntl`
  - Tick scheduling owned by `producer_daemon` (long-lived process) instead of openclaw cron + `agentTurn`
- Requires `senpi-trading-runtime >= 2.0.0`.
- `runtime.yaml` unchanged. `external_scanner.name: wolverine_signals` matches the producer's `client.push_signal(scanner=...)`.
- Per Rachin's review of Cheetah PR #209: dead fields stripped from payload; `signal_type="WOLVERINE_HYPE_HYBRID"` passed explicitly.

## Thesis (preserved from v4.2.0)

Single-asset HYPE alpha hunter. Six-gate entry validation: 4h trend structure, 4h structural strength ≥0.65, 1h-4h alignment, 15m momentum ≥0.15, base-tech floor, **4h magnitude ≥1.0%**. Multi-factor scoring (~17 max points), MIN_SCORE 9, conviction-tiered leverage (3x standard / 5x apex), FP-001 quiet hours. DSL Phase 2 trailing owns all winner exits.

## Install

### Step 1 — Pull the helpers package (one-time per host)

> **Note:** The `_helpers/senpi_runtime_helpers/` package is currently only on the `helper-mcp-envelope-aligned` branch — it has not yet landed on `main`. Pull from that branch until it does. Every other file in this skill is on `main` as normal.

```bash
mkdir -p /data/workspace/skills/_helpers/senpi_runtime_helpers
for f in __init__.py _config.py _logging.py cache.py client.py \
         daemon.py lock.py parallel.py SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/helper-mcp-envelope-aligned/_helpers/senpi_runtime_helpers/$f" \
    -o "/data/workspace/skills/_helpers/senpi_runtime_helpers/$f"
done
```

Skip if already pulled for Cheetah / Turbine / Kodiak / Polar / another v3 skill.

### Step 2 — Pull Wolverine v5.0.0

```bash
mkdir -p /data/workspace/skills/wolverine-strategy/{config,scripts,state,references}
for f in scripts/wolverine-producer.py scripts/wolverine_config.py \
         SKILL.md README.md references/skill-attribution.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/wolverine/$f" \
    -o "/data/workspace/skills/wolverine-strategy/$f"
done
```

`runtime.yaml` is unchanged from v4.x — don't touch the existing runtime.

### Step 3 — Required env vars

```bash
export WOLVERINE_WALLET_ADDRESS=<your-wolverine-wallet>
export SENPI_AUTH_TOKEN=...
export WOLVERINE_DECISION_MODEL=gemini-3.1-pro-preview
```

### Step 4 — Stop the v4.x cron, start the v5.0.0 daemon

```bash
openclaw cron list | grep wolverine
openclaw cron delete <wolverine-cron-id>

# Start daemon (long-lived, no cron)
nohup python3 -u /data/workspace/skills/wolverine-strategy/scripts/wolverine-producer.py \
  > /tmp/wolverine-producer.log 2>&1 &
```

## Smoke test

```bash
tail -f /tmp/wolverine-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expected: `status=ok` every tick (180s interval). Tick `duration_ms` should drop from ~30-60s (v4.x mcporter) to ~1-3s.

## Configure

**Set wallet, strategyId, chatId in `config/wolverine-config.json`** — canonical source. Producer reads from here on every cron tick.

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
export WOLVERINE_DECISION_MODEL=gemini-2.5-pro    # bare model name; NO provider prefix
```

## Install runtime + producer cron

```bash
openclaw senpi runtime create --path /data/workspace/skills/wolverine-strategy/runtime.yaml
openclaw senpi runtime list
```

Cron (3-min cadence, no env vars needed — wallet read from config):

```cron
*/3 * * * * cd /data/workspace/skills/wolverine-strategy && python3 scripts/wolverine-producer.py >> state/producer.log 2>&1
```

## Key parameters

| Parameter | Value |
|---|---|
| Asset | HYPE (single-asset) |
| Max positions | 1 |
| Margin per slot | $250 |
| Leverage | 3x / 5x (score-tiered: 9 / 11+) |
| MIN_SCORE | 9 |
| LLM min_confidence | 7 |
| Per-asset cooldown | 120 min (2h) |
| Daily entry cap | 4 |
| Daily loss limit | 10% |
| Drawdown halt | 25% |
| Quiet hours | 00:00-04:00 UTC (apex score 11+ bypasses) |
| Entry order type | FEE_OPTIMIZED_LIMIT |
| Exit order type | FEE_OPTIMIZED_LIMIT |

## DSL Phase 2 ladder (preserved from v3.0.4)

HYPE-tuned. All time-based cuts disabled — exits 100% price-action.

| Tier | Trigger (margin ROE) | Lock (% of HW) |
|---|---|---|
| T0 | +10% | 15% |
| T1 | +20% | 35% |
| T2 | +35% | 55% |
| T3 | +55% | 70% |
| T4 (apex) | +80% | 85% |

Phase 1: max_loss 20% / retrace 8% / 3 consecutive breaches.
**Time-cuts:** `hard_timeout` / `weak_peak_cut` / `dead_weight_cut` all DISABLED (v3.0.1/3.0.2/3.0.4 fixes preserved).

## Migrating from v3.x

```bash
cd /data/workspace/skills/wolverine-strategy
rm -f scripts/wolverine-scanner.py                    # replaced by wolverine-producer.py
# Pull new files (curl above)
# Update cron: replace wolverine-scanner.py with wolverine-producer.py
# Reload runtime: openclaw senpi runtime delete <old>; openclaw senpi runtime create --path runtime.yaml
```

State files (`state/trade-counter.json`, `state/cooldowns.json`) are vestigial in v4.0 and can be deleted.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
