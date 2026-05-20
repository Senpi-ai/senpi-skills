# 🦅 Hawk — 4h Breakout / Breakdown (SM-confirmed)

LONG when price breaks above the 7-day high AND Smart Money is > 55% long. SHORT when price breaks below the 7-day low AND SM is > 55% short. Universe: BTC, ETH, SOL. **Tight DSL** — failed breakouts cut at 8% max_loss; winning ones lock fast at +5%.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Key parameters

| Parameter | Value |
|---|---|
| Universe | BTC, ETH, SOL |
| Tick interval | 300s (5 min) |
| MIN_SCORE | 5 (out of ~9) |
| Leverage | 5x default, max 5x |
| Margin per trade | 20% of equity |
| Max entries per day | 2 |
| Per-asset cooldown | 240 min (4h) |
| Daily loss limit | 15% |
| Drawdown halt | 20% |
| SM tilt minimum | 55% |
| Breakout lookback | 168h (7 days) |
| DSL Phase 1 max_loss | **8%** (tight) |
| DSL Phase 1 retrace | **5** (tight) |
| DSL Phase 2 T0 | **+5% / lock 30%** (fast) |
| hard_timeout | 24h |
| weak_peak_cut | 60min / 3% min |

## Install

Same install pattern as Beaver — see [Beaver README](../beaver/README.md) for the runtime-plugin registration. Strategy-specific commands:

```bash
mkdir -p /data/workspace/skills/hawk-strategy/{config,scripts,state,references}
for f in scripts/hawk-producer.py scripts/hawk_config.py \
         runtime.yaml SKILL.md README.md references/skill-attribution.md \
         config/hawk-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/hawk/$f" \
    -o "/data/workspace/skills/hawk-strategy/$f"
done

export HAWK_WALLET=<your-strategy-wallet>
export SENPI_AUTH_TOKEN=...
export HAWK_DECISION_MODEL=<your-preferred-model>

openclaw senpi runtime create --path /data/workspace/skills/hawk-strategy/runtime.yaml

nohup python3 -u /data/workspace/skills/hawk-strategy/scripts/hawk-producer.py \
  > /tmp/hawk-producer.log 2>&1 &
disown
```

## Verification

```bash
sleep 320
tail -3 /tmp/hawk-producer.log | jq '._hawk_producer_version, .note // null, .best // null'
```

Most ticks output `"WAITING — no breakout with SM agreement on universe"` — that's normal. Hawk is a low-frequency strategy by design.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
