# Phoenix — Contribution Velocity Scanner

**Runtime:** 1.0 (schema mislabeled — agent semver in version field; flag)  ·  **Asset:** Multi-asset  ·  **Status:** Live  ·  **Version:** 3.0.0

## Thesis

Contribution velocity scanner. SM profit velocity diverging from price.

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
Contribution velocity scanner. SM profit velocity diverging from price.
High-conviction, low-frequency. v3.0 adopts the Lemon DSL profile:
removes weak_peak_cut (54% of losers were killed by it), extends
hard_timeout to 480m, widens Phase 2 tiers, loosens Phase 1.
```

## Related

- Top-level repo README: `../README.md`
- Runtime spec: `../senpi-trading-runtime/`
- DSL plugin: `../dsl-dynamic-stop-loss/`
