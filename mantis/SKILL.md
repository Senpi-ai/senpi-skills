---
name: mantis-strategy
description: >-
  MANTIS v4.0 — Striker-Only SM Explosion Scanner. Stalker removed.
  Detects violent rank jumps across 50+ markets. Fast-cycling DSL.
  DSL exit managed by plugin runtime via runtime.yaml.
license: MIT
metadata:
  author: jason-goldberg
  version: "4.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🦗 MANTIS v4.0 — Striker-Only SM Explosion Scanner

Stalker is dead. Only explosions.

---

## ⛔ CRITICAL AGENT RULES

### RULE 1: Install path is `/data/workspace/skills/mantis-strategy/`
### RULE 2: THE SCANNER DOES NOT EXIT POSITIONS — DSL only.
### RULE 3: MAX 3 POSITIONS
### RULE 4: Verify runtime on every session start
### RULE 5: Never modify parameters
### RULE 6: MAX 6 ENTRIES PER DAY
### RULE 7: 120-minute per-asset cooldown

---

## Runtime Setup

```bash
sed -i 's/${WALLET_ADDRESS}/<WALLET>/' /data/workspace/skills/mantis-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/mantis-strategy/runtime.yaml
openclaw senpi runtime create --path /data/workspace/skills/mantis-strategy/runtime.yaml
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
8. Send: "🦗 MANTIS v4.0 online. Striker-only. Silence = no explosions."

---

## Risk

| Rule | Value |
|---|---|
| Max positions | 3 |
| Max entries/day | 6 |
| Leverage | 7x |
| Cooldown | 120 min per asset |
| Min Striker score | 9 |

---

## Files

| File | Purpose |
|---|---|
| `scripts/mantis-scanner.py` | Striker-only SM explosion scanner |
| `scripts/mantis_config.py` | Config helper |
| `config/mantis-config.json` | Wallet, strategy ID |
| `runtime.yaml` | Runtime YAML for DSL plugin (fast-cycling) |

---

## License

MIT — Built by Senpi (https://senpi.ai).
