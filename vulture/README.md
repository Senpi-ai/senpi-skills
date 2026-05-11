# Vulture — Arena-Winner Template Clone

**Runtime:** 2.0 (capable)  ·  **Asset:** XYZ markets  ·  **Status:** Live  ·  **Version:** 1.0.1

## Thesis

VULTURE v3.1.1 — observability fix: full ingest-failure logging.

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
VULTURE v3.1.1 — observability fix: full ingest-failure logging.
2026-05-02 → 2026-05-06 dormancy was caused by a 4-day stuck producer
lockfile combined with silent INGEST_FAILED errors (openclaw exited
non-zero with empty stderr; producer only logged stderr, so the actual
failure cause was invisible). v3.1.1 widens the log on ingest failure
to capture returncode + stderr + stdout + the rejected payload, so any
future ingest issue is self-diagnosing from the producer log alone.
Same widening applied to INGEST_REJECTED (schema validator) and
INGEST_EXCEPTION (Python errors). No scoring or DSL changes.
```

## Related

- Top-level repo README: `../README.md`
- Runtime spec: `../senpi-trading-runtime/`
- DSL plugin: `../dsl-dynamic-stop-loss/`
