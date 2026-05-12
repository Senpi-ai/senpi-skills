# Mantis — Cross-Asset Catchup Hunter

**Runtime:** 1.0  ·  **Asset:** Multi-asset  ·  **Version:** 1.5.0

## Thesis

MANTIS v5.0 — Slipstream. Cross-asset catchup hunter built on the new

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
MANTIS v5.0 — Slipstream. Cross-asset catchup hunter built on the new
market_get_cross_asset_flows MCP tool. When BTC (or another leader)
makes a significant 4h move and a correlated alt hasn't responded yet,
Mantis strikes the alt before the catchup completes. Trades the
statistical lag, not the momentum itself. Hard veto if the leader
reverses mid-position (enforced by the scanner, not the runtime).
First Predator built around cross-asset flow detection.
```

## Related

- Top-level repo README: `../README.md`
- Runtime spec: `../senpi-trading-runtime/`
- DSL plugin: `../dsl-dynamic-stop-loss/`
