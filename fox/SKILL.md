---
name: fox-strategy
description: >-
  FOX v3.0 — Contra-Trend Striker. Enters against the 4H trend when SM
  explodes opposite to price. Front-runs reversals. Tighter gates.
license: MIT
metadata:
  author: jason-goldberg
  version: "3.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🦊 FOX v3.0 — Contra-Trend Striker

SM is betting against the trend. Follow them.

## ⛔ CRITICAL AGENT RULES
### RULE 1: Install path is `/data/workspace/skills/fox-strategy/`
### RULE 2: THE SCANNER DOES NOT EXIT POSITIONS — DSL only.
### RULE 3: MAX 2 POSITIONS
### RULE 4: MAX 4 ENTRIES PER DAY, 180-minute cooldown

## Runtime Setup
```bash
sed -i 's/${WALLET_ADDRESS}/<WALLET>/' /data/workspace/skills/fox-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/fox-strategy/runtime.yaml
openclaw senpi runtime create --path /data/workspace/skills/fox-strategy/runtime.yaml
openclaw senpi runtime list && openclaw senpi status
```

## Bootstrap Gate
On EVERY session start, verify runtime + status + scanner cron (90s, main).
Send: "🦊 FOX v3.0 online. Contra-trend Striker. Silence = no reversals."

## License
MIT — Built by Senpi (https://senpi.ai).
