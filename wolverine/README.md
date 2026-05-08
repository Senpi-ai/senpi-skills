# 🦡 WOLVERINE v4.0.0 — HYPE Alpha Hunter (v2-runtime-native)

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## What changed in v4.0

- `wolverine-producer.py` (NEW) replaces `wolverine-scanner.py` (DELETED)
- v2-runtime-native: external_scanner + LLM-pass-through gate + native `risk.guard_rails`
- DSL exits via `FEE_OPTIMIZED_LIMIT` (saves ~0.020-0.030% per maker-filled close)
- Trade chain DB emits per-trade telemetry — chain DB visibility on Wolverine for the first time
- v3.0.3 six-gate entry validation preserved EXACTLY (incl. the 4h-magnitude fix that rejects dead-flat chop)
- v3.0.1/2/4 v1-DSL fixes preserved: time-cuts all disabled, exits 100% price-action

## Thesis (preserved from v3.x)

Single-asset HYPE alpha hunter. Six-gate entry validation: 4h trend structure, 4h structural strength, 1h confirmation, 15m momentum alignment, base-tech floor, **4h magnitude ≥1.5%** (the v3.0.3 fix). Multi-factor scoring (~17 max points), MIN_SCORE 9, conviction-tiered leverage (3x/5x). DSL Phase 2 trailing owns all winner exits.

## Install

```bash
mkdir -p /data/workspace/skills/wolverine-strategy/{config,scripts,state}

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/wolverine/runtime.yaml -o /data/workspace/skills/wolverine-strategy/runtime.yaml
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/wolverine/SKILL.md -o /data/workspace/skills/wolverine-strategy/SKILL.md
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/wolverine/config/wolverine-config.json -o /data/workspace/skills/wolverine-strategy/config/wolverine-config.json
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/wolverine/scripts/wolverine-producer.py -o /data/workspace/skills/wolverine-strategy/scripts/wolverine-producer.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/wolverine/scripts/wolverine_config.py -o /data/workspace/skills/wolverine-strategy/scripts/wolverine_config.py
```

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
