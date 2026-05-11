# Dog — Multi-Asset Exhaustion Fader

**Runtime:** 2.0  ·  **Asset:** Multi-asset  ·  **Status:** Live  ·  **Version:** 2.5.0

## Thesis

Dog v2.5 — exhaustion gate calibration (2026-05-05). v2.2's MIN_EXHAUSTION_PCT 4.5 produced ZERO trades in ~3 weeks because

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
Dog v2.5 — exhaustion gate calibration (2026-05-05). v2.2's
MIN_EXHAUSTION_PCT 4.5 produced ZERO trades in ~3 weeks because
4.5% 4h moves on BTC/ETH/SOL/HYPE majors are rare (BTC's biggest
4h move in past 44h was only +2.27%). Lowered to 3.0 — captures
legitimate overextension while rejecting trend-continuation noise.
Sweet spot between v2.1's permissive 2.5% (which lost -$53 on 10
trades) and v2.2's never-fires 4.5%. DEEP_EXHAUSTION_BONUS
unchanged — bigger moves still get the +2 score boost.
```

## Related

- Top-level repo README: `../README.md`
- Runtime spec: `../senpi-trading-runtime/`
- DSL plugin: `../dsl-dynamic-stop-loss/`
