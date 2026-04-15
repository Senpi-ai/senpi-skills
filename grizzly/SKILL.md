---
name: grizzly-strategy
description: >-
  GRIZZLY v4.0 — BTC Contrarian (SM Exhaustion Fader). Single-asset BTC
  scanner that trades AGAINST Smart Money consensus when the move is
  exhausted. A/B variable vs Grizzly-Horribilis: Grizzly = single entry,
  Horribilis = pyramids into winners. v4.0 is a direction flip from v3.x
  after fleet audit found the momentum-following signal was inverted.
license: MIT
metadata:
  author: jason-goldberg
  version: "4.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🐻 GRIZZLY v4.0 — BTC Contrarian

Smart money goes one way on BTC. Grizzly goes the other. Single entry, no pyramiding.

## Thesis

BTC is the most-watched asset on Hyperliquid, and when top-trader consensus reaches extreme levels after a significant move, the unwind is typically violent and profitable for counter-trend traders. Grizzly fades the crowd when:

1. **Smart money consensus is strong** on one direction (pct_of_top_traders_gain ≥ threshold, trader count ≥ threshold)
2. **The 4H move is already extended** in that direction (overextension setup)
3. **15m velocity is no longer building** (the move is exhausting)
4. **A consolidation/reversal pattern is forming** on the 1H/4H

Grizzly then enters in the **OPPOSITE** direction to SM consensus.

## v4.0 — DIRECTION FLIP

Fleet analysis on 2026-04-10 found that Grizzly's earlier SM-consensus momentum signal was perfectly inverted on BTC. The multi-timeframe confirmation requirement meant the scanner systematically entered AFTER the move was exhausted — buying tops and selling bottoms. An inversion test on 11 trades showed **81.8% win rate if direction were flipped**.

v4.0 embraces that finding:

- **Trade OPPOSITE to SM consensus direction** (was: trade with consensus)
- **Leverage tiers aligned with Grizzly-Horribilis** — 7x base, 10x at score 10+
- **Added MOVE_EXHAUSTION penalty → bonus** (was missing in v3.x, Horribilis had it)
- **15m velocity tiers simplified** — less spike-chasing
- **1h acceleration aligned with Horribilis**
- **Added same-direction cooldown** — 60 min after any trade to prevent chasing
- **Fixed resting order filter** — now correctly ignores reduceOnly DSL stops

## A/B vs Grizzly-Horribilis

Both Grizzly and Grizzly-Horribilis are BTC contrarian faders with nearly identical signal logic. The difference:

| | Grizzly | Grizzly-Horribilis |
|---|---|---|
| Entry style | Single full-size entry | Pyramids (scales into winners) |
| Position complexity | Simpler, one-shot | More complex, multi-leg |
| Risk profile | Discrete binary bet | Progressive exposure |
| Best for | Operators who want simple execution | Operators comfortable with pyramid sizing |

Deploy one or the other (or both on separate wallets for A/B comparison), not multiple of the same variant.

## Key settings

| Setting | Value | Why |
|---|---|---|
| Asset | BTC | Single-asset focus |
| Max positions | 1 | No parallel bets |
| Margin per trade | 50% | High conviction commits high capital |
| Leverage | 7x base, 10x at score 10+ | Fleet cap (H12 audit) |
| Min score | 8 | Tunable |
| Per-asset cooldown | 180 min | Patience between trades |
| Same-direction cooldown | 60 min | Prevents chasing |
| Max daily entries | 3 (dynamic cap aware) | Quality over quantity |

## ⛔ Critical agent rules

1. **Install path** is `/data/workspace/skills/grizzly-strategy/`
2. **THE SCANNER DOES NOT EXIT POSITIONS** — DSL handles all exits
3. **MAX 1 POSITION at a time**
4. **BTC ONLY** — no multi-asset
5. **Contrarian direction inversion is intentional** — Grizzly trades OPPOSITE to SM by design
6. **Respect per-asset and same-direction cooldowns**
7. **Verify runtime on every session start**

## Setup

```bash
mkdir -p /data/workspace/skills/grizzly-strategy/{config,scripts,state}

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/grizzly/runtime.yaml \
  -o /data/workspace/skills/grizzly-strategy/runtime.yaml

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/grizzly/SKILL.md \
  -o /data/workspace/skills/grizzly-strategy/SKILL.md

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/grizzly/config/grizzly-config.json \
  -o /data/workspace/skills/grizzly-strategy/config/grizzly-config.json

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/grizzly/scripts/grizzly-scanner.py \
  -o /data/workspace/skills/grizzly-strategy/scripts/grizzly-scanner.py

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/grizzly/scripts/grizzly_config.py \
  -o /data/workspace/skills/grizzly-strategy/scripts/grizzly_config.py
```

Set wallet and chat ID:

```bash
sed -i 's/${WALLET_ADDRESS}/<YOUR_STRATEGY_WALLET>/' /data/workspace/skills/grizzly-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<YOUR_TELEGRAM_CHAT_ID>/' /data/workspace/skills/grizzly-strategy/runtime.yaml
```

Install runtime:

```bash
openclaw senpi runtime create --path /data/workspace/skills/grizzly-strategy/runtime.yaml
openclaw senpi runtime list
```

Verify:

```bash
python3 /data/workspace/skills/grizzly-strategy/scripts/grizzly-scanner.py
```

Run on recurring schedule:

```bash
nohup bash -c 'while true; do python3 /data/workspace/skills/grizzly-strategy/scripts/grizzly-scanner.py >> /tmp/grizzly-loop.log 2>&1; sleep 180; done' > /tmp/grizzly-nohup.log 2>&1 &
```

## Best for

- Operators who want a BTC-only contrarian with simple single-entry execution
- Counter-trend traders who believe momentum chasing is a losing edge
- Diversification pair with Grizzly-Horribilis for A/B comparison on pyramiding vs single-entry

## License

MIT — Copyright 2026 Senpi (https://senpi.ai). BTC Contrarian.
