# Orca — Gen-2 Striker (FIRST_JUMP)

**Runtime:** 1.0  ·  **Asset:** Multi-asset  ·  **Version:** 1.0.0

## Thesis

Gen-2 Striker — FIRST_JUMP detection enhanced with momentum event

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
Gen-2 Striker — FIRST_JUMP detection enhanced with momentum event
quality confirmation. Only enters when quality traders (ELITE/RELIABLE)
are driving the move. Fast-cycling DSL.
```

## Related

- Top-level repo README: `../README.md`
- Runtime spec: `../senpi-trading-runtime/`
- DSL plugin: `../dsl-dynamic-stop-loss/`
