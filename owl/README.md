# 🦉 OWL v7.0 — Pure Contrarian Crowding-Unwind Hunter (v2-runtime-native)

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Wait for the crowd to overcommit. Wait for them to exhaust. Then eat their liquidations. **One thesis: the crowd is wrong.** Owl is the only Senpi agent that fades crowding — every other skill enters WITH momentum, WITH the trend, WITH smart money. The edge: crowded trades unwind violently and predictably.

## v7.0 architecture

| Layer | v6.x | v7.0 |
|---|---|---|
| Trading loop | Self-executing scanner + `create_position` | Producer pushes signals via `external-scanner ingest`; runtime owns execution |
| Entry gate | Scanner decides + executes | LLM pass-through gate (producer already filtered) |
| Entry order | FEE_OPTIMIZED_LIMIT (already maker-first) | Same — `ensure_execution_as_taker: false` preserved |
| Exit order | DSL + MARKET orders | DSL + **FEE_OPTIMIZED_LIMIT** (maker-first, 60s, taker fallback) |
| Risk gates | Scanner-side dynamicSlots + drawdown circuit breaker | Declarative `runtime.risk.guard_rails` + producer dynamic cap |
| State | `state/state.json` | `state/<wallet-hash>/` (multi-wallet safe) |

**Why v7 matters:** v6 entries were already maker-first, but v6 EXITS used MARKET orders (taker fees). v7 brings maker-first to exits — recovering ~50% of HL exit fees with no thesis change. Plus the v2 architectural wins: declarative risk, wallet isolation, fail-loud guards.

**Thesis preserved verbatim from v6.2:**
- Crowding score: funding extremity + SM tilt + OI concentration. Floor 6.
- Persistence: 1+ hour above floor (with 2-tick noise tolerance)
- Exhaustion: ≥ 2 distinct signals (volume decline / price stall / vol spike no follow / RSI divergence), score ≥ 5
- Combined score ≥ 12 to fire
- Universe: ALL crypto perps with OI > $3M (v6.1 expansion)
- Direction is OPPOSITE of crowd (the entire edge)

See [`SKILL.md`](SKILL.md) for full setup, env vars, and behavior expectations.

## License

Apache-2.0 — Built by Senpi (https://senpi.ai). Attribution required for derivative works.
