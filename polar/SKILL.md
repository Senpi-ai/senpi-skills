---
name: polar-strategy
description: >-
  POLAR v2.0 — ETH Alpha Hunter. The patience benchmark. Thesis exit
  permanently removed. Scanner enters, DSL exits. +19.8% ROE trades
  after removing thesis exit.
license: MIT
metadata:
  author: jason-goldberg
  version: "2.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🐻‍❄️ POLAR v2.0 — ETH Alpha Hunter

One asset. Maximum patience. Scanner enters. DSL exits.

## ⛔ CRITICAL AGENT RULES
### RULE 1: Install path is `/data/workspace/skills/polar-strategy/`
### RULE 2: THE SCANNER DOES NOT EXIT POSITIONS — DSL only.
When the scanner sees an active ETH position, it outputs NO_REPLY.
There is NO thesis re-evaluation. There is NO evaluate_eth_position function.
The DSL is the ONLY exit mechanism.
### RULE 3: MAX 1 POSITION — ETH only.
### RULE 4: MAX 4 ENTRIES PER DAY. 120-minute cooldown.

## Runtime Setup
```bash
sed -i 's/${WALLET_ADDRESS}/<WALLET>/' /data/workspace/skills/polar-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/polar-strategy/runtime.yaml
openclaw senpi runtime create --path /data/workspace/skills/polar-strategy/runtime.yaml
openclaw senpi runtime list && openclaw senpi status
```

## Bootstrap Gate
Verify runtime + status + scanner cron (3 min, main).
CRITICAL: The cron must be configured to call `create_position` via Senpi MCP when the scanner outputs a signal with an entry block.
Send: "🐻‍❄️ POLAR v2.0 online. ETH hunter. Silence = no conviction."

## License
MIT — Built by Senpi (https://senpi.ai).
