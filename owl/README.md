# Owl — Pure Contrarian + Macro Gate

**Runtime:** 2.0 (capable)  ·  **Asset:** XYZ markets  ·  **Status:** Live  ·  **Version:** 1.7.1

## Thesis

OWL v7.1 — Pure Contrarian + macro trend gate (2026-05-06). v7.0 architectural rewrite (Apr 28) didn't change the thesis — same

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
OWL v7.1 — Pure Contrarian + macro trend gate (2026-05-06).
v7.0 architectural rewrite (Apr 28) didn't change the thesis — same
contrarian unwind logic from v6.2. Empirically lost -$63 additional
Apr 28 → May 6 (lifetime -$203 / $187k notional / -0.108% per round
trip; below maker-fee breakeven). Same disease as Lemon: fade thesis
fails in trending regimes. v7.1 ports the fleet macro_trend_gate
pattern: block crypto fades when |BTC 4h move| > 3%. Inherits from
Wolverine HYPE post-mortem, Cobra rotation, Condor v3.0, Lemon v1.3
(same fix shipped same day). XYZ unban deferred to v7.2 — Owl's
scoring uses funding + SM long_pct calibrated on crypto data shape;
XYZ needs its own calibration pass. Producer fetches BTC 4h move from
same leaderboard_get_markets call (no extra MCP cost).
```

## Related

- Top-level repo README: `../README.md`
- Runtime spec: `../senpi-trading-runtime/`
- DSL plugin: `../dsl-dynamic-stop-loss/`
