---
name: bison-strategy
description: >-
  BISON v2.0 — Macro Conviction Holder. Ultra-patient, BTC/ETH/SOL only.
  Overwhelming SM consensus required. Holds through deep pullbacks.
  24-hour hard timeout. 1 trade per day max. 5x leverage.
license: MIT
metadata:
  author: jason-goldberg
  version: "2.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🦬 BISON v2.0 — Macro Conviction Holder

The patient predator. One trade. Days of patience.

## ⛔ CRITICAL AGENT RULES
### RULE 1: Install path is `/data/workspace/skills/bison-strategy/`
### RULE 2: THE SCANNER DOES NOT EXIT POSITIONS — DSL only.
### RULE 3: MAX 1 POSITION. ONE. Do NOT open a second.
### RULE 4: MAX 1 ENTRY PER DAY. 6-hour cooldown.
### RULE 5: Do NOT close positions early. Bison HOLDS. The DSL manages exits.

## Runtime Setup
```bash
sed -i 's/${WALLET_ADDRESS}/<WALLET>/' /data/workspace/skills/bison-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/bison-strategy/runtime.yaml
openclaw senpi runtime create --path /data/workspace/skills/bison-strategy/runtime.yaml
openclaw senpi runtime list && openclaw senpi status
```

## Bootstrap Gate
On EVERY session start, verify runtime + status + scanner cron (15 min, main).
Send: "🦬 BISON v2.0 online. Macro conviction. Silence = no overwhelming consensus."

## License
MIT — Built by Senpi (https://senpi.ai).
