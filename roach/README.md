# 🪳 ROACH v2.0 — Striker Only. v2-Runtime-Native. Maker Exits.

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## The Strategy

ROACH disables Stalker entirely and only trades STRIKER signals — violent FIRST_JUMP / IMMEDIATE_MOVER explosions backed by 1.5x volume, 1h price alignment, and 4h trend agreement. Confirmed by Fox v1.0 data: 17 Stalker trades, 17.6% win rate, -$91 net; the one Striker (ZEC LONG score 11) was the only profitable explosive entry.

ROACH will be quiet. Days with zero trades are expected and correct. Striker signals require a 10+ rank jump from #25+, score >= 10 with 4+ reasons, cc_15m >= 0.5, 1h price aligned >= 0.1%, volume >= 1.5x. That's rare. The patience IS the edge.

## v2.0 architecture

| Layer | v1.x | v2.0 |
|---|---|---|
| Trading loop | Agent runs scanner + calls `create_position` | Producer pushes signals via `external-scanner ingest`; runtime owns execution |
| Entry gate | Agent decides | LLM pass-through gate (producer already filtered) |
| Exit | DSL + MARKET orders | DSL + **FEE_OPTIMIZED_LIMIT** (maker-first, 60s, taker fallback) |
| Risk gates | Agent enforces in scanner code | Declarative `runtime.risk.guard_rails` |

**Why v2 matters:** v1 used MARKET orders for every exit, paying ~3 bp/exit in HL taker fees. v2's maker-first exits target 50-70% recovery on HL exit fees with no thesis change.

See [`SKILL.md`](SKILL.md) for full setup, env vars, and behavior expectations.

## License

MIT — see root repo LICENSE.
