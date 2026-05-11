---
name: raptor-strategy
description: >-
  RAPTOR v3.2 — Hot Streak Follower (whale entry-price discipline).
  Finds ELITE/RELIABLE traders currently on a 4-hour hot streak via
  leaderboard_get_top + discovery_get_top_traders, identifies their
  strongest position, confirms SM alignment, and piggybacks — but
  ONLY if we get a fill at a better price than the whale did.
  v3.2 (2026-04-16) adds entry-discipline: fetch current price vs
  whale's entryPx; if asset has run 20%+ in whale's favor post-entry,
  SKIP (we'd be buying their top). Bonus +1/+2 points if we're
  getting a better fill than the whale. Prior v3.1 spiral (-$60 in
  16h) was caused by re-entering whales' positions at worse prices
  than they originally filled.
license: MIT
metadata:
  author: jason-goldberg
  version: "3.4"
  platform: senpi
  exchange: hyperliquid
---

# RAPTOR v3.2 — Hot Streak Follower (entry-price discipline)

Find traders who are already winning big right now, confirm they're quality (ELITE or RELIABLE on TCS), figure out what bet is driving their streak, check the smart money crowd agrees, and piggyback the same trade.

## v3.0 — why the complete rewrite

**RAPTOR v2.1 traded zero times since deployment.** Root cause:

v2.1 used `leaderboard_get_momentum_events` as the primary signal source and dereferenced three fields on each event — `concentration`, `top_positions`, and `trader_tags`. **All three are `null` on blocked events.** And blocked events are ~100% of tier 2 events in normal market conditions (820/820 tier 2 events over a 44-hour window in one recent sample were all blocked — either `trader_cooldown_active` or `system_cooldown_active`).

Per the senpi guide for `leaderboard_get_momentum_events`:
> Blocked events are equally valid momentum signals. The blocking only affects notification delivery, not signal quality. Events with `top_positions: null` (blocked events without position data) will not match any asset filter.

v2.1's filter chain silently dropped every blocked event before ever reaching the SM alignment check. Raptor was mathematically incapable of producing a signal regardless of market conditions.

## v3.0 architecture

v3.0 replaces `leaderboard_get_momentum_events` with a multi-step pipeline that uses populated data sources:

```
1. leaderboard_get_top(limit=30)
     → top 30 traders by 4h delta PnL (populated data, not blocked)

2. Local filter: delta_pnl ≥ $2M (tier 1 threshold)
     → narrows to currently active hot streaks

3. discovery_get_top_traders(addresses=[...], consistency=["ELITE","RELIABLE"])
     → single batch call filters to quality traders and attaches labels

4. Per quality trader: leaderboard_get_trader_positions(address)
     → per-market delta PnL breakdown with real data

5. Pick the strongest position by |delta_pnl|, compute concentration locally

6. leaderboard_get_markets → SM alignment check on each candidate

7. Score and execute the best candidate via create_position (self-executing)
```

API cost per scan: 1 (top) + 1 (classify) + up to 10 (positions) + 1 (SM) = ~13 calls/scan. Higher than v2.1's 2 calls/scan, but v2.1 was zero trades/month and v3.0 actually produces signals.

## Scoring model

| Component | Max | Gate |
|---|---|---|
| TCS consistency | 3 | ELITE/RELIABLE required |
| Delta PnL tier | 3 | ≥$2M required |
| Concentration | 2 | ≥0.40 required |
| SM alignment strength | 2 | ≥2% SM, ≥10 traders, direction match required |
| 4h + 1h price confirmation | 2 | — |
| 15m velocity freshness | +1/-1 | — |

**Hard gates before scoring:** ELITE/RELIABLE consistency, ≥$500k position delta PnL, ≥40% concentration, SM alignment with matching direction.

**Default minScore to trade:** 6. Tunable via `config/raptor-config.json`.

## Leverage tiers

Conviction-scaled:
- Score 6-7: **7x**
- Score 8-9: **8x**
- Score 10+: **10x**

## Fleet-standard guardrails

- **Self-executing** — scanner calls `create_position` directly via mcporter, matching the Wolverine/Phoenix pattern. No external action layer required.
- **Dynamic daily cap** (fleet PR #176) — P&L-aware entry cap: 12 trades/day if +5% PnL, down to 0 trades (HARD STOP) at -25% drawdown.
- **Auto-cancel stale resting orders** (fleet PR #177) — `has_resting_orders()` cancels any non-reduceOnly maker order older than 10 minutes so a stuck FEE_OPTIMIZED_LIMIT order never locks the scanner out.
- **Per-trader event dedupe** — won't re-enter the same (trader, asset) pair within 4 hours.
- **Per-asset cooldown** — 2-hour cooldown after a trade on any given coin.

## Runtime

- `runtime.yaml` added (was missing in v2.1)
- Standard Predators deployment: `position_tracker` scanner + rule-based action + DSL engine
- DSL tuned for follow-the-streak trades: hard_timeout 360 min, wide phase 2 tiers to let winners compound

## Configuration

All thresholds live in `config/raptor-config.json`:

- `hotStreak.minDeltaPnl` — minimum 4h delta PnL to consider a trader (default $2M)
- `hotStreak.tier2Threshold` / `tier3Threshold` — scoring tier thresholds ($5.5M / $10M)
- `hotStreak.topTraderLimit` — how many from leaderboard_get_top to pull (default 30)
- `hotStreak.minConcentration` — minimum position concentration (default 0.40)
- `smAlignment.minSmPct` / `minSmTraders` — SM consensus strength gates
- `dedupe.eventDedupeHours` — trader-asset dedupe window (default 4h)
- `dedupe.perAssetCooldownMinutes` — asset cooldown after any trade (default 120 min)
- `entry.minScore` — score floor to fire an entry (default 6)

## Deployment

Agents on v2.1 should pull the v3.0 package and restart. The v3.0 repo package includes:

- `raptor/runtime.yaml` (NEW)
- `raptor/SKILL.md` (NEW)
- `raptor/config/raptor-config.json` (NEW)
- `raptor/scripts/raptor_config.py` (NEW — was missing in v2.1 even though scanner imported it)
- `raptor/scripts/raptor-scanner.py` (REWRITTEN)

Pull command:
```bash
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/raptor/scripts/raptor-scanner.py -o /data/workspace/skills/raptor-strategy/scripts/raptor-scanner.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/raptor/scripts/raptor_config.py -o /data/workspace/skills/raptor-strategy/scripts/raptor_config.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/raptor/config/raptor-config.json -o /data/workspace/skills/raptor-strategy/config/raptor-config.json
```

Then verify with a single manual run:
```bash
python3 /data/workspace/skills/raptor-strategy/scripts/raptor-scanner.py
```

Expected: clean exit, `_raptor_version: "3.0"` in output JSON. If `leaderboard_get_top` returns data and there's at least one ELITE/RELIABLE hot trader, the scanner may emit a candidate signal or fire a trade on the first run.


---

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.
