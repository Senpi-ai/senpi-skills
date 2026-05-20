# 🐈 Bobcat — Big Tech Equity Perp Trend Follower

NVDA / TSLA / AAPL / META / MSFT / GOOGL / AMZN / AMD / MU / INTC / TSM / ORCL as perpetuals on Hyperliquid XYZ. Same names retail knows from their brokerage account, except here with leverage and 23/5 hours.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Key parameters

| Parameter | Value |
|---|---|
| Universe | NVDA, TSLA, AAPL, META, MSFT, GOOGL, AMZN, AMD, MU, INTC, TSM, ORCL (xyz:) |
| Tick interval | 300s (5 min) |
| MIN_SCORE | 5 (of ~9) |
| Leverage | 5x default, max 5x |
| Margin per trade | 20% of equity |
| Slots | 3 |
| Max entries per day | 4 |
| Per-asset cooldown | 240 min (4h) |
| DSL Phase 1 max_loss | 15% |
| DSL Phase 2 | Bison-pattern wide ladder |
| hard_timeout | 48h |

## Install

```bash
mkdir -p /data/workspace/skills/bobcat-strategy/{config,scripts,state,references}
for f in scripts/bobcat-producer.py scripts/bobcat_config.py \
         runtime.yaml SKILL.md README.md references/skill-attribution.md \
         config/bobcat-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/bobcat/$f" \
    -o "/data/workspace/skills/bobcat-strategy/$f"
done

export BOBCAT_WALLET=<your-strategy-wallet>
export SENPI_AUTH_TOKEN=...
export BOBCAT_DECISION_MODEL=<your-preferred-model>

openclaw senpi runtime create --path /data/workspace/skills/bobcat-strategy/runtime.yaml

nohup python3 -u /data/workspace/skills/bobcat-strategy/scripts/bobcat-producer.py \
  > /tmp/bobcat-producer.log 2>&1 &
disown
```

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
