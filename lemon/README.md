# Lemon — Degen Fader

**Runtime:** 1.0  ·  **Asset:** XYZ markets  ·  **Version:** 1.3.0

## Thesis

Lemon v1.3 — Degen Fader (macro gate + XYZ unban). v1.2 (10x cap / 30% margin / MIN_SCORE 9) eliminated catastrophic

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
Lemon v1.3 — Degen Fader (macro gate + XYZ unban).
v1.2 (10x cap / 30% margin / MIN_SCORE 9) eliminated catastrophic
blow-ups but didn't fix signal quality: -$246 lifetime / 37% win rate /
-$2 per trade avg / asymmetric win-loss size ($2 win vs $6 loss).
v1.3 targets the underlying signal:
  1. MACRO_TREND_GATE (crypto only): block fades when |BTC 4h| > 3%.
     Fade thesis structurally fails in trending regimes — pattern
     documented across Wolverine HYPE (-$160), Cobra (-60%), and
     Condor v3.0's MACRO_TREND_GATE precedent. XYZ bypasses the
     gate (oil/gold/spx trade on their own macro).
  2. XYZ unban: prior XYZ_BANNED=True was a lazy scaffold default;
     fade thesis applies to news-driven XYZ moves (Apr 17 Iranian
     ship seizure on oil = textbook crowd-pile fade setup). Erik's
     XYZ DSL prefix fix (2026-05-05) wires exit protection
     correctly. Adds xyz:BRENTOIL/CL/GOLD/SPX. ISOLATED margin set
     automatically on XYZ orders per HIP-3.
DSL config unchanged from v1.2.
```

## Related

- Top-level repo README: `../README.md`
- Runtime spec: `../senpi-trading-runtime/`
- DSL plugin: `../dsl-dynamic-stop-loss/`
