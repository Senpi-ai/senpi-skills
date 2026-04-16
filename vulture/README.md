# 🦅 Vulture v2.0 — Long-Tail Momentum Rider

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

**COMPLETE REWRITE from v1.0.** Scans 25+ small/mid-cap Hyperliquid perps (HEMI, WLD, MON, XPL, AIXBT, ARB, ASTER, ZEC, LIT, TAO, etc.) that no other Senpi predator covers. Follows SM direction when confluence is strong. Hold winners for days (7-day hard_timeout), cut losers fast (60-min dead_weight_cut). Built from the #1 Arena winner's 3-week playbook (38.6% win rate, 6.15x profit factor).

## Install

```bash
mkdir -p /data/workspace/skills/vulture-strategy/{config,scripts,state}

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/vulture/runtime.yaml -o /data/workspace/skills/vulture-strategy/runtime.yaml
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/vulture/SKILL.md -o /data/workspace/skills/vulture-strategy/SKILL.md
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/vulture/config/vulture-config.json -o /data/workspace/skills/vulture-strategy/config/vulture-config.json
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/vulture/scripts/vulture-scanner.py -o /data/workspace/skills/vulture-strategy/scripts/vulture-scanner.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/vulture/scripts/vulture_config.py -o /data/workspace/skills/vulture-strategy/scripts/vulture_config.py
```

## Configure

```bash
sed -i 's/${WALLET_ADDRESS}/<YOUR_STRATEGY_WALLET>/' /data/workspace/skills/vulture-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<YOUR_TELEGRAM_CHAT_ID>/' /data/workspace/skills/vulture-strategy/runtime.yaml
```

## Install runtime + create scanner cron

```bash
openclaw senpi runtime create --path /data/workspace/skills/vulture-strategy/runtime.yaml
openclaw senpi runtime list
# Create 3-minute cron: python3 /data/workspace/skills/vulture-strategy/scripts/vulture-scanner.py
```

## Key parameters

| Parameter | Value |
|---|---|
| Universe | 25 small/mid-cap perps (see SKILL.md) |
| Max positions | 2 concurrent |
| Leverage | 3-7x (score-scaled) |
| hard_timeout | 7 days |
| dead_weight_cut | 60 min |
| MIN_SCORE | 7 |
| Cooldown | 4h per asset |

## License

MIT — Built by Senpi (https://senpi.ai).
