# 🦊 VIXEN v1.0 — Dual-Mode Emerging Movers

Part of the [Senpi Trading Skills Zoo](https://github.com/Senpi-ai/senpi-skills).

## What VIXEN Does

VIXEN detects smart money moves on Hyperliquid using two distinct entry modes:

**STALKER** — catches SM quietly accumulating over 3+ consecutive scans before the explosion. This is where FOX's biggest winners came from (ZEC +$129, SILVER +$128 — both entered at score 5-7 before the crowd arrived).

**STRIKER** — catches violent FIRST_JUMP breakouts with raw volume confirmation. This is FOX's v7.2 Feral Gauntlet plus a volume gate that filters blow-off tops (like PUMP, which had 8x volume but was exhaustion, not breakout).

Built from FOX v1.6's live trading data: +34.5% ROI, 91 trades, 4 days.

## Why Two Modes?

FOX's data revealed a paradox: the v7.2 gauntlet (score >= 9, FIRST_JUMP required) would have **rejected** the two most profitable trades. Tightening further was dangerous. But loosening to score 6 let in noise.

The answer: two separate entry paths with different gates, feeding into the same DSL trailing system.

```
                    leaderboard_get_markets
                           |
                    +------+------+
                    |             |
               STALKER        STRIKER
            (accumulation)   (explosion)
            Score 6+         Score 9+
            3+ scans         FIRST_JUMP
            Steady climb     Velocity >15
            Vol building     Vol >= 1.5x avg
                    |             |
                    +------+------+
                           |
                   Same DSL High Water
                   Same Phase 1 cuts
                   Same trailing stops
```

## Quick Start

1. Deploy `config/vixen-config.json` to your Senpi agent
2. Deploy `scripts/vixen-scanner.py` and `scripts/vixen_config.py`
3. Create scanner cron (90s, main session) and DSL cron (3 min, isolated)
4. Fund with $1,000 on the Senpi Predators leaderboard

## Directory Structure

```
vixen-v1.0/
|-- README.md
|-- SKILL.md
|-- config/
|   +-- vixen-config.json
+-- scripts/
    |-- vixen-scanner.py
    +-- vixen_config.py
```

## Key Improvements Over FOX v1.6

| Issue in FOX | Fix in VIXEN |
|---|---|
| v7.2 gauntlet filtered out best winners (ZEC, SILVER) | Stalker mode catches accumulation pattern at score 6+ |
| Blow-off tops passed the gauntlet (PUMP) | Striker mode requires raw volume >= 1.5x of 6h avg |
| PAXG double-entry (revenge trade) | 2-hour per-asset cooldown after Phase 1 exits |
| 5 breakeven trades had weak volume at entry | Both modes confirm volume before entry |
| Single entry mode couldn't distinguish accumulation from explosion | Dual-mode architecture with mode-specific gates |

## Dual-Mode Summary

| | STALKER | STRIKER |
|---|---|---|
| Pattern | Steady SM accumulation | Violent FIRST_JUMP |
| Min score | 6 | 9 |
| Key gate | 3+ scan sustained climb | Rank jump >= 15 + volume >= 1.5x |
| Entry timing | Before the explosion | During the explosion |
| Expected R:R | Higher (enter early) | Lower (enter at breakout) |
| Frequency | More common | Rarer |

## License

MIT — see root repo LICENSE.
