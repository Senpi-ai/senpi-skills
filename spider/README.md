# 🕷️ SPIDER v3.0 — Patient Anchor Sniper (v2-runtime-native)

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## What changed in v3.0

- `spider-producer.py` (NEW) replaces the v2.0 simulated-runtime cron-prompt approach
- `spider-rationale-log.py` (DELETED) — chain DB telemetry replaces it
- v2-runtime-native: external_scanner + LLM-pass-through gate + native `risk.guard_rails` + standard `OPEN_POSITION` action
- DSL exits via `FEE_OPTIMIZED_LIMIT` (entries already on FEE_OPTIMIZED_LIMIT)
- Trade chain DB emits per-trade telemetry — chain DB visibility for the first time
- **Single-leg anchor only** — basket leg dropped (no native portfolio runtime support); revisit when `dsl_portfolio` ships
- All v2.0 anchor scoring logic preserved (arena 0.40 + SM 0.30 + funding 0.15 + relstr 0.15)

## Why the redesign

v2.0 was specced with custom runtime types (`composite_score`, `portfolio_snapshot`, `predators_position_aggregator`, `LLM_PORTFOLIO_DECISION`) that the senpi-trading-runtime doesn't implement. The runtime silently dropped the unsupported types; the operator agent simulated the missing engine via cron text prompts. **Spider v2.0 never made a real trade in 30+ days of "operation."**

v3.0 trade-off: drop the basket leg + portfolio-level coordination to use ONLY runtime primitives that exist today. Single high-conviction long anchor, 7-day minimum hold, fee-aware. The thesis (patience > frequency) survives intact.

## Thesis

Hold one high-conviction long anchor for at least 7 days. Generate edge from:

1. **Multi-day trend** — top SM markets that are also held by arena top-10 traders
2. **Positive carry** — prefer assets where funding favors longs
3. **Relative strength** — assets outperforming over 30d
4. **Fee-aware** — FEE_OPTIMIZED_LIMIT entries + exits, ~1 entry + 1 exit per trade

While 95% of the fleet churns daily on noise, Spider sits with a single position. **Patience is the edge.**

## Install

```bash
mkdir -p /data/workspace/skills/spider-strategy/{config,scripts,state}

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/spider/runtime.yaml -o /data/workspace/skills/spider-strategy/runtime.yaml
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/spider/SKILL.md -o /data/workspace/skills/spider-strategy/SKILL.md
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/spider/config/spider-config.json -o /data/workspace/skills/spider-strategy/config/spider-config.json
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/spider/scripts/spider-producer.py -o /data/workspace/skills/spider-strategy/scripts/spider-producer.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/spider/scripts/spider_config.py -o /data/workspace/skills/spider-strategy/scripts/spider_config.py
```

## Configure

**Set wallet, strategyId, chatId in `config/spider-config.json`** — canonical source. Producer reads from here on every cron tick.

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId",
  "minScore": 7.0
}
```

LLM model env var (only at runtime-create time):

```bash
export SPIDER_DECISION_MODEL=gemini-3.1-pro-preview    # bare model name; NO provider prefix
```

## Install runtime + producer cron

```bash
openclaw senpi runtime create --path /data/workspace/skills/spider-strategy/runtime.yaml
openclaw senpi runtime list
```

Cron (hourly, no env vars needed — wallet read from config). Spider's producer self-skips when riding an anchor with held_days < 7, so hourly cadence is safe and catches faster regime shifts than daily would.

```cron
0 * * * * cd /data/workspace/skills/spider-strategy && python3 scripts/spider-producer.py >> state/producer.log 2>&1
```

If you prefer the v2.0 daily 13:00 UTC cadence:

```cron
0 13 * * * cd /data/workspace/skills/spider-strategy && python3 scripts/spider-producer.py >> state/producer.log 2>&1
```

## Key parameters

| Parameter | Value |
|---|---|
| Universe | Top 15 SM leaderboard (XYZ banned, LONG-only) |
| Max positions | 1 (anchor only) |
| Margin per slot | $1000 (100% of equity, single anchor) |
| Leverage | 1x / 2x / 3x (score-tiered, capped at 3x) |
| **MIN_SCORE** | **7.0** |
| Min hold | 7 days |
| Post-close cooldown | 7 days (matches min-hold) |
| Daily entry cap | 2 |
| Daily loss limit | 12% |
| Drawdown halt | 25% |
| drawdown_reset_on_day_rollover | false |
| per_asset_cooldown | 10080 min (7d) |
| Entry order type | FEE_OPTIMIZED_LIMIT |
| Exit order type | FEE_OPTIMIZED_LIMIT |

## DSL Phase 2 ladder (v3.0 — patience-tuned, wider than active agents)

| Tier | Trigger (margin ROE) | Lock (% of HW) |
|---|---|---|
| T0 | +10% | 35% |
| T1 | +20% | 55% |
| T2 | +35% | 70% |
| T3 | +60% | 85% |
| T4 (apex) | +100% | 92% |

Phase 1: max_loss 12% / retrace 8 / 3 consecutive breaches.
Time cuts: hard_timeout 30d (43200 min) — fail-safe only. weak_peak_cut and dead_weight_cut DISABLED — patience agent.

## Migrating from v2.0

```bash
cd /data/workspace/skills/spider-strategy

# v2.0 had no real positions (it never traded). Safe to delete + recreate.
rm -f scripts/spider_rationale_log.py     # replaced by chain DB
# (curl commands above for new files)

# Reload runtime
openclaw senpi runtime delete <old runtime id>
openclaw senpi runtime create --path runtime.yaml

# Update cron to point at spider-producer.py
```

State files from v2.0 (`spider-log.jsonl`, `paper-positions.json`, etc.) are vestigial — chain DB owns telemetry now. Safe to archive or delete.

## What's deferred for later

- **Basket leg** — when the runtime ships `dsl_portfolio` and supports multi-leg coordinated entry/exit, basket-of-shorts can be re-added. For now, anchor only.
- **Fleet concentration overlay** — leverage modifier from peer-position aggregator. Producer can simulate this via `discovery_get_top_strategies`, but skipped in v3.0 to ship.
- **Funding harvest from coordinated shorts** — partially captured via the funding-favorability score component on the anchor.

## Operator checklist for the first 7 days

- **Day 1:** confirm `state/producer.log` accumulating one JSON-per-tick. Look for `_spider_producer_version: "3.0.0"` in output.
- **Day 1-2:** if anchor opens, verify chain DB emits CREATED + DECISION + ACTION_RESULT events (via `trade_chains_get`)
- **Day 2-7:** producer should output `note: "riding anchor day X.Y/7 min hold"` on every tick. No new emissions.
- **Day 7+:** anchor can close via DSL Phase 1 max_loss OR Phase 2 trailing. After close, 7-day post-close cooldown blocks re-entry on same asset.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
