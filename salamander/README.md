# 🦎 Salamander — Pullback Catcher

Buy dips in uptrends, short rallies in downtrends. Salamander only acts when the **4h trend is established**, **price has pulled back 3-7% on 1h**, AND **Smart Money agrees with the trend direction**. Universe: BTC, ETH, SOL. Asymmetric DSL — Phase 1 wider (10% max_loss) to give pullbacks room, Phase 2 tighter to lock fast when the trend resumes.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Key parameters

| Parameter | Value |
|---|---|
| Universe | BTC, ETH, SOL |
| Tick interval | 300s |
| MIN_SCORE | 5 (of ~9) |
| Leverage | 5x default, max 5x |
| Margin per trade | 20% of equity |
| Max entries per day | 2 |
| Per-asset cooldown | 240 min |
| Daily loss limit | 15% |
| Drawdown halt | 20% |
| SM tilt minimum | 55% |
| Pullback band | 3-7% (configurable) |
| Pullback lookback | 24h |
| DSL Phase 1 max_loss | **10%** (wide for pullback room) |
| DSL Phase 2 T0 | **+5% / lock 30%** (tight, lock fast) |
| hard_timeout | 48h |
| weak_peak_cut | 90min / 3% min |

## Install

Same pattern as Hawk — see [Hawk README](../hawk/README.md). Strategy-specific:

```bash
mkdir -p /data/workspace/skills/salamander-strategy/{config,scripts,state,references}
for f in scripts/salamander-producer.py scripts/salamander_config.py \
         runtime.yaml SKILL.md README.md references/skill-attribution.md \
         config/salamander-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/salamander/$f" \
    -o "/data/workspace/skills/salamander-strategy/$f"
done

export SALAMANDER_WALLET=<your-strategy-wallet>
export SENPI_AUTH_TOKEN=...
export SALAMANDER_DECISION_MODEL=<your-preferred-model>

openclaw senpi runtime create --path /data/workspace/skills/salamander-strategy/runtime.yaml

nohup python3 -u /data/workspace/skills/salamander-strategy/scripts/salamander-producer.py \
  > /tmp/salamander-producer.log 2>&1 &
disown
```

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
