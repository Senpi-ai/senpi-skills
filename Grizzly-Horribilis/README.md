# Grizzly Horribilis — BTC Contrarian Sniper

**Runtime:** 1.0  ·  **Asset:** BTC  ·  **Version:** 2.1.0

## Thesis

BTC Contrarian Sniper v2.1 — sniper recalibration. Fades exhausted SM

See `SKILL.md` and `runtime.yaml` for full scoring components, gates, and DSL configuration.

## Scoring components

Defined in the producer/scanner. See `runtime.yaml` `scanner:` section and the producer script in this folder for the current scoring weights and gates.

## Entry / Exit

- **Entry:** Producer-emitted signals scored against MIN_SCORE gate.
- **Exit:** DSL (Dynamic Stop-Loss) Phase 1 + Phase 2 trailing exits per `runtime.yaml` `dsl:` section.
- **Time cuts:** Disabled per single-asset rule.

## Fleet rules applied

- Standard fleet drawdown gate.
- Fee budget tracking via shared infra.
- Producer reentrancy guard via fcntl lockfile (Runtime 2.0 only).

## Configuration

Operator-specific values configured at deploy time.

## Recent version notes

```
BTC Contrarian Sniper v2.1 — sniper recalibration. Fades exhausted SM
consensus moves on BTC. v2.1: MIN_SCORE raised 8→10 (Cheetah v5.1 APEX
pattern), MIN_EXHAUSTION_PCT raised 2.5→4.5 (Dog v2.2 fix — stops
fighting fresh trends), pyramid scale-up bar raised 9→12 (apex only).
7x at score 10-11, 10x at score 12+. 50% margin per trade.
```

## Related

- Top-level repo README: `../README.md`
- Runtime spec: `../senpi-trading-runtime/`
- DSL plugin: `../dsl-dynamic-stop-loss/`
