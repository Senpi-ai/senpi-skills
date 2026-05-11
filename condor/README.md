# Condor — High-Conviction Momentum Hunter

**Runtime:** 1.0  ·  **Asset:** Multi-asset  ·  **Status:** Live  ·  **Version:** 1.3.4

## Thesis

CONDOR v3.4 — gate calibration fix (2026-05-05). v3.2's tightening

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
CONDOR v3.4 — gate calibration fix (2026-05-05). v3.2's tightening
(MIN_SCORE 11→13, MIN_SM 65%→75%) over-corrected. Account flatlined
at $1001.28 across 13 days post-deploy (2026-04-22 → 2026-05-05) —
zero trades, zero PnL change, vlm metric stuck at 213k from the
v3.0/v3.1 era. The gate combination required basically every scoring
lane to fire simultaneously, including the rare 15m_spike (c15m>=2.0).
v3.4 splits the difference: MIN_SCORE 13→12 (achievable without
rare 15m_spike lane), MIN_SM 75→70 (mid-stage moves now scoreable),
MIN_15M_VELOCITY 0.2→0.1 (was silent killer in 3TF structural gate).
Other gates preserved: MACRO_TREND_GATE 10% (Wolverine's lesson),
trader_count >= 50, OI >= $1M, MAX_LEVERAGE 10, position sizing
tiers, DSL preset (v3.3 weak_peak_cut disabled). Architecture
unchanged (still v1 full-agency); v4.0 producer migration on the
queue pending v3.4 calibration validation.
```

## Related

- Top-level repo README: `../README.md`
- Runtime spec: `../senpi-trading-runtime/`
- DSL plugin: `../dsl-dynamic-stop-loss/`
