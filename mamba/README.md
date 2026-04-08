# 🐍 MAMBA v2.0 — Range-Bound High Water + Regime Protection

Part of the [Senpi Trading Skills Zoo](https://github.com/Senpi-ai/senpi-skills).

## What MAMBA Does

MAMBA detects range-bound conditions on Hyperliquid (tight Bollinger Bands, low ATR, RSI at extremes, declining volume) and enters at support/resistance boundaries. DSL High Water Mode trails the position — capturing both the range bounce and any breakout that escapes the range.

v2.0 adds three protective gates that would have turned v1.0's -$313 loss into an estimated +$30-60 profit.

## v2.0 Changes

| Fix | What it prevents |
|---|---|
| **BTC regime gate** | No longs in bearish macro, no shorts in bullish. Killed 14 losing trades in v1.0. |
| **4-hour per-asset cooldown** | No re-entering an asset after a loss for 4 hours. GOLD was entered 5x in v1.0. |
| **10x leverage hard cap** | No more 15x desperation bets. BTC/XRP 15x shorts lost -$126 in v1.0. |
| **XYZ equities banned** | GOLD, PAXG, NVDA, BRENTOIL accounted for -$80+ in v1.0. |

## Quick Start

1. Deploy `config/mamba-config.json` to your Senpi agent
2. Deploy `scripts/mamba-scanner.py` and `scripts/mamba_config.py`
3. Create scanner cron (5 min, isolated) and DSL cron (3 min, isolated)

## Directory Structure

```
mamba-v2.0/
├── README.md
├── SKILL.md
├── config/
│   └── mamba-config.json
└── scripts/
    ├── mamba-scanner.py
    └── mamba_config.py
```

## v1.0 vs v2.0

| | v1.0 | v2.0 |
|---|---|---|
| Trades/day | ~12 | 4-6 |
| Win rate | 24% | Target 45-55% |
| BTC regime gate | None | Hard block |
| Per-asset cooldown | None | 4 hours after loss |
| Leverage | Uncapped (went to 15x) | Hard cap 10x |
| XYZ equities | Allowed | Banned |
| Net result | -$313 (-31.4%) | Tracking fresh |

## License

MIT — see root repo LICENSE.
