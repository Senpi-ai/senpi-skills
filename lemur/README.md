# 🦤 Lemur — Pre-IPO Perpetual (IPOP) Trend Follower

Trade pre-IPO companies as perpetuals on Hyperliquid XYZ. Auto-discovers IPOPs via the trade.xyz funding signature. Today: `xyz:SPCX`. Auto-expands when trade.xyz lists Anthropic, OpenAI, Stripe, etc.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Key parameters

| Parameter | Value |
|---|---|
| Universe | Auto-discovered IPOPs (funding ≤ 1e-7, max_leverage ≤ 5, daily vol ≥ $100K) |
| Today's universe | `[xyz:SPCX]` |
| Tick interval | 900s (15 min — IPOPs move slow per Discovery Bounds) |
| MIN_SCORE | 5 (of ~9) |
| Leverage | 3x default, auto-capped to instrument's own max (typically 5x) |
| Margin per trade | 15% of equity |
| Slots | 2 |
| Max entries per day | 3 |
| Per-asset cooldown | 360 min (6h) |
| DSL Phase 1 max_loss | 10% |
| DSL Phase 2 | Bison-pattern wide ladder (T0 lock 0 → T5 lock 85) |
| Time cuts | All DISABLED |

## Install

```bash
mkdir -p /data/workspace/skills/lemur-strategy/{config,scripts,state,references}
for f in scripts/lemur-producer.py scripts/lemur_config.py \
         runtime.yaml SKILL.md README.md references/skill-attribution.md \
         config/lemur-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/lemur/$f" \
    -o "/data/workspace/skills/lemur-strategy/$f"
done

export LEMUR_WALLET=<your-strategy-wallet>
export SENPI_AUTH_TOKEN=...
export LEMUR_DECISION_MODEL=<your-preferred-model>

openclaw senpi runtime create --path /data/workspace/skills/lemur-strategy/runtime.yaml

nohup python3 -u /data/workspace/skills/lemur-strategy/scripts/lemur-producer.py \
  > /tmp/lemur-producer.log 2>&1 &
disown
```

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
