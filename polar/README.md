# 🐻‍❄️ POLAR v5.0.0 — ETH Alpha Hunter (senpi_runtime_helpers)

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## What changed in v5.0.0

**Plumbing-only migration. NO thesis change.** v4.2.0's scoring tables, leverage tiers, MIN_SCORE 12, quiet hours, DSL preset are all preserved verbatim.

- `polar-producer.py` and `polar_config.py` migrate to `senpi_runtime_helpers`:
  - MCP calls go via `SenpiClient.mcp_call()` (direct HTTPS) instead of `mcporter` subprocess
  - Signal emission goes via `SenpiClient.push_signal()` (direct HTTP POST) instead of `openclaw senpi external-scanner ingest` subprocess
  - Reentrancy lock owned by `producer_daemon.scanner_lock` instead of hand-rolled `fcntl`
  - Tick scheduling owned by `producer_daemon` (long-lived process) instead of openclaw cron + `agentTurn`
- Requires `senpi-trading-runtime >= 2.0.0`.
- `runtime.yaml` unchanged. `external_scanner.name: polar_signals` matches the producer's `client.push_signal(scanner=...)`.
- Per Rachin's review of Cheetah PR #209: dead fields stripped from payload; `signal_type="POLAR_ETH_HYBRID"` passed explicitly.

## Thesis (preserved from v4.2.0)

ETH single-asset hybrid hunter. Hyperfeed Smart Money gates (pct≥5%, traders≥30, cc_15m≥0.3 acceleration), structural gates (4h trend != NEUTRAL, 4h-1h-15m alignment, RSI not extreme), multi-factor scoring (~17 max), conviction-tiered leverage (5x/7x/10x), MIN_SCORE 12 floor, FP-001 quiet hours (00-04 UTC unless apex score 17+).

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

Skip if already pulled for Cheetah / Turbine / Kodiak / another v3 skill.

### Step 2 — Pull Polar v5.0.0

```bash
mkdir -p /data/workspace/skills/polar-strategy/{config,scripts,state,references}
for f in scripts/polar-producer.py scripts/polar_config.py \
         SKILL.md README.md references/skill-attribution.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/polar/$f" \
    -o "/data/workspace/skills/polar-strategy/$f"
done
```

`runtime.yaml` is unchanged from v4.x — don't touch the existing runtime.

### Step 3 — Required env vars

```bash
export POLAR_WALLET_ADDRESS=<your-polar-wallet>   # NOT STRATEGY_ADDRESS
export SENPI_AUTH_TOKEN=...
export POLAR_DECISION_MODEL=gemini-3.1-pro-preview
```

### Step 4 — Stop the v4.x cron, start the v5.0.0 daemon

```bash
openclaw cron list | grep polar
openclaw cron delete <polar-cron-id>

# Start daemon (long-lived, no cron)
nohup python3 -u /data/workspace/skills/polar-strategy/scripts/polar-producer.py \
  > /tmp/polar-producer.log 2>&1 &
```

## Smoke test

```bash
tail -f /tmp/polar-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expected: `status=ok` every tick (180s interval).

## Configure

**Set wallet, strategy ID, and chat ID in `config/polar-config.json`** — this is the canonical source of truth. Producer reads from here on every cron tick; runtime reads from here at startup.

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
export POLAR_DECISION_MODEL=gemini-2.5-pro    # bare model name; NO provider prefix
```

## Install runtime + create producer cron

```bash
openclaw senpi runtime create --path /data/workspace/skills/polar-strategy/runtime.yaml
openclaw senpi runtime list
```

Add 3-minute cron (wallet read from config.json — no env vars needed):

```cron
*/3 * * * * cd /data/workspace/skills/polar-strategy && python3 scripts/polar-producer.py >> state/producer.log 2>&1
```

## Key parameters

| Parameter | Value |
|---|---|
| Asset | ETH (single-asset) |
| Max positions | 1 |
| Margin per slot | $500 |
| Leverage | 5x / 7x / 10x (score-tiered: 14 / 15 / 17+) |
| MIN_SCORE | 14 |
| LLM min_confidence | 7 |
| Per-asset cooldown | 240 min (4h) |
| Daily entry cap | 4 |
| Daily loss limit | 10% |
| Drawdown halt | 25% |
| Quiet hours | 00:00-04:00 UTC (apex score 17+ bypasses) |
| Entry order type | FEE_OPTIMIZED_LIMIT |
| Exit order type | FEE_OPTIMIZED_LIMIT |

## DSL Phase 2 ladder (preserved from v3.x)

ETH-tuned, leverage-aware. All time-based cuts disabled — exits are 100% price-action.

| Tier | Trigger (margin ROE) | Lock (% of HW) |
|---|---|---|
| T0 | +8% | 25% |
| T1 | +15% | 50% |
| T2 | +25% | 65% |
| T3 | +35% | 80% |
| T4 (apex) | +50% | 85% |

Phase 1: max_loss 25% / retrace 8% / 3 consecutive breaches.
**Time-cuts:** `hard_timeout` / `weak_peak_cut` / `dead_weight_cut` all DISABLED (v3.0.4/3.0.5/3.0.6 fixes preserved — v1 DSL fired hard_timeout in Phase 2 incorrectly per spec).

## Migrating from v3.x

```bash
cd /data/workspace/skills/polar-strategy
rm -f scripts/polar-scanner.py                       # replaced by polar-producer.py
# Pull the new files (curl commands above)
# Update cron: replace polar-scanner.py with polar-producer.py
# Reload runtime: openclaw senpi runtime delete <old-id>; openclaw senpi runtime create --path runtime.yaml
```

The runtime swap retains DSL state on any open position via venue-side stops — your live trade is not at risk during the upgrade. State files (`state/trade-counter.json`, `state/cooldowns.json`) are vestigial in v4.0 and can be deleted.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
