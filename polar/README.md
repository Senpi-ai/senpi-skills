# 🐻‍❄️ POLAR v2.0 — ETH Alpha Hunter

The patience benchmark. +5.1% ROE. Three consecutive wins at +19.8%, +18.4%, +2.2% ROE after removing thesis exit.

Single-asset ETH lifecycle hunter. When SM commits 80%+ to ETH with trend confirmation, enter and let the DSL trail. The proof that scanner enters, DSL exits.

## Quick Start
1. Deploy on Senpi x Railway
2. Tell your agent: "Install the latest polar-strategy from senpi-skills GitHub"
3. Verify: `openclaw senpi runtime list` and `openclaw senpi status`
4. CRITICAL: Make sure the scanner cron executes trades (see SKILL.md)

## Key Settings
| Setting | Value |
|---|---|
| Asset | ETH only |
| Leverage | 7x |
| Max positions | 1 |
| Min score | 8 |
| DSL | Lifecycle hunter (180m timeout, no time cuts) |

## License
MIT — Copyright 2026 Senpi (https://senpi.ai)
