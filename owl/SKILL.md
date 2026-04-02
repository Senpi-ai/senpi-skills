---
name: owl-strategy
description: >-
  OWL v6.0 — Pure Contrarian. Enters against the crowd when SM is
  concentrated, funding is extreme, and price is exhausting.
  Re-crowding exit: if crowd rebuilds stronger, thesis is dead.
  Widest DSL in the fleet.
license: MIT
metadata:
  author: jason-goldberg
  version: "6.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🦉 OWL v6.0 — Pure Contrarian

Wait for the crowd to trap themselves. Enter opposite. Ride the unwind.

## ⛔ CRITICAL AGENT RULES
### RULE 1: Install path is `/data/workspace/skills/owl-strategy/`
### RULE 2: DSL manages trailing. OWL only exits on RE-CROWDING (thesis dead).
### RULE 3: MAX 1 POSITION at a time.
### RULE 4: MAX 2 ENTRIES PER DAY. 6-hour cooldown.
### RULE 5: When holding, check for re-crowding every scan. If crowd rebuilds
stronger (SM up 5+ points AND funding 1.5x worse), close immediately.

## Runtime Setup
```bash
sed -i 's/${WALLET_ADDRESS}/<WALLET>/' /data/workspace/skills/owl-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/owl-strategy/runtime.yaml
openclaw senpi runtime create --path /data/workspace/skills/owl-strategy/runtime.yaml
openclaw senpi runtime list && openclaw senpi status
```

## Bootstrap Gate
Verify runtime + status + scanner cron (5 min, main).
Send: "🦉 OWL v6.0 online. Pure contrarian. Watching for trapped crowds. Silence = no crowding."

## License
MIT — Built by Senpi (https://senpi.ai).
