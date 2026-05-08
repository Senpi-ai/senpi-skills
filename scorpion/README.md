# 🦂 SCORPION v4.0 — Multi-Market Active Trader (v2-native)

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

The only fleet predator that hunts across BOTH crypto and XYZ DEX (commodities / indices). SM concentration + 4H price trend alignment gates the multi-factor score. v4.0 is the second fleet agent built natively on `senpi-trading-runtime` v2 (after Jackal v2) — producer emits signals, runtime LLM gates every entry, risk guardrails enforced declaratively, DSL uses maker-preferred exits.

## Architecture

```
scorpion-producer.py (60s cron)           senpi-trading-runtime (v2)
  score all crypto + XYZ markets           scorpion_signals scanner
  emit candidates at score >= 9       →    scorpion_entry action (LLM-gated)
  enrich w/ BTC macro + funding +          position_tracker + DSL
    current positions                      risk.guard_rails
                                           exit: FEE_OPTIMIZED_LIMIT
```

## Why v4.0

v3.2 logged 43 fills / 18h / -$79.84 in Arena Week 5 despite `MAX_DAILY_ENTRIES=3` in code. The scalp-reentry bypass path and in-Python trade counter were silently leaking. v4.0 removes all that bookkeeping:

- **Producer has no execution authority.** No create_position, no trade counters, no cooldown state.
- **Runtime enforces max_entries_per_day: 5 via `risk.guard_rails`.** No bypass path.
- **LLM gates every entry.** ~30-40% expected pass rate at min_confidence 7.
- **DSL uses FEE_OPTIMIZED_LIMIT on exits** (the big v2 win). At ~40 trades/day pre-gating, saves ~$20/week in fee drag.

## Key Settings (v4)

| Setting | Value |
|---|---|
| Universe | 15 crypto + 4 XYZ (CL, BRENTOIL, GOLD, SPX) |
| Entry signal gate | MIN_SCORE ≥ 9 (producer-level) |
| Entry decision | LLM-gated via `decision_prompt`, min_confidence 7 |
| Decision model | Required via `$SCORPION_DECISION_MODEL` env var — no default |
| Max concurrent | 2 slots |
| Margin per slot | $250 |
| Max entries/day | 5 (runtime-enforced, no bypass) |
| Per-asset cooldown | 120 min (runtime-enforced) |
| Daily loss cap | 5% |
| Consecutive loss pause | 3 → 90 min cooldown |
| Drawdown halt | 20% |
| DSL exit order type | **FEE_OPTIMIZED_LIMIT** (maker-first, taker fallback) |
| DSL hard_timeout | 12h (time cuts auto-disable in Phase 2 per v2 spec) |
| DSL Phase 1 max_loss | 15% |

## What's different from v3.2

| | v3.2 | v4.0 |
|---|---|---|
| Scanner size | 549 lines | 280-line producer |
| Entry decision | Hardcoded thresholds | LLM decision prompt |
| Daily counter | Python state file (leaked to 43/18h) | Runtime `risk.guard_rails` |
| Scalp re-entry | Special bypass code | Removed; `per_asset_cooldown_minutes` authoritative |
| DSL exit fees | Taker (market orders) | **Maker-preferred with taker fallback** |
| Phase-2 time-cut bug | Fires inappropriately | Auto-disabled in Phase 2 by v2 spec |

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
