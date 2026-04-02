---
name: grizzly-strategy
description: >-
  GRIZZLY v3.0 — BTC Alpha Hunter. Tightened configs. 7x leverage.
  360-minute timeout. No thesis exit. Plugin runtime DSL.
license: MIT
metadata:
  author: jason-goldberg
  version: "3.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🐻 GRIZZLY v3.0 — BTC Alpha Hunter

One asset. Maximum patience. DSL exits.

## ⛔ CRITICAL AGENT RULES
### RULE 1: Install path is `/data/workspace/skills/grizzly-strategy/`
### RULE 2: THE SCANNER DOES NOT EXIT POSITIONS — DSL only.
### RULE 3: MAX 1 POSITION — BTC only.
### RULE 4: MAX 2 ENTRIES PER DAY. 180-minute cooldown.

## Runtime Setup
```bash
sed -i 's/${WALLET_ADDRESS}/<WALLET>/' /data/workspace/skills/grizzly-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/grizzly-strategy/runtime.yaml
openclaw senpi runtime create --path /data/workspace/skills/grizzly-strategy/runtime.yaml
openclaw senpi runtime list && openclaw senpi status
```

## Bootstrap Gate
On EVERY session start, verify runtime + status + scanner cron (3 min, main).
Send: "🐻 GRIZZLY v3.0 online. BTC hunter. Silence = no conviction."

## License
MIT — Built by Senpi (https://senpi.ai).
