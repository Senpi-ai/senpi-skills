# 🐆 CHEETAH v5.1.1 APEX — Multi-signal confluence sniper

v5.1.1 adds leverage safety (clamps to asset max via
`strategy_get_asset_trading_limits`) and inner-order success validation
to eliminate phantom ENTRY logs on CREATE_INVALID_LEVERAGE rejections.

Top performer in the Senpi Predators fleet at +7.6% ROE (v2.0 peak).

Hunts HYPE exclusively using SM commitment as the primary signal. When SM concentration exceeds threshold with 4H trend alignment, Cheetah enters. BTC trend is a conviction booster, not a hard gate. HYPE-specific wide DSL tiers give volatile HYPE positions room to breathe.

## Quick Start
1. Deploy on Senpi x Railway
2. Tell your agent: "Install the latest cheetah-strategy from senpi-skills GitHub"
3. Verify: `openclaw senpi runtime list` and `openclaw senpi status`
4. CRITICAL: Make sure the scanner cron executes trades (see SKILL.md)

## Key Settings
| Setting | Value |
|---|---|
| Asset | HYPE only |
| Leverage | 7x |
| Max positions | 1 |
| Min score | 8 |
| DSL | Lifecycle hunter with HYPE-wide tiers |

## License
MIT — Copyright 2026 Senpi (https://senpi.ai)
