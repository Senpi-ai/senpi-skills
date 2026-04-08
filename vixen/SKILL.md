---
name: vixen-strategy
description: >-
  VIXEN v1.0 — Dual-mode emerging movers scanner. Built from FOX v1.6 live trading
  data (+34.5% ROI). Two entry modes: STALKER (steady accumulation, score 6+, catches
  the SM buildup BEFORE the explosion) and STRIKER (violent FIRST_JUMP, score 9+, with
  raw volume confirmation to filter blow-off tops). 2-hour per-asset cooldown after
  Phase 1 exits. DSL High Water Mode mandatory.
license: MIT
metadata:
  author: jason-goldberg
  version: "1.0"
  platform: senpi
  exchange: hyperliquid
---

# 🦊 VIXEN v1.0 — Dual-Mode Emerging Movers

Two ways to catch smart money. Enter before the crowd, or ride the explosion — but never buy a blow-off top.

## Why VIXEN Exists

FOX v1.6 returned +34.5% in 4 days. The post-mortem revealed a critical insight: the two biggest winners (ZEC +$129, SILVER +$128) were caught at score 5-7 by the early v0.1 scanner, and would have been **rejected** by the later v7.2 "Feral Gauntlet" (score >= 9, FIRST_JUMP required, velocity >15 required).

The Feral Gauntlet accidentally optimized for violent spikes only — which are sometimes real breakouts (FARTCOIN +$111, ENA +$72) and sometimes blow-off tops (PUMP $0, XPL $0). It filtered out the most profitable signal type: steady SM accumulation before the explosion.

VIXEN solves this with two entry modes that each capture a distinct alpha pattern.

## Dual-Mode Entry Architecture

### MODE A — STALKER (the ZEC/SILVER pattern)

**What it detects:** Steady, sustained SM rank climbing over 3+ consecutive scans (9+ minutes). SM is quietly building positions. Price hasn't exploded yet. You enter BEFORE the crowd.

**Gates:**
- Asset must show rank improvement over 3+ consecutive scans
- Total climb >= 5 ranks across those scans
- Each scan shows continued climbing (no stalls or reversals)
- SM contribution must be building (each scan >= previous)
- 4H trend alignment required
- Destination ceiling: reject if already in Top 10
- Rank history must not be erratic
- Score >= 6

**Scoring:**
| Signal | Points |
|---|---|
| STALKER_CLIMB (sustained climbing) | 3 |
| CONTRIB_ACCEL (contribution velocity > 0.1%/scan) | 2 |
| CONTRIB_POSITIVE (velocity > 0) | 1 |
| SM_ACTIVE (10+ traders) | 1 |
| DEEP_START (started from #30+) | 1 |
| Time-of-day (04-14 UTC bonus) | +1 |
| Time-of-day (18-02 UTC penalty) | -2 |

### MODE B — STRIKER (the FARTCOIN/ENA pattern)

**What it detects:** Violent FIRST_JUMP — 15+ rank spike in a single scan from outside Top 25. The explosion is happening NOW. Requires raw volume confirmation.

**Gates (Fox v7.2 Feral Gauntlet, plus volume):**
- FIRST_JUMP or IMMEDIATE_MOVER (10+ rank jump from #25+)
- Explosive threshold: rank jump >= 15 OR velocity > 15
- Velocity floor: > 10 (relaxed to > 0 for FIRST_JUMP)
- 4H trend alignment required
- Destination ceiling: reject if in Top 10
- Minimum 4 distinct reasons
- **NEW: Raw volume confirmation (1h volume >= 1.5x of 6h average)**
- Score >= 9

**Scoring:**
| Signal | Points |
|---|---|
| FIRST_JUMP | 3 |
| IMMEDIATE_MOVER | 2 |
| CONTRIB_EXPLOSION (3x+) | 2 |
| HIGH_VELOCITY (>10) | 2 |
| DEEP_CLIMBER | 1 |
| Multi-scan climb bonus | 1 |
| Time-of-day modifier | -2 to +1 |

### Priority Rules

When both modes fire on the same asset, STRIKER takes priority (stronger immediate signal). Signals are combined and sorted by score. The execution agent picks the best available signal with an open slot.

## 2-Hour Per-Asset Cooldown

**The PAXG lesson:** FOX entered PAXG, got chopped out by Phase 1, then immediately re-entered on a high score flash — and lost again. The conditions that killed the first entry were still present.

VIXEN enforces a **120-minute cooldown per asset after any Phase 1 exit** (dead weight, timeout, or absolute floor). Other assets are unaffected. After 120 minutes, if the asset passes the full gauntlet again, it's a valid fresh entry.

## Shared Gates (Both Modes)

- **4H trend alignment:** Never trade against the macro. LONG requires green 4H, SHORT requires red 4H.
- **Destination ceiling:** Reject if asset lands in Top 10 (move is already over).
- **Time-of-day modifier:** +1 for 04-14 UTC (optimal window), -2 for 18-02 UTC (chop zone).
- **Max 3 simultaneous positions**
- **Per-asset cooldown:** 2 hours after Phase 1 exit

## MANDATORY: DSL High Water Mode

**VIXEN MUST use DSL High Water Mode. This is not optional.**

Spec: https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md

### Phase 1 (Conviction-Scaled)

| Score | Absolute Floor | Hard Timeout | Weak Peak | Dead Weight |
|---|---|---|---|---|
| 6-7 (Stalker) | -20% ROE | 30 min | 15 min | 10 min |
| 8-9 | -25% ROE | 45 min | 20 min | 15 min |
| 10+ (Striker) | -30% ROE | 60 min | 30 min | 20 min |

### Phase 2 (High Water Trailing)

| Tier | Trigger ROE | Lock % of HW | Breaches |
|---|---|---|---|
| 1 | 5% | 20% | 2 |
| 2 | 10% | 40% | 2 |
| 3 | 20% | 55% | 2 |
| 4 | 30% | 70% | 1 |
| 5 | 50% | 80% | 1 |
| 6 | 75% | 85% | 1 |
| 7 | 100%+ | 90% | 1 |

## Risk Management

| Rule | Value |
|---|---|
| Max positions | 3 |
| Max entries/day | 3 base, up to 6 on profitable days |
| Absolute floor | 2% notional (~20% ROE at 10x) |
| Drawdown halt | 25% from peak |
| Daily loss limit | 10% |
| Cooldown | 30 min after 3 consecutive losses |
| Per-asset cooldown | 120 min after Phase 1 exit |
| Stagnation TP | 10% ROE stale for 45 min |

## Cron Architecture

| Cron | Interval | Session | Purpose |
|---|---|---|---|
| Scanner | 90s | main | Dual-mode emerging movers detection |
| DSL v5 | 3 min | isolated | High Water Mode trailing stops |

## Notification Policy

**ONLY alert:** Position OPENED (mode, asset, direction, score, reasons), position CLOSED (DSL or Phase 1 with reason), risk guardian triggered, critical error.

**NEVER alert:** Scanner ran with no signals, signals filtered out, DSL routine check, any reasoning.

## Bootstrap Gate

On EVERY session, check `config/bootstrap-complete.json`. If missing:
1. Verify Senpi MCP
2. Create scanner cron (90s, main) and DSL cron (3 min, isolated)
3. Write `config/bootstrap-complete.json`
4. Send: "🦊 VIXEN v1.0 is online. Dual-mode scanner active — stalking accumulators and striking breakouts. Silence = no conviction."

## Expected Behavior

| Metric | Expected |
|---|---|
| Trades/day | 3-8 (Stalker catches more setups than Striker alone) |
| Stalker trades | 2-5/day (steady climbers are more common than explosions) |
| Striker trades | 1-3/day (violent breakouts are rare by design) |
| Win rate | ~50-60% (Stalker entries have better R:R from earlier positioning) |
| Avg Stalker winner | 40-130%+ ROE (entered early, DSL trails the entire move) |
| Avg Striker winner | 20-60%+ ROE (entered at breakout, less room to trail) |
| Avg loser | -15 to -25% ROE (Phase 1 cuts fast) |

## Files

| File | Purpose |
|---|---|
| `scripts/vixen-scanner.py` | Dual-mode scanner (Stalker + Striker) |
| `scripts/vixen_config.py` | Shared config, MCP helpers, state I/O, cooldown tracking |
| `config/vixen-config.json` | All configurable variables with DSL tiers |

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills
