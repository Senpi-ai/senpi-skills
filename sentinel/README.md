# Sentinel — Quality-Trader Convergence Scanner

**Runtime:** 1.0  ·  **Asset:** Multi-asset  ·  **Version:** 2.2.0

## Thesis

Quality trader convergence scanner v2.2. Finds assets where multiple

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
Quality trader convergence scanner v2.2. Finds assets where multiple
ELITE/RELIABLE traders converge. v2.2 widens Phase 2 tiers
([5,10,15] → [15,30,50,75,100]) per Sentinel's own rec; 45% WR
confirms the signal is valid, DSL was bleeding value via slow cuts
on 17/23 losers.
```

## Related

- Top-level repo README: `../README.md`
- Runtime spec: `../senpi-trading-runtime/`
- DSL plugin: `../dsl-dynamic-stop-loss/`
