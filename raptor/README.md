# Raptor — Hot Streak Follower

**Runtime:** 1.0  ·  **Asset:** Multi-asset  ·  **Version:** 3.3.0

## Thesis

RAPTOR v3.3 — Hot Streak Follower (entry-price discipline tightened).

See `SKILL.md` and `runtime.yaml` for full scoring components, gates, and DSL configuration.

## Scoring components

Defined in the producer/scanner. See `runtime.yaml` `scanner:` section and the producer script in this folder for the current scoring weights and gates.

## Entry / Exit

- **Entry:** Producer-emitted signals scored against MIN_SCORE gate.
- **Exit:** DSL (Dynamic Stop-Loss) Phase 1 + Phase 2 trailing exits per `runtime.yaml` `dsl:` section.
- **Time cuts:** See `dsl:` config in runtime.yaml.

## Fleet rules applied

- Standard fleet drawdown gate.
- Fee budget tracking via shared infra.
- Producer reentrancy guard via fcntl lockfile.

## Configuration

Operator-specific values configured at deploy time.

## Recent version notes

```
RAPTOR v3.3 — Hot Streak Follower (entry-price discipline tightened).
v3.2 introduced whale-entry-price gate at 20% threshold. Raptor
self-diagnosed 2026-04-23: the gate was still too loose. Scanner was
re-firing on whales already deep in profit, taking all downside risk
with none of their PnL cushion. v3.3 tightens
MAX_PRICE_RUN_PCT_FROM_WHALE_ENTRY from 20% to 5% — 4x tighter, still
leaves room for "whale in early profit" piggyback. Raptor's proposed
1-2% would likely zero the scanner; 5% is the first tightening with
room for further per data. Scanner-only change. DSL unchanged.
```

## Related

- Top-level repo README: `../README.md`
- Runtime spec: `../senpi-trading-runtime/`
- DSL plugin: `../dsl-dynamic-stop-loss/`
