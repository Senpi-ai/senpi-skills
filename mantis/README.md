# 🦎 MANTIS v5.0 — Slipstream

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/goal/senpi-skills).

Cross-asset catchup hunter. Strikes correlated alts that haven't yet responded to a leader's move, before the catchup completes. First Predator built around the new `market_get_cross_asset_flows` MCP tool.

> The mantis is patience plus speed. Stillness, then strike.

## What Mantis does

- **Monitors the cross-asset flow tool** every 60 seconds
- **When BTC moves >2% in 4h**, the tool surfaces correlated alts that historically follow but haven't yet responded
- **Strikes the highest-confidence laggard** — sized by confidence, direction matches the leader's move
- **Hard timeout = avg_lag_minutes × 1.5** — calibrated per-asset to the alt's historical catchup window
- **Vetoes if the leader reverses** — closes immediately when BTC retraces >1% from the entry move

## The thesis in one paragraph

When BTC runs a +3% 4h breakout, alts like SOL, AVAX, INJ historically follow within a measured time window with a measured reliability. Senpi's cross-asset flow cron computes those windows and reliabilities daily across the top 50 assets. Mantis trades the spread: when an alt with 90% follow rate hasn't moved within its expected lag window, the catchup is the trade. If the leader reverses before the catchup happens, the trade is dead — exit immediately.

## What's different about this Predator

Most Predators trade based on what's happening *to a single asset*. Mantis trades based on what's happening *across asset relationships*. The signal source is unique to Senpi — no public dashboard surfaces 30-day correlation matrices + 90-day lag distributions + smart-money rotation overlay in one composite confidence score.

The data is proprietary. The trade logic is proprietary. The exit logic is proprietary (leader-reversal veto is novel — no other Predator has it).

## Entry filters

| Filter | Threshold |
|---|---|
| `follow_rate` | ≥ 0.85 |
| `confidence` | ≥ 0.75 |
| `gap_pct` | ≥ 1.5% (absolute) |
| `sm_starting_to_rotate` | true |
| `lag_stddev_minutes` | ≤ 90 |
| Asset cooldown | 4 hours since last strike |

## Sizing tiers

| Confidence | Margin | Leverage |
|---|---|---|
| ≥ 0.92 | 75% | 8x |
| ≥ 0.85 | 50% | 7x |
| ≥ 0.75 | 25% | 5x |

## Risk envelope

- Max 2 concurrent positions
- Max 6 entries per UTC day
- Max 75% notional of available margin
- Per-asset cooldown 4 hours
- Hard timeout per trade clamped to [30, 240] min

## Install

```bash
mkdir -p /data/workspace/skills/mantis-strategy/{config,scripts,state}

gh repo clone Senpi-ai/senpi-skills /tmp/senpi-skills
cp -r /tmp/senpi-skills/mantis/* /data/workspace/skills/mantis-strategy/

cp /data/workspace/skills/mantis-strategy/config/mantis-config.example.json \
   /data/workspace/skills/mantis-strategy/config/mantis-config.json

sed -i 's/${WALLET_ADDRESS}/<WALLET>/' /data/workspace/skills/mantis-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/mantis-strategy/runtime.yaml

openclaw senpi runtime create --path /data/workspace/skills/mantis-strategy/runtime.yaml
```

## Files

- `runtime.yaml` — agent runtime configuration (scanners, action, sizing, exit, risk envelope)
- `SKILL.md` — what the LLM agent reads at runtime
- `scripts/mantis-scanner.py` — the main scanner: candidate gathering, leader-reversal veto, strike construction
- `scripts/mantis_config.py` — constants + MCP helpers + config overlay
- `scripts/mantis_state.py` — cooldowns, entry log, position metadata persistence
- `config/mantis-config.example.json` — operator config template
- `references/cross-asset-flow-guide.md` — explainer for the new MCP tool's outputs and how Mantis interprets them
- `references/skill-attribution.md` — lineage and dependencies

## Status

**v5.0 — initial release.** Scanner is production-ready. Currently BTC-only as the leader (the cross-asset flow tool only has pre-computed lag data for BTC in v1). Adding ETH/SOL/HYPE coverage is a one-line config change once their pre-computed lag data ships.

## Operator checklist for first 7 days

- **Day 1:** confirm `state/entry-log.jsonl` is being written (NO_ENTRY entries are fine — they prove the scanner is running)
- **Day 2-3:** check that BTC moves >2% in 4h are producing candidates. If you see `no_qualifying_laggards` constantly even on big BTC moves, entry filters may be too strict for current regime
- **Day 4+:** first STRIKE should appear within the first week if BTC has had a meaningful 4h move. Check the trade reasoning in the entry log — does the catchup thesis match what actually happened to price?
- **Day 7:** review week-1 distribution: how many strikes? How many leader-reversal vetos? What's the win rate?

If win rate is below 55% after 30 strikes, the entry filters need tightening. If strikes are <2/week, filters may be too strict.
