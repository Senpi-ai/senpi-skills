# Bison — Conviction Holder

**Runtime:** 1.0  ·  **Asset:** Multi-asset  ·  **Status:** Live  ·  **Version:** 1.0.1

## Thesis

BISON v2.1 — Conviction Holder (asset whitelist + conviction floor +

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
BISON v2.1 — Conviction Holder (asset whitelist + conviction floor +
time-cuts disabled).
v2.1 (2026-05-07): scanner ports operator-diagnosed fixes:
  - Asset whitelist (BTC/ETH/SOL); was top-10-by-volume which let
    small-caps like ZEC consume the daily slot
  - minScore 8 → 11; was firing on first mediocre setup post-midnight,
    now demands real conviction
Runtime DSL preset (this file): time-cuts ALL DISABLED per fleet
pattern reference_dsl_time_cuts_single_asset.md. v1 DSL fires
hard_timeout incorrectly in Phase 2; weak_peak_cut stays as the only
time-based cut (self-limiting). Phase 1 max_loss + Phase 2 ratchet
ladder own all exits via price action. Phase 2 wide-by-design
(T0 trigger 10%/0% lock through T5 100%/85% lock) for conviction
breathing room.
```

## Related

- Top-level repo README: `../README.md`
- Runtime spec: `../senpi-trading-runtime/`
- DSL plugin: `../dsl-dynamic-stop-loss/`
