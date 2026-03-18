---
name: phoenix-strategy
description: >-
  PHOENIX v1.0 — Contribution Velocity Scanner. Uses the contribution_pct_change_4h
  field from leaderboard_get_markets — a pre-computed measure of how fast an asset's
  share of top trader profits is growing. Single API call, no local state, no scan
  history. Enters when SM profit share is accelerating AND price hasn't caught up yet.
  The simplest scanner in the zoo with the strongest unused signal.
license: MIT
metadata:
  author: jason-goldberg
  version: "1.0.1"
  platform: senpi
  exchange: hyperliquid
---

# 🔥 PHOENIX v1.0 — Contribution Velocity Scanner

The simplest scanner. The strongest unused signal.

## Why PHOENIX Exists

Every scanner in the zoo reads rank, direction, trader count from `leaderboard_get_markets`. None read `contribution_pct_change_4h` — Senpi's server-side calculation of how fast an asset's share of top trader profits is changing over 4 hours.

This field is a leading indicator. When SM profit concentration in an asset is accelerating, it means traders are actively making money on it RIGHT NOW and the rate is increasing. Price moves follow. Rank changes follow. But the contribution velocity comes first.

Look at the raw data from the March 17 snapshot:
- BTC: contribution 29.55%, change_4h +32.19% — SM is flooding into BTC profits
- HYPE: contribution 15.86%, change_4h +19.12% — significant acceleration
- LIT: contribution 10.73%, change_4h +10.68% — building fast
- ETH: contribution 11.49%, change_4h +11.98% — steady acceleration
- SOL: contribution 4.50%, change_4h +5.27% — moderate

The `contribution_pct_change_4h` tells you WHERE SM money is accelerating before rank climbing is visible. It's the signal our scanners have been leaving on the table.

## How It Works

```
Every 90 seconds: ONE API call → leaderboard_get_markets

For each asset:
  ├─ contribution_pct_change_4h >= 5%?  (SM accelerating)
  ├─ Rank 6-40?                          (not peaked, not invisible)
  ├─ Contribution >= 1%?                 (meaningful SM share)
  ├─ 30+ traders?                        (broad base, not one whale)
  ├─ 4H price aligned with direction?    (trend confirmation)
  ├─ Leverage >= 5x?                     (tradeable)
  └─ XYZ banned?                         (no equities)
      │
      └─ SCORE by velocity, divergence, rank, depth
          └─ Score >= 7 → ENTER best signal
```

No scan history. No multi-scan climbing. No momentum events. Just: "where is SM profit share growing fastest?"

## The Divergence Signal

The most valuable sub-signal is **velocity vs price divergence**:

- BTC contribution growing at +32% over 4h, but price only moved +0.7%
- That's a 46x ratio — SM is making huge money on a tiny price move
- This means SM entered BEFORE the price move and is sitting on leveraged gains
- More SM enters → more buying pressure → price catches up

PHOENIX scores this divergence directly. A 10x+ contrib/price ratio gets +2 points. A 5x+ ratio gets +1 point. This is the alpha window — the gap between SM profiting and price reflecting it.

## Scoring (0-15 range)

| Signal | Points |
|---|---|
| Extreme velocity (>30% 4h change) | 5 |
| High velocity (>15% 4h change) | 3 |
| Base velocity (>5% 4h change) | 2 |
| Dominant SM (>10% of gains) | 2 |
| Strong SM (>5% of gains) | 1 |
| Sweet spot rank (10-20) | 2 |
| Approaching top (6-10) or deep riser (20-30) | 1 |
| Massive SM (150+ traders) | 2 |
| Deep SM (80+ traders) | 1 |
| Price lag (<1.5% move vs strong contrib) | 2 |
| Early move (<3% price) | 1 |
| Extreme divergence (10x+ contrib/price) | 2 |
| Divergence (5x+ contrib/price) | 1 |

Minimum score: 7.

## Comparison

| | Orca | RAPTOR | PHOENIX |
|---|---|---|---|
| Signal source | Leaderboard rank climbing | Tier 2 events + leaderboard | Contribution velocity |
| API calls/scan | 1 | 2 | 1 |
| Local state needed | Scan history (60 snapshots) | None | None |
| Key field | rank change over scans | delta_pnl + TCS/TRP | contribution_pct_change_4h |
| Expected trades/day | 134 signals, 6-10 entries | 3-5 | 5-10 |
| Time to signal | 4.5 min (3 scans) | 90 sec | 90 sec (single scan) |
| Warmup period | 3 scans minimum | None | None |

## DSL Configuration

- Phase 2 trigger at 5% ROE (lower than RAPTOR's 7% — capture gains earlier)
- Extra tier at 30% ROE with 85% lock (for runners)
- Stagnation TP at 8% ROE stale for 40 min
- Conviction-scaled Phase 1 timing by score
- All v1.1.1 fixes: `highWaterPrice: null`, correct field names, dynamic `absoluteFloorRoe`

## Risk Management

| Rule | Value |
|---|---|
| Max positions | 3 |
| Max entries/day | 6 |
| Margin | 20% base, 25% at score 9+, 30% at score 12+ |
| Per-asset cooldown | 90 min (shorter than RAPTOR — velocity signals are faster) |
| Leverage | 5-10x |

## Notification Policy (STRICT)

**ONLY alert:** Position OPENED (asset, direction, score, velocity, divergence ratio), position CLOSED.

**NEVER alert:** Scan with no velocity signals, any reasoning.

## Cron Architecture

| Cron | Interval | Session |
|---|---|---|
| Scanner | 90s | main |
| DSL | 3 min | isolated |

## Cron Setup (CRITICAL — Read This)

The previous Phoenix deployment failed because the DSL cron was configured with a wrong command (`npx skills exec` instead of the Python script). Positions sat unmanaged for 10 hours. BTC lost -$76 and ETH lost -$73 that should have been cut within 30-45 minutes.

**The exact cron commands:**

Scanner cron (every 90 seconds, main session, systemEvent):
```
python3 /data/workspace/skills/phoenix-strategy/scripts/phoenix-scanner.py
```

DSL cron (every 3 minutes, isolated session, agentTurn):
```
python3 /data/workspace/skills/dsl-dynamic-stop-loss/scripts/dsl-v5.py --state-dir /data/workspace/skills/phoenix-strategy/state
```

**DO NOT use `npx skills exec` or any other wrapper.** Run the Python scripts directly. Verify both crons are `status: ok` after creation.

## Bootstrap Gate

On EVERY session, check `config/bootstrap-complete.json`. If missing:
1. Verify Senpi MCP connected
2. Create scanner cron (90s, main) with the EXACT command above
3. Create DSL cron (3 min, isolated) with the EXACT command above
4. Verify both crons show `status: ok` via `openclaw crons list`
5. Write `config/bootstrap-complete.json`
6. Send: "🔥 PHOENIX v1.0.1 online. Contribution velocity scanner active. Silence = no conviction."

## Files

| File | Purpose |
|---|---|
| `scripts/phoenix-scanner.py` | Contribution velocity scanner |
| `scripts/phoenix_config.py` | Standalone config helper |
| `config/phoenix-config.json` | Parameters |

## License

MIT — Built by Senpi (https://senpi.ai).

## Changelog

### v1.0.1
- Added explicit cron commands to SKILL.md to prevent DSL cron misconfiguration
- Added bootstrap gate with cron verification step
- No scanner logic changes — v1.0 code was correct, the bug was agent-side cron setup

### v1.0
- Initial release — contribution velocity scanner using `contribution_pct_change_4h`
