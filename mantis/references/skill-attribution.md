# Mantis — Skill Attribution

**Skill:** mantis-strategy v5.0.0
**Author:** jason-goldberg
**License:** MIT
**Released:** 2026-04-22

## What Mantis is

Mantis v5.0 — Slipstream — is the first Senpi Predator built around the new `market_get_cross_asset_flows` MCP tool. It strikes correlated alts that haven't yet responded to a leader's significant 4h move, before the catchup completes.

## Inherited patterns

- **Persistent JSONL log on disk** (`state/entry-log.jsonl`) — pattern from Wolverine v2.3 and Spider v2.0. Survives session clears.
- **Per-asset cooldown state** — pattern from Spider/Polar fleet-standard cooldown handling.
- **Conviction-scaled sizing tiers** — same shape as Wolverine and Kodiak (higher score = higher leverage + margin).
- **MCP tool surface** — `strategy_get_clearinghouse_state`, `create_position`, `close_position`, `market_get_cross_asset_flows`.
- **Fleet-standard SKILL.md / runtime.yaml / scripts/ / references/ structure**.

## New patterns introduced

- **Cross-asset signal sourcing** — first Predator to use a cross-asset signal (correlation + lag distribution + SM rotation) rather than a single-asset signal. Previous fleet was entirely single-asset focused.
- **Dynamic per-trade hard timeout** — `avg_lag_minutes × 1.5`, computed at entry time per-asset. Most Predators use a fixed `hard_timeout`; Mantis sets it per-trade based on the alt's historical catchup window.
- **Leader-reversal veto** — novel exit primitive. Closes the position immediately when the leader (BTC) reverses >1% from its move-at-entry. No other Predator has this kind of cross-asset exit dependency.
- **Per-position metadata storage** — `state/position-metadata.json` tracks `leader_asset`, `leader_pct_at_entry`, and expected lag per open position so the veto loop can run independently on each scan tick.

## Dependencies

- `senpi-trading-runtime` — runtime YAML interpreter, scanner orchestration, DSL exit engine
- `dsl-dynamic-stop-loss` — Phase 1 / Phase 2 trailing logic (standard fleet DSL)
- `market_get_cross_asset_flows` — the new cross-asset flow detection MCP tool (released 2026-04-22)
- `hyperliquid-cross-asset-flow-cron` — daily background job that pre-computes the 30-day correlation matrix and 90-day lag analysis the tool depends on

## Coverage

Currently BTC-only as the leader (the cross-asset flow tool only has pre-computed lag data for BTC in its initial release). Adding ETH, SOL, HYPE as leaders is a one-line config change in `mantis_config.py` once Sarvesh ships pre-computed lag data for those assets.
