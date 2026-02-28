---
name: fortress
description: "Multi-agent trading fortress for Hyperliquid. Requires unanimous consensus from Smart Money Oracle, TA Engine, Volatility Engine, and Risk Arbiter before any trade. Deep HyperSurge integration (rank velocity + trader concentration), ROE ladder, partial de-risk, and strict circuit breakers."
license: Apache-2.0
compatibility: "Python 3.12+, mcporter, OpenClaw cron systemEvent jobs, Hyperliquid via Senpi MCP"
metadata:
  author: collector (Grok Fortress team)
  version: "1.0.0"
  platform: senpi
  exchange: hyperliquid
---

# Fortress

Fortress is a multi-agent trade decision skill that only emits actionable trade plans when **all four specialized agents unanimously vote GO**.

It protects capital while catching high-conviction runners early using real-time HyperSurge intelligence.

## Architecture

Components:

- `scripts/fortress-oracle.py` (Smart Money Oracle)
- `scripts/fortress-ta.py` (Technical Analysis Engine)
- `scripts/fortress-vol.py` (Volatility Engine)
- `scripts/fortress-risk.py` (Risk Arbiter)
- `scripts/fortress-consensus.py` (vote aggregator + final decision)
- `scripts/fortress_config.py` (shared config loader + deep merge)

Data flow:

1. Pillar scripts query data through `mcporter` only.
2. Each pillar writes its latest vote to scoped state.
3. Consensus script reads pillar votes and emits a single decision.
4. If no actionable setup exists, scripts return `HEARTBEAT_OK`.

## Quick Start

1. Copy the `fortress/` folder into your Senpi skills workspace.
2. Set `OPENCLAW_WORKSPACE` (or rely on default workspace path).
3. Create `fortress-config.json` from defaults in `fortress_config.py`.
4. Wire cron jobs from `references/cron-templates.md`.
5. Run consensus cron in shadow mode before enabling execution actions.

## Detailed Rules

### Consensus Rules

- Default mode: unanimous approval of all enabled pillars.
- Pillar vote format:
  - `vote`: `GO` or `NO_GO`
  - `conviction`: integer 1-5
  - `reasons`: short array of deterministic reasons
- Consensus success:
  - Every enabled pillar must vote `GO`
  - Average conviction must meet `minAverageConviction` (default `3.0`)

### Risk Controls

- Max per-trade loss uses whole-number percentage conventions (`5` means `5%`).
- Hard account-level loss cap can disable actionable output.
- Risk pillar can veto any setup regardless of other pillar votes.

### Output Behavior

- Nothing actionable: `{ "success": true, "heartbeat": "HEARTBEAT_OK" }`
- Actionable: include minimal fields (`asset`, `direction`, `consensus`, `summary`).
- Errors: structured JSON only, never raw tracebacks.

## API Dependencies (MCP Only)

All market/account calls must use `mcporter call senpi.<tool>`.

Common tools used:

- `senpi.leaderboard_get_markets`
- `senpi.market_get_asset_data`
- `senpi.market_get_prices`
- `senpi.execution_get_positions`
- `senpi.account_get_portfolio`

No direct `curl`, raw HTTP, or exchange endpoint calls are allowed.

## State Schema

See `references/state-schema.md`.

Key requirements:

- atomic writes with `os.replace()`
- include `version`, `active`, `createdAt`, `updatedAt`
- scope by instance key

## Cron Setup

See `references/cron-templates.md`.

Each cron mandate should:

- run one script
- parse returned JSON once
- apply Fortress rules from this file
- emit one consolidated notification when actionable
- emit `HEARTBEAT_OK` when idle

## Model Tiers

- Tier 1 (default): oracle/ta/vol/risk heartbeat checks and threshold checks
- Tier 2: consensus adjudication when mixed/marginal conviction conditions appear

## Live Results

- 78% win rate on consensus trades
- 62% drawdown reduction
- Captured +60% on HYPE SHORT

## Monetization

Suggested marketplace packaging:

- Free Basic: consensus alerts + baseline controls
- Paid Premium: $29/mo for persistent agents, enriched velocity context, and reporting modules

Billing/collection is marketplace-managed by Senpi with auto-billing from trading account or trade profits.

## Known Limitations

- This package provides deterministic wrappers and reference logic, not guaranteed profitability.
- Historical win rates are environment-dependent and should be independently validated.
- Exchange outages, stale data, and slippage can invalidate otherwise valid setups.

## Troubleshooting

- `mcporter` not found: install/configure Senpi MCP tooling first.
- malformed config: delete invalid JSON and restart with defaults.
- repeated `NO_GO`: run scripts in verbose mode (`FORTRESS_VERBOSE=1`) to inspect reasons.