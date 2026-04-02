---
name: grizzly-strategy
description: >-
  GRIZZLY HORRIBILIS — BTC conviction-scaled leverage. 7x to 40x based
  on conviction score. Same thesis as Grizzly v3.0. Always DSL protected.
license: MIT
metadata:
  author: jason-goldberg
  version: "horribilis-1.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🐻 GRIZZLY HORRIBILIS — The Most Aggressive Bear

Same thesis. Conviction-scaled leverage. Always DSL protected.

## ⛔ CRITICAL AGENT RULES
### RULE 1: Install path is `/data/workspace/skills/grizzly-strategy/`
### RULE 2: THE SCANNER DOES NOT EXIT POSITIONS — DSL only.
### RULE 3: MAX 1 POSITION — BTC only.
### RULE 4: MAX 2 ENTRIES PER DAY. 180-minute cooldown.
### RULE 5: Leverage is set by the scanner based on conviction. Do NOT override.

## Runtime Setup
```bash
sed -i 's/${WALLET_ADDRESS}/<WALLET>/' /data/workspace/skills/grizzly-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/grizzly-strategy/runtime.yaml
openclaw senpi runtime create --path /data/workspace/skills/grizzly-strategy/runtime.yaml
openclaw senpi runtime list && openclaw senpi status
```

## Bootstrap Gate
On EVERY session start, verify runtime + status + scanner cron (3 min, main).
Send: "🐻 HORRIBILIS online. BTC hunter. Conviction-scaled leverage. DSL protects everything."

## License
MIT — Built by Senpi (https://senpi.ai).
