# Turbine v2.0

**Volume generation engine — PRIVATE, internal use only.**

See `SKILL.md` for full documentation.

## Quick reference

| Asset | Target daily volume | Max HL fee burn | Leverage | Slots |
|---|---|---|---|---|
| Mixed (XYZ 70% / HL 30%) | $1.4M (Phase 1) → $3M+ (Phase 2) | ~$170–360/day | 5x | 3 |

## Install

1. Create wallet via Senpi, fund with $1,500+.
2. Copy `config/turbine-config.example.json` to `config/turbine-config.json`, fill in `wallet` + `strategyId`.
3. Set env: `TURBINE_DECISION_MODEL=<cheap model>`, `WALLET_ADDRESS=<wallet>`, `TELEGRAM_CHAT_ID=<id>`.
4. Install runtime: `openclaw senpi runtime create --from runtime.yaml`.
5. Register cron: producer every 60s.
6. Verify: `openclaw senpi runtime list` shows `turbine-tracker-<hash>` running.
