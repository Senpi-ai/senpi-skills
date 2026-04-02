---
name: phoenix-strategy
description: >-
  PHOENIX v2.0 — Contribution Velocity Scanner (Hardened). Same battle-tested
  signal. Fixed trade counter. DSL plugin exits.
license: MIT
metadata:
  author: jason-goldberg
  version: "2.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🔥 PHOENIX v2.0 — Contribution Velocity Scanner

SM profit velocity diverging from price. The signal works. The infrastructure is fixed.

## ⛔ CRITICAL AGENT RULES
### RULE 1: Install path is `/data/workspace/skills/phoenix-strategy/`
### RULE 2: THE SCANNER DOES NOT EXIT POSITIONS — DSL only.
### RULE 3: MAX 3 POSITIONS
### RULE 4: MAX 4 ENTRIES PER DAY — the counter increments in the scanner. Do NOT bypass.
### RULE 5: Never modify parameters.

## Runtime Setup
```bash
sed -i 's/${WALLET_ADDRESS}/<WALLET>/' /data/workspace/skills/phoenix-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/phoenix-strategy/runtime.yaml
openclaw senpi runtime create --path /data/workspace/skills/phoenix-strategy/runtime.yaml
openclaw senpi runtime list && openclaw senpi status
```

## Bootstrap Gate
On EVERY session start, verify runtime + status + scanner cron (2 min, main).
VERIFY trade counter: `cat /data/workspace/skills/phoenix-strategy/state/trade-counter.json`
If the date is not today, delete it and let the scanner recreate it.
Send: "🔥 PHOENIX v2.0 online. Hunting divergences. Counter: X/4."

## License
MIT — Built by Senpi (https://senpi.ai).
