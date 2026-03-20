# HYDRA v1.0

**Multi-Source Squeeze Scanner for Hyperliquid**

HYDRA detects crowded positions across the crypto market using 6 independent signal sources, enters trades with conviction-based sizing, and manages them with the Senpi DSL v1.1.1 trailing stop system with exchange-synced stop-losses.

Inspired by the community's Liquidity Hunter v3.0 (net profitable over 48 trades, TRUMP +35.8% best trade). HYDRA keeps the signal architecture and adds Senpi infrastructure: exchange-level SL sync, atomic DSL state generation, 10x leverage cap, and the standard skills zoo patterns.

## Quick Start

Scanner cron (systemEvent):
```
python3 /data/workspace/skills/hydra/scripts/hydra-scanner.py
```

DSL cron (agentTurn):
```
python3 /data/workspace/skills/dsl-dynamic-stop-loss/scripts/dsl-v5.py --state-dir /data/workspace/skills/hydra/state
```

Monitor cron (systemEvent):
```
python3 /data/workspace/skills/hydra/scripts/hydra-monitor.py
```

## Directory Structure

```
hydra-v1.0/
├── README.md
├── SKILL.md                     # Full spec — the agent reads this
├── config/
│   └── hydra-config.json        # All configurable parameters
└── scripts/
    ├── hydra-scanner.py         # 6-source scoring + signal output
    ├── hydra-monitor.py         # Independent watchdog (3rd cron)
    └── hydra_config.py          # Standalone config helper
```

## Signal Sources (6)

| # | Source | Weight | Role |
|---|--------|--------|------|
| 1 | FDD — Funding Divergence | 30/110 | Primary gate. No trade without FDD. |
| 2 | LCD — Liquidation Cascade | 25/110 | Nearby liquidation clusters |
| 3 | OIS — Open Interest Surge | 20/110 | New leverage entering/exiting |
| 4 | MED — Momentum Exhaustion | -10 to +5 | Dual role: confirmation OR penalty |
| 5 | EM — Emerging Movers | -8 to +15 | SM consensus from leaderboard |
| 6 | OPP — Opportunity Scanner | -999 to +10 | Final gate: hourly trend alignment |

## Key Differences from Liquidity Hunter

| Dimension | HYDRA | Liquidity Hunter |
|-----------|-------|-----------------|
| Exchange SL | Synced via DSL engine | None (software only) |
| Leverage cap | 10x | 50-75x |
| DSL format | v1.1.1 (standard zoo) | Custom format |
| DSL state | Scanner generates atomically | Created after position open |
| Config helper | Self-contained | wolf_config dependency |
| XYZ equities | Banned | Not filtered |
| Cooldown | 120 min per-asset | 120 min per-asset |

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
Source: https://github.com/Senpi-ai/senpi-skills
