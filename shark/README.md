# 🦈 SHARK — Liquidation Cascade Front-Runner

The most aggressive strategy in the Senpi ecosystem. Identifies where leveraged positions cluster on Hyperliquid, estimates liquidation zones, and enters JUST BEFORE price reaches them — riding the forced cascade.

## Architecture

```
SM Consensus (15min)   →  Top 10 trader alignment on asset + direction
                          3+ traders agreeing = consensus signal
                          15m candle confirmation required

OI Tracker (5min)      →  Snapshot OI + price + funding for all assets
                          24h history for zone estimation

Liq Mapper (5min)      →  Estimates liquidation zones from OI buildup
                          Scores proximity + momentum toward zones

Proximity (2min)       →  Watches stalking assets approaching zones
                          Volume surge + OI crack detection

Entry (2min)           →  Cascade triggers: OI drop + price break + SM aligned
                          ALO maker orders, 10x leverage, 18% margin

Risk Guardian (5min)   →  Daily loss halt, drawdown, cascade invalidation
                          OI increase after entry = immediate cut

DSL v5.3.1 (3min)          →  9-tier trailing stops (shared skill)

Health (10min)         →  Portfolio reporting, orphan DSL detection
```

## Active Strategy: SM Consensus Trader

Primary entry engine uses smart money leaderboard alignment:
- Pull top 10 traders (4h window)
- Find 3+ traders aligned on same asset + direction
- Validate with 15m candle confirmation
- Enter: 25% margin, 10x leverage, ALO maker-only
- SL at 5% ROE, winners run via DSL trailing

## Scripts

| Script | Status | Purpose |
|--------|--------|---------|
| `shark-sm-consensus.py` | **Active** | SM consensus signal + entry engine |
| `shark-risk.py` | **Active** | Risk guardian with cascade invalidation |
| `shark_config.py` | **Active** | Shared config, MCP helpers, atomic writes |
| `shark-oi-tracker.py` | Active | OI + price + funding snapshots |
| `shark-liq-mapper.py` | Active | Liquidation zone estimation |
| `shark-proximity.py` | Active | Zone proximity detection |
| `shark-entry.py` | Active | Cascade entry triggers |
| `shark-movers.py` | Active | Emerging movers scanner |
| `shark-health.py` | Active | Portfolio health + reporting |
| `shark-conviction.py` | Legacy | v4 prototype |
| `shark-setup.py` | Setup | Strategy initialization |

## Setup

1. Deploy on OpenClaw with Senpi MCP configured
2. Run `shark-setup.py --wallet 0x... --strategy-id UUID --budget 5000 --chat-id 12345`
3. Set environment variables: `SHARK_STRATEGY_ID`, `SHARK_STRATEGY_WALLET`
4. Create cron jobs (see `references/cron-templates.md`)
5. OI tracker needs ~1 hour of data before liquidation mapper produces reliable zones

## Requires

- OpenClaw (with exec, cron, Telegram)
- Senpi MCP server (configured via mcporter)
- DSL v5.3.1 skill (dsl-dynamic-stop-loss)
- Python 3
- `mcporter` CLI
