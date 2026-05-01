# 🐻‍❄️ POLAR v4.0.0 — ETH Alpha Hunter (v2-runtime-native)

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## What changed in v4.0

- `polar-producer.py` (NEW) replaces `polar-scanner.py` (DELETED)
- v2-runtime-native: external_scanner + LLM-pass-through gate + native `risk.guard_rails`
- DSL exits via `FEE_OPTIMIZED_LIMIT` (saves ~0.020-0.030% per maker-filled close)
- Trade chain DB emits `LIFECYCLE_RUNTIME_STARTED → DECISION_EXECUTED → ACTION_RESULT → DSL_CREATED → DSL_CLOSED` for every trade — per-trade telemetry restored
- v3.x scoring + DSL preset preserved EXACTLY (proved correct on 2026-04-23 ETH Short locking +$71.15 via Phase 2; current live ETH LONG running +$54 unrealized at +5% margin ROE)
- The Python-state-crash class of bug from v3.x (`load_tc` / `set_cooldown` / `has_resting_orders`) is structurally impossible in v4.0 (state owned by runtime)

## Thesis (preserved from v3.x)

Single-asset ETH lifecycle hunter. Scores ETH using Hyperfeed Smart Money concentration, multi-timeframe trend structure, candle-based confirmation, and funding/OI/BTC-correlation context. Enters on high conviction (MIN_SCORE 14, scoring max ~17), lets DSL manage all exits via Phase 2 trailing.

## Install

```bash
mkdir -p /data/workspace/skills/polar-strategy/{config,scripts,state}

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/polar/runtime.yaml -o /data/workspace/skills/polar-strategy/runtime.yaml
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/polar/SKILL.md -o /data/workspace/skills/polar-strategy/SKILL.md
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/polar/config/polar-config.json -o /data/workspace/skills/polar-strategy/config/polar-config.json
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/polar/scripts/polar-producer.py -o /data/workspace/skills/polar-strategy/scripts/polar-producer.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/polar/scripts/polar_config.py -o /data/workspace/skills/polar-strategy/scripts/polar_config.py
```

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
