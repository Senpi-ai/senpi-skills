# 🐻‍❄️ POLAR v2.5.0 — ETH Alpha Hunter (asymmetry-optimized)

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Single-asset ETH alpha hunter. Hyperfeed primary direction (SM concentration) + structural-veto gates from candle data + multi-factor scoring + conviction-tiered leverage. v3.0's structural-veto thesis preserved — entries require 4h direction to match SM direction (the v3.0 anti-chop gate).

## v2.5.0 architecture

| Layer | v3.0.6 | v2.5.0 |
|---|---|---|
| Trading loop | Scanner + `create_position` | Producer pushes signals via `external-scanner ingest`; runtime owns execution |
| MIN_SCORE | 14 (16 trades / 10 days = too few) | **12** (between v2.4's 10 and v3.0's 14) |
| Exit order | DSL + MARKET orders | DSL + **FEE_OPTIMIZED_LIMIT** (maker-first, ~$0.40 per close vs $1.40+) |
| Phase 1 | max_loss 25% / retrace 8% / breaches 3 | **max_loss 15% / retrace 5% / breaches 2** — cut losers FAST/CHEAP |
| Phase 2 | tiers 8/25, 15/50, 25/65, 35/80, 50/85 | **10/20, 18/40, 30/60, 45/75, 75/88** — RIDE WINNERS HARD |
| Risk | Agent enforces in scanner | Declarative `runtime.risk.guard_rails` |

**Naming note:** v2.5 (not v3.x). v3.0's diagnostic was correct — v2.4's 32% WR + 'fee drag dominated' was the real issue. But v3.0 over-corrected with MIN_SCORE 14 + over-strict gates → 16 trades / 10 days / -$72. v2.5 keeps v3.0's structural veto, lowers MIN_SCORE to 12, and pairs with maker-exit fees + asymmetry-optimized DSL.

## The asymmetry math

At 5x leverage: per-loser cost ≈ -$15 (3% price stop + $0.40 maker fee). Per Phase 2 tier 3+ winner ≈ +$50-150. **32% WR × $30 avg winner − 0.68 × $15 = +$11.84/trade expectancy.**

## Files

| File | Purpose |
|---|---|
| `runtime.yaml` | v2 runtime spec |
| `scripts/polar-producer.py` | Cron-driven producer (3 min cadence) |
| `scripts/polar_config.py` | Shared MCP helper + atomic state I/O |
| `config/polar-config.json` | Operator-tunable defaults |

See [`SKILL.md`](SKILL.md) for full setup, env vars, and behavior expectations.

## License

MIT — Built by Senpi (https://senpi.ai).
