# 🦝 Raccoon — Weekend XYZ Reconciliation

ONLY trades during the trade.xyz no-external-price weekend window (Fri 22:00 UTC → Mon 00:00 UTC). Captures the Monday-open snap-back when external pricing resumes. Broad XYZ universe (excludes IPOPs).

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Key parameters

| Parameter | Value |
|---|---|
| Universe | All xyz: (not delisted, max_leverage >= 10, daily vol >= $1M) |
| Active window | Fri 22:00 UTC → Mon 00:00 UTC (UTC clock) |
| Tick interval | 300s (5 min during weekend) |
| MIN_SCORE | 5 (of ~9) |
| Leverage | 3x default, max 5x |
| Margin per trade | 15% of equity |
| Slots | 3 |
| Max entries per day | 4 |
| Per-asset cooldown | 480 min (8h) |
| Min directional move | 2% over 48h |
| DSL Phase 1 max_loss | 12% |
| DSL Phase 2 T0 | +5% / lock 30% (lock fast) |
| hard_timeout | 48h (Monday-open exit) |

## Install

```bash
mkdir -p /data/workspace/skills/raccoon-strategy/{config,scripts,state,references}
for f in scripts/raccoon-producer.py scripts/raccoon_config.py \
         runtime.yaml SKILL.md README.md references/skill-attribution.md \
         config/raccoon-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/raccoon/$f" \
    -o "/data/workspace/skills/raccoon-strategy/$f"
done

export RACCOON_WALLET=<your-strategy-wallet>
export SENPI_AUTH_TOKEN=...
export RACCOON_DECISION_MODEL=<your-preferred-model>

openclaw senpi runtime create --path /data/workspace/skills/raccoon-strategy/runtime.yaml

nohup python3 -u /data/workspace/skills/raccoon-strategy/scripts/raccoon-producer.py \
  > /tmp/raccoon-producer.log 2>&1 &
disown
```

Run continuously — the producer self-gates to the weekend window. Outside Fri 22:00 → Mon 00:00 UTC, ticks output `OUTSIDE_WEEKEND_WINDOW` and consume minimal resources.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
