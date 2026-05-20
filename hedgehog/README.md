# 🦔 Hedgehog — Major Crypto Basket (BTC + ETH + SOL)

Equal-weight crypto basket. Each asset independently evaluated — BTC long, ETH short, SOL idle if that's what the signals say. Per-position DSL so one going wrong doesn't drag the others.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Key parameters

| Parameter | Value |
|---|---|
| Universe | BTC, ETH, SOL |
| Tick interval | 300s |
| MIN_SCORE | 5 |
| Leverage | 5x |
| Margin per leg | 10% of equity (up to 3 legs = 30% max committed) |
| Slots | 3 |
| Max entries per day | 4 |
| Per-asset cooldown | 240 min |
| DSL Phase 1 max_loss | 15% per position |
| DSL Phase 2 | Bison-pattern wide ladder |
| hard_timeout | 48h |

## Install

```bash
mkdir -p /data/workspace/skills/hedgehog-strategy/{config,scripts,state,references}
for f in scripts/hedgehog-producer.py scripts/hedgehog_config.py \
         runtime.yaml SKILL.md README.md references/skill-attribution.md \
         config/hedgehog-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/hedgehog/$f" \
    -o "/data/workspace/skills/hedgehog-strategy/$f"
done

export HEDGEHOG_WALLET=<your-strategy-wallet>
export SENPI_AUTH_TOKEN=...
export HEDGEHOG_DECISION_MODEL=<your-preferred-model>

openclaw senpi runtime create --path /data/workspace/skills/hedgehog-strategy/runtime.yaml

nohup python3 -u /data/workspace/skills/hedgehog-strategy/scripts/hedgehog-producer.py \
  > /tmp/hedgehog-producer.log 2>&1 &
disown
```

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
