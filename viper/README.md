# Viper — Range-Bound Liquidity Sniper

**Runtime:** 2.0  ·  **Asset:** Multi-asset  ·  **Status:** Live  ·  **Version:** 2.2.0

## Thesis

VIPER v2.2 — Range-Bound Liquidity Sniper (DSL loosen for winners).

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
- Producer reentrancy guard via fcntl lockfile (Runtime 2.0 only).

## Configuration

Operator-specific values configured at deploy time.

## Recent version notes

```
VIPER v2.2 — Range-Bound Liquidity Sniper (DSL loosen for winners).
v2.1 shipped with 15/20 min dead_weight/weak_peak cuts and 4h
hard_timeout. Result: 240 fills / -19.9% ROI. The "range trades
resolve fast" assumption cut winners before they could develop. v2.2
doubles the timing windows: 30/45/480. Phase 1 stays tight since
range-fader stops should be close to entry. Phase 2 unchanged.
```

## Related

- Top-level repo README: `../README.md`
- Runtime spec: `../senpi-trading-runtime/`
- DSL plugin: `../dsl-dynamic-stop-loss/`
