# 🐺 JACKAL v2.0 — The Smart Stalker (v2-native)

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

The fleet's first SECONDARY-SIGNAL agent AND the first built natively on `senpi-trading-runtime` v2. Observes top-performing Senpi perp traders, detects new entries by pool members, and lets a Claude Sonnet decision prompt decide whether to mirror with our own DSL + risk guardrails.

Not a passive mirror — an intelligent stalker where the runtime LLM gates every entry.

## Architecture

```
jackal-producer.py (60s cron)      senpi-trading-runtime (v2)
  refresh pool (daily)              jackal_signals scanner
  diff positions vs last-seen   →   jackal_entry action (LLM-gated)
  enrich + push signal              position_tracker + DSL
                                    risk.guard_rails
```

## Key Settings (v2)

| Setting | Value |
|---|---|
| Pool | Top 25 by composite quality score (refreshed daily) |
| Pool filters | win_rate ≥ 0.50, roi_30d ≥ 10%, trader_age ≥ 14d |
| Entry age gate | < 10 min (producer-side freshness) |
| Entry decision | Claude Sonnet 4 via `decision_prompt`, min_confidence 7 |
| Max concurrent | 2 slots |
| Leverage | 5x default (runtime-enforced) |
| Margin per slot | $300 |
| Daily loss cap | 5% |
| Max entries/day | 4 |
| Consecutive losers pause | 3 → 120 min cooldown |
| Drawdown halt | 20% |
| Per-asset cooldown | 240 min (4h) |
| DSL hard_timeout | 72h |
| DSL Phase 1 max_loss | 22% |

## What's different from v1.1

| | v1.1 | v2.0 |
|---|---|---|
| Scanner size | 760 lines | 400-line producer |
| Entry decision | Hardcoded score thresholds | LLM prompt |
| DSL attach | Manual `ratchet_stop_add` | Runtime auto-managed |
| Risk | Python constants | Declarative YAML |
| Pool | Two-tier watchlist + active | Single top-N from discovery API |

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
