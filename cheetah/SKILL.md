---
name: cheetah-strategy
description: >-
  CHEETAH v2.0 — HYPE Predator. SM commitment as primary signal.
  Top performer at +7.6%. All fleet fixes applied.
license: MIT
metadata:
  author: jason-goldberg
  version: "2.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🐆 CHEETAH v2.0 — HYPE Predator

SM commits. Cheetah pounces. DSL trails.

## ⛔ CRITICAL AGENT RULES
### RULE 1: Install path is `/data/workspace/skills/cheetah-strategy/`
### RULE 2: THE SCANNER DOES NOT EXIT POSITIONS — DSL only.
### RULE 3: MAX 1 POSITION — HYPE only.
### RULE 4: MAX 4 ENTRIES PER DAY. 90-minute cooldown.

## Runtime Setup
```bash
sed -i 's/${WALLET_ADDRESS}/<WALLET>/' /data/workspace/skills/cheetah-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/cheetah-strategy/runtime.yaml
openclaw senpi runtime create --path /data/workspace/skills/cheetah-strategy/runtime.yaml
openclaw senpi runtime list && openclaw senpi status
```

## Bootstrap Gate
Verify runtime + status + scanner cron (90s, main).
CRITICAL: The cron must be configured to call `create_position` via Senpi MCP when the scanner outputs a signal with an entry block. If the cron just runs the scanner and exits, trades will never execute.
Send: "🐆 CHEETAH v2.0 online. HYPE predator. Silence = no SM commitment."

## License
MIT — Built by Senpi (https://senpi.ai).
