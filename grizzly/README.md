# 🐻 GRIZZLY v4.0 — BTC Contrarian

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

Single-asset BTC contrarian scanner. Trades AGAINST smart money consensus when BTC is overextended and the move is exhausting. Single entry, no pyramiding. v4.0 is a complete direction flip from v3.x (fleet audit found the original momentum-following signal was inverted — 81.8% win rate on flipped entries).

## What Grizzly does

- **Scans BTC** every 3 minutes via `leaderboard_get_markets`
- **Identifies SM dominant direction** on BTC
- **Verifies 4H extension** — price must be meaningfully extended in the SM direction
- **Verifies 15m velocity is not building** — the move must be exhausting, not still developing
- **Scores the contrarian setup** across SM concentration, move exhaustion, velocity, 4H structure
- **Fires the FADE entry** in the opposite direction at 7x leverage (10x at score 10+)
- **Hands off to DSL** — single entry, no scale-ins (use Grizzly-Horribilis if you want pyramiding)

## Why a second direction flip

The prior Grizzly versions (v1.x through v3.x) followed SM consensus. Fleet audit on 2026-04-10 found that signal was inverted: the multi-timeframe confirmation gate meant Grizzly entered AFTER moves were exhausted. Testing showed an 81.8% win rate if the direction was flipped on 11 recent trades. v4.0 embraces the contrarian thesis full-time.

## Grizzly vs Grizzly-Horribilis

Both are BTC contrarian scanners with nearly identical signal logic. They're an intentional A/B:

- **Grizzly**: single entry, discrete binary bet. Simpler execution.
- **Grizzly-Horribilis**: pyramiding, adds to winners progressively. More complex but lets winners compound.

Deploy one or both (on separate wallets) based on preference.

## Install

```bash
mkdir -p /data/workspace/skills/grizzly-strategy/{config,scripts,state}

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/grizzly/runtime.yaml -o /data/workspace/skills/grizzly-strategy/runtime.yaml
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/grizzly/SKILL.md -o /data/workspace/skills/grizzly-strategy/SKILL.md
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/grizzly/config/grizzly-config.json -o /data/workspace/skills/grizzly-strategy/config/grizzly-config.json
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/grizzly/scripts/grizzly-scanner.py -o /data/workspace/skills/grizzly-strategy/scripts/grizzly-scanner.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/grizzly/scripts/grizzly_config.py -o /data/workspace/skills/grizzly-strategy/scripts/grizzly_config.py
```

## Configure

```bash
sed -i 's/${WALLET_ADDRESS}/<YOUR_STRATEGY_WALLET>/' /data/workspace/skills/grizzly-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<YOUR_TELEGRAM_CHAT_ID>/' /data/workspace/skills/grizzly-strategy/runtime.yaml
```

Environment variables: `GRIZZLY_WALLET`, `GRIZZLY_STRATEGY_ID`.

## Install the runtime in OpenClaw

```bash
openclaw senpi runtime create --path /data/workspace/skills/grizzly-strategy/runtime.yaml
openclaw senpi runtime list
```

## Verify

```bash
python3 /data/workspace/skills/grizzly-strategy/scripts/grizzly-scanner.py
```

Expected: clean exit, JSON output. Most likely first run shows a heartbeat — contrarian setups require specific gate conditions.

## Run on a recurring schedule

```bash
nohup bash -c 'while true; do python3 /data/workspace/skills/grizzly-strategy/scripts/grizzly-scanner.py >> /tmp/grizzly-loop.log 2>&1; sleep 180; done' > /tmp/grizzly-nohup.log 2>&1 &

ps aux | grep grizzly-scanner | grep -v grep
tail -5 /tmp/grizzly-loop.log
```

3-minute cadence. Zero LLM wake cost.

## Key settings

| Setting | Value | Notes |
|---|---|---|
| Asset | BTC | Single-asset focus |
| Max positions | 1 | No parallel bets |
| Margin per trade | 50% | High conviction commits high capital |
| Leverage | 7x / 10x | Score-scaled, fleet cap |
| Min score | 8 | Tunable |
| Per-asset cooldown | 180 min | Patience |
| Same-direction cooldown | 60 min | Prevents chasing |

## Troubleshooting

**Scanner trades OPPOSITE to expected direction:** That's the thesis. Grizzly v4.0 is contrarian by design.

**Scanner imports fail:** Make sure both `grizzly-scanner.py` AND `grizzly_config.py` are in `scripts/`.

**Heartbeats constantly:** Normal. The contrarian gate requires SM consensus AND 4H extension AND 15m velocity exhausting AND score ≥ 8. This is a rare confluence.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai). BTC Contrarian.
