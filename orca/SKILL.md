---
name: orca-strategy
description: >-
  ORCA v2.0 — Gen-2 Striker with momentum event quality confirmation.
  FIRST_JUMP detection enhanced with Tier 2 momentum events and TCS
  trader quality tags. Stalker permanently removed.
  DSL exit managed by plugin runtime via runtime.yaml.
license: MIT
metadata:
  author: jason-goldberg
  version: "2.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🐋 ORCA v2.0 — Gen-2 Striker

Explosions confirmed by quality traders.

---

## ⛔ CRITICAL AGENT RULES

### RULE 1: Install path is `/data/workspace/skills/orca-strategy/`
### RULE 2: THE SCANNER DOES NOT EXIT POSITIONS — DSL only.
### RULE 3: MAX 3 POSITIONS
### RULE 4: Verify runtime on every session start
### RULE 5: Never modify parameters
### RULE 6: MAX 6 ENTRIES PER DAY
### RULE 7: 120-minute per-asset cooldown

---

## Runtime Setup

```bash
sed -i 's/${WALLET_ADDRESS}/<WALLET>/' /data/workspace/skills/orca-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/orca-strategy/runtime.yaml
openclaw senpi runtime create --path /data/workspace/skills/orca-strategy/runtime.yaml
openclaw senpi runtime list
openclaw senpi status
```

---

## Bootstrap Gate

On EVERY session start, check `config/bootstrap-complete.json`. If missing:
1. Read senpi-trading-runtime skill
2. Verify Senpi MCP
3. Set wallet and telegram in runtime.yaml
4. Install runtime
5. Verify: `openclaw senpi runtime list` and `openclaw senpi status`
6. Create scanner cron (90s, main)
7. Write `config/bootstrap-complete.json`
8. Send: "🐋 ORCA v2.0 online. Gen-2 Striker. Silence = no quality explosions."

---

## Risk

| Rule | Value |
|---|---|
| Max positions | 3 |
| Max entries/day | 6 |
| Leverage | 7x |
| Cooldown | 120 min per asset |
| Min score | 9 |

---

## Files

| File | Purpose |
|---|---|
| `scripts/orca-scanner.py` | Gen-2 Striker scanner |
| `scripts/orca_config.py` | Config helper |
| `config/orca-config.json` | Wallet, strategy ID |
| `runtime.yaml` | Runtime YAML for DSL plugin |

---

## License

MIT — Built by Senpi (https://senpi.ai).
