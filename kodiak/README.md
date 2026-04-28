# 🐻 KODIAK v6.0 — SOL Alpha Hunter (asymmetry-optimized)

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

SOL-only single-asset alpha hunter. Hyperfeed primary direction + structural-veto from candle data + multi-factor scoring + conviction-tiered leverage. v5.1's base-tech-score floor preserved — entries require 4h/1h/15m alignment AND SM agreement.

## v6.0 architecture

| Layer | v5.1 | v6.0 |
|---|---|---|
| Trading loop | Scanner runs + calls `create_position` | Producer pushes signals via `external-scanner ingest`; runtime owns execution |
| Default leverage | **10x** (drove -17.8% / -$178 over 26 trades) | **5x** (the key thesis fix) |
| Exit order | DSL + MARKET orders | DSL + **FEE_OPTIMIZED_LIMIT** (maker-first, ~$0.40 per close vs $1.40+) |
| Phase 1 | max_loss 25% / retrace 8% / breaches 3 | **max_loss 15% / retrace 5% / breaches 2** — cut losers FAST/CHEAP |
| Phase 2 | tiers 8/25, 15/50, 25/65, 35/80, 50/85 | **10/20, 18/40, 30/60, 45/75, 75/88** — RIDE WINNERS HARD |
| Risk | Agent enforces in scanner | Declarative `runtime.risk.guard_rails` |

**Why v6:** v5.1 ran 10x SOL = -$178 over 26 trades. Diagnostic: 10x on single-asset SOL chop produced loss-biased per-trade variance. Same scanner logic at 5x has half the variance. Combined with maker-exit fee recovery + asymmetry-optimized DSL, the 32% WR pattern that bled at v5.1 should produce positive expectancy at v6.

## The asymmetry math

At 5x lev with v6.0 DSL: per-loser cost ≈ -$15 (3% price stop + $0.40 maker fee). Per Phase 2 tier 3+ winner ≈ +$50-150. **32% WR × $30 avg winner − 0.68 × $15 = +$11.84/trade expectancy.** v5.1's 10x leverage made losers $30+ each — same WR became net negative.

## Files

| File | Purpose |
|---|---|
| `runtime.yaml` | v2 runtime spec |
| `scripts/kodiak-producer.py` | Cron-driven producer (3 min cadence) |
| `scripts/kodiak_config.py` | Shared MCP helper + atomic state I/O |
| `config/kodiak-config.json` | Operator-tunable defaults |

See [`SKILL.md`](SKILL.md) for full setup, env vars, and behavior expectations.

## License

MIT — Built by Senpi (https://senpi.ai).
