# Python — Patient Multi-Asset Scanner

**Runtime:** 1.0  ·  **Asset:** Multi-asset  ·  **Version:** 1.2.0

## Thesis

PYTHON v1.2 — disable weak_peak_cut (completes patience thesis fix).

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
PYTHON v1.2 — disable weak_peak_cut (completes patience thesis fix).
v1.1 loosened Phase 1 retrace 10→30 and extended time cuts after
Python's self-diagnostic showed trades averaging <4h despite a 2-4
day thesis target. v1.2 removes weak_peak_cut entirely — a
multi-day patience thesis cannot tolerate ANY time-based cut. A
position peaking at 2% ROE on day 1 of a 3-day thesis can still
develop into a monster over day 3. Phase 1 retrace (30%) + max_loss
(20%) + Phase 2 tiers own all exits. dead_weight_cut + hard_timeout
(96h) preserved — 96h matches thesis target as an outer bound.
Scanner unchanged. v1.1 retrace/dead_weight loosening preserved.
```

## Related

- Top-level repo README: `../README.md`
- Runtime spec: `../senpi-trading-runtime/`
- DSL plugin: `../dsl-dynamic-stop-loss/`
