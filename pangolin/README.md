# 🦔 PANGOLIN v1.2 — Extreme Funding Rate Fader (universe expansion)

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

When funding rates are elevated (>20% annualized), the crowd is paying to hold their position. Pangolin enters opposite to the funding direction — collecting funding every 8 hours while waiting for the crowded side to capitulate. Conservative 3-5x leverage, very wide DSL (12-hour hard timeout). **Scans every Hyperliquid perp with OI > $3M** (~60 assets, post-v1.2 expansion).

## v1.2 Changelog (2026-04-16)

- **UNIVERSE EXPANSION.** Removed hardcoded top-20 `ALLOWED_ASSETS` whitelist. Pangolin now scans every instrument with OI > $3M — ~60 assets instead of 20. Addresses Owl's diagnostic finding that extreme funding signals on mid-caps (ZEC/MON/LIT hit >1000% annualized at probe time) were being filtered out.

## v1.1 Changelog

`MIN_FUNDING_RATE` lowered 0.0003 → 0.00015 (40% ann → 20% ann). v1.0 never fired — peak funding in the current regime was only 9% annualized.

See [SKILL.md](SKILL.md) for full setup instructions.

## License

MIT — Built by Senpi (https://senpi.ai).
