---
name: owl-strategy
description: >-
  OWL v5.2 — Pure contrarian. One scanner, one thesis: the crowd is wrong. Monitors crowding
  across top 30 assets (funding extremity, OI concentration, SM tilt). When crowding persists
  4+ hours AND exhaustion signals fire (volume declining, price stalling, RSI divergence),
  enters AGAINST the crowd to ride the liquidation unwind. 1-2 trades per day max.
  Re-crowding exit: if the crowd comes back, thesis is dead, exit immediately.
  DSL High Water Mode (mandatory). The patient predator.
  v5.2: funding floor lowered from 20% to 12% so the five-factor scoring model actually runs.
  Added observability logging (top 3 crowding scores per scan cycle).
license: Apache-2.0
metadata:
  author: jason-goldberg
  version: "5.2"
  platform: senpi
  exchange: hyperliquid
---

# OWL v5.2 — Pure Contrarian

Wait for the crowd to overcommit. Wait for them to exhaust. Then eat their liquidations.

**One scanner. One thesis.** Every other skill in the zoo enters WITH momentum, WITH the trend, WITH smart money. OWL is the only skill that enters AGAINST the crowd. The edge: crowded trades unwind violently and predictably.

**v5 is a complete rebuild.** v1-v4 had 3 scanners (contrarian + momentum + correlation). The momentum and correlation scanners caused the agent to enter WITH the crowd while thinking it was contrarian. v5 has one scanner that does one thing: find crowded assets that are exhausting.

**v5.2 fix: funding floor lowered from 20% → 12%.** At 20%, the funding gate hard-blocked every asset before SM concentration, OI concentration, or any other signal was evaluated. Hyperliquid funding rates rarely exceed ~11% annualized in normal conditions, so the five-factor scoring model was never running. At 12%, funding must still be meaningfully elevated, but assets with strong SM/OI crowding signals can now accumulate a score. The `minCrowdingScore` of 8 remains the real quality gate.

**v5.2 also adds observability logging.** Every scan cycle logs the top 3 crowding scores and active persistence timers to stderr (internal log only, not notifications). This lets us diagnose "is OWL seeing anything?" without changing config or asking the agent.

## MANDATORY: DSL High Water Mode

**OWL MUST use DSL High Water Mode. This is not optional. Do not substitute standard DSL tiers.**

Spec: https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md

When creating DSL state files for any OWL position, you MUST include:

```json
{
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 10,
  "tiers": [
    {"triggerPct": 10, "lockHwPct": 20, "consecutiveBreachesRequired": 3},
    {"triggerPct": 20, "lockHwPct": 40, "consecutiveBreachesRequired": 3},
    {"triggerPct": 35, "lockHwPct": 60, "consecutiveBreachesRequired": 2},
    {"triggerPct": 50, "lockHwPct": 75, "consecutiveBreachesRequired": 1},
    {"triggerPct": 75, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
  ]
}
```

**FALLBACK:** Use `tiersLegacyFallback` from config until engine supports `pct_of_high_water`.

## How OWL v5 Works

### The Three Phases (all must pass before entry)

**Phase 1 — CROWDING (score ≥ 8)**
Scan top 30 assets every 15 minutes. Score how one-sided each asset's positioning is:

| Signal | Max Points |
|---|---|
| Funding extremity (annualized rate) | 4 |
| SM concentration (leaderboard tilt) | 4 |
| OI concentration (USD-weighted) | 2 |
| SM confirms funding direction | 1 |

Most assets score 0-3 (not crowded). Only assets scoring 8+ advance.

**Phase 2 — PERSISTENCE (4+ hours)**
Crowding must persist for at least 4 hours. A brief funding spike that resolves in 30 minutes is noise. True crowding builds over hours — funding stays extreme, OI keeps growing, SM stays tilted. The longer the crowding persists, the more violent the eventual unwind.

**Phase 3 — EXHAUSTION (score ≥ 5)**
The crowd is positioned, and they've been positioned for hours. Now: are they exhausting? Four signals:

| Signal | Points | What It Means |
|---|---|---|
| Volume declining (recent vs 6h avg) | 3 | Conviction leaving — nobody new is entering |
| Price stalling (crowd long but price flat) | 3 | The trade stopped working — crowd is trapped |
| Volume spike without follow-through | 2 | Capitulation wick — someone tried to push, failed |
| 4h RSI divergence | 2 | Momentum dying despite positioning |

### Entry

Total score (crowding + exhaustion) must be ≥ 14. Entry direction is OPPOSITE to the crowd. If the crowd is long, OWL goes short. If the crowd is short, OWL goes long.

This means OWL enters 1-2 times per day at most. Often zero. That's by design.

### Hold

Every 15-minute scan re-evaluates held positions FIRST. The position holds as long as the crowd doesn't come back.

### Re-Crowding Exit (unique to OWL)

If the crowd rebuilds in their original direction (re-crowding score ≥ 6), the unwind thesis is dead and the position exits immediately. This is OWL's equivalent of SCORPION's "sting" — an instant, thesis-based exit that overrides DSL.

## DSL: Widest in the Zoo

Contrarian entries retrace hard before working. The crowd doesn't unwind smoothly — they fight back first. OWL needs the widest DSL bands of any skill.

| Setting | Value | Compare to FOX |
|---|---|---|
| Phase 1 floor | 4% notional (~40% ROE at 10x) | FOX: 1.5% |
| Phase 2 trigger | +10% ROE | FOX: +7% |
| T1 lock | 20% of HW | FOX: 40% |
| 85% trail at | +75% ROE | FOX: +20% |
| Stagnation TP | 15% ROE, 120min | FOX: 10%, 45min |
| Time exits | All disabled | FOX: 30min hard |

The tradeoff: OWL loses bigger on losers (-35 to -40% ROE) but catches crowding unwinds that produce 50-200%+ ROE when the cascade hits.

## Risk Management

| Rule | Value | Notes |
|---|---|---|
| Max positions | 2 | Rare, concentrated bets |
| Max entries/day | 2 base, up to 4 on profitable days | |
| Phase 1 floor | 4% notional | Widest in the zoo |
| G5 per-position cap | 10% of account | Wider than most — contrarian needs room |
| Drawdown halt | 25% from peak | |
| Max consecutive losses | 2 → 180 min cooldown | Long cooldown — if the contrarian thesis failed twice, something changed |
| Re-crowding exit | Immediate | If the crowd comes back, thesis is dead |
| Loss cooldown per asset | 6 hours | Don't re-enter same contrarian trade too soon |

## Cron Architecture

| Cron | Interval | Session | Purpose |
|---|---|---|---|
| Scanner | 15 min | isolated | Crowding scan + exhaustion detection + re-crowding check |
| DSL v5.3.1 | 3 min | isolated | High Water Mode trailing stops |

**15-minute scanner interval is intentional.** Crowding builds over hours, not minutes. Scanning every 3 minutes would waste tokens on data that hasn't changed. The DSL cron still runs every 3 minutes for trailing stop protection.

## Notification Policy

**ONLY alert:** Position OPENED (asset, direction, crowding score, exhaustion signals, how long crowded), position CLOSED (DSL or re-crowding exit with reason), risk guardian triggered, critical error.
**NEVER alert:** Scanner found no crowding, scanner found crowding but no exhaustion, persistence tracking updates, DSL routine check, any reasoning.
All crons isolated. `NO_REPLY` for idle cycles.

## Bootstrap Gate

Check `config/bootstrap-complete.json` every session. If missing:
1. Verify Senpi MCP
2. Create scanner cron (15 min, isolated) and DSL cron (3 min, isolated)
3. Write `config/bootstrap-complete.json`
4. Send: "🦉 OWL v5 is online. Pure contrarian. Scanning for crowded exhaustion. Silence = no opportunity."

## Expected Behavior

| Metric | Expected |
|---|---|
| Trades/day | 0-2 (crowding unwinds are rare) |
| Avg hold time | 4-24 hours |
| Win rate | ~45-55% (wider stops, contrarian timing is hard) |
| Avg winner | 40-150%+ ROE (crowding unwinds are violent) |
| Avg loser | -25 to -40% ROE (wide floors, structural invalidation) |
| Fee drag/day | $2-8 (very few trades, all maker entries) |
| Profit factor | Target 1.3-2.0 (big winners compensate for wider losers) |

## Files

| File | Purpose |
|---|---|
| `scripts/owl-scanner.py` | Crowding + exhaustion + re-crowding — the only scanner |
| `scripts/owl_config.py` | Shared config, MCP helpers |
| `config/owl-config.json` | All variables with DSL High Water tiers + legacy fallback |

## License

Apache-2.0 — Built by Senpi (https://senpi.ai). Attribution required for derivative works.
Source: https://github.com/Senpi-ai/senpi-skills
