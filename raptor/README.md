# 🦅 RAPTOR v1.0 — Momentum Event Confluence Scanner

Part of the [Senpi Trading Skills Zoo](https://github.com/Senpi-ai/senpi-skills).

## What RAPTOR Does

RAPTOR is the first scanner to combine Hyperfeed's two strongest signal sources:

**Tier 2 Momentum Events** — When a proven trader's delta PnL crosses $5.5M+, that's not speculation. That's real money being made right now. Only 155 of these fire per day (vs 5,123 Tier 1 events). After filtering by trader quality (TCS ELITE/RELIABLE, TRP SNIPER/AGGRESSIVE, concentration >0.5), roughly 30-40 remain.

**SM Leaderboard Confirmation** — The asset must also be climbing the smart money concentration leaderboard. Contribution building, rank improving, 20+ traders aligned. This confirms the momentum isn't a single whale — it's broad SM consensus.

When both fire on the same asset: enter with high conviction. Expected 3-5 trades per day.

## Why This Combination Works

Every other scanner in the zoo uses ONE signal source:

| Scanner | Signal Source | Weakness |
|---|---|---|
| Orca | SM leaderboard rank climbing | Can't tell if the climbing leads to a real move |
| Bison | SM + trend alignment | Waits for 4H/1H agreement — often too late |
| Barracuda | Funding persistence | Slow — waits 6+ hours for extreme funding |
| Fox | SM + scoring gauntlet | Filters are tight but still single-source |

RAPTOR uses two independent sources. A momentum event confirms someone is profiting. The leaderboard confirms SM is building. Either alone is noisy. Together they're high conviction.

## The Timing Advantage

Orca's Stalker mode waits 3 scans (4.5 minutes) to confirm rank climbing is real. By then, early movers have already entered.

RAPTOR doesn't wait. The momentum event IS the confirmation — a $5.5M threshold crossing from a consistently profitable trader. The leaderboard check takes one API call. Total time from signal to entry decision: one scan cycle (90 seconds).

## Directory Structure

```
raptor-v1.0/
├── README.md
├── SKILL.md
├── config/
│   └── raptor-config.json
└── scripts/
    ├── raptor-scanner.py
    └── raptor_config.py
```

## Quick Start

1. Deploy `config/raptor-config.json` with your wallet and strategy ID
2. Deploy `scripts/raptor-scanner.py` and `scripts/raptor_config.py`
3. Create scanner cron (90s, main session) and DSL cron (3 min, isolated)
4. Fund with $1,000 on the Senpi Predators leaderboard

## Trader Quality Taxonomy

RAPTOR filters momentum events by three trader dimensions:

**TCS (Trader Consistency Score):**
- ELITE (≥75, no negative in 7+ day segments) — accepted
- RELIABLE (≥50) — accepted
- STREAKY (≥33) — rejected
- CHOPPY (<33) — rejected

**TRP (Trader Risk Profile):**
- SNIPER (75-100, max leverage, near-max margin) — accepted
- AGGRESSIVE (50-75) — accepted
- BALANCED (25-50) — accepted
- CONSERVATIVE (0-25) — rejected

**Concentration:** >0.5 required (gains concentrated in few positions, not diversified noise).

## Signal Flow

```
Tier 2 events (155/day)
    │
    ├─ TCS filter → ~80 remain
    ├─ TRP filter → ~50 remain
    ├─ Concentration filter → ~30-40 remain
    │
    └─ Cross-reference with SM leaderboard
        ├─ Asset rank 6-30
        ├─ contribution_pct_change_4h > 0
        ├─ Direction match (trader + SM agree)
        ├─ 20+ traders
        └─ Leverage ≥ 5x
            │
            └─ Score ≥ 7 → ENTER
                └─ 3-5 signals per day
```

## Key Differences from Orca

| | Orca v1.1.1 | RAPTOR v1.0 |
|---|---|---|
| Signal source | SM leaderboard only | Momentum events + leaderboard |
| Entry trigger | 3+ scans of rank climbing | Single momentum event + leaderboard check |
| Trader quality | Not used | TCS/TRP/concentration filtered |
| Time to signal | 4.5+ minutes (3 scans) | 90 seconds (1 scan) |
| Daily signals | 134 Stalker + 0 Striker (typical) | 3-5 confluence signals |
| Frequency | High volume, moderate conviction | Low volume, high conviction |

## Requires

- dsl-v5.py with Patch 1 (dynamic absoluteFloorRoe calculator) and Patch 2 (highWaterPrice null handling)
- Senpi MCP with `leaderboard_get_momentum_events` and `leaderboard_get_markets`

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills

## Changelog

### v1.0
- Initial release — dual-source momentum event + SM leaderboard confluence scanner
- TCS/TRP/concentration quality filtering on momentum events
- Direction confirmation between trader position and SM consensus
- Score-based conviction scaling (margin 25/30/35% by score)
- DSL v1.1.1 pattern: `highWaterPrice: null`, correct field names, dynamic `absoluteFloorRoe`
- 2-hour per-asset cooldown, XYZ equity ban, leverage floor 5x
