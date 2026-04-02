---
name: raptor-strategy
description: >-
  RAPTOR v2.0 — Hot Streak Follower. Follows quality traders on $5.5M+
  momentum streaks into their strongest position. SM confirmed.
license: MIT
metadata:
  author: jason-goldberg
  version: "2.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🦅 RAPTOR v2.0 — Hot Streak Follower

Find the hot trader. Follow their best trade.

---

## ⛔ CRITICAL AGENT RULES

### RULE 1: Install path is `/data/workspace/skills/raptor-strategy/`
### RULE 2: THE SCANNER DOES NOT EXIT POSITIONS — DSL only.
### RULE 3: MAX 2 POSITIONS
### RULE 4: Verify runtime on every session start
### RULE 5: Never modify parameters
### RULE 6: MAX 4 ENTRIES PER DAY
### RULE 7: 120-minute per-asset cooldown

---

## Runtime Setup

```bash
sed -i 's/${WALLET_ADDRESS}/<WALLET>/' /data/workspace/skills/raptor-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/raptor-strategy/runtime.yaml
openclaw senpi runtime create --path /data/workspace/skills/raptor-strategy/runtime.yaml
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
6. Create scanner cron (3 min, main)
7. Write `config/bootstrap-complete.json`
8. Send: "🦅 RAPTOR v2.0 online. Following hot streaks. Silence = no quality momentum."

---

## Files

| File | Purpose |
|---|---|
| `scripts/raptor-scanner.py` | Hot streak follower scanner |
| `scripts/raptor_config.py` | Config helper |
| `config/raptor-config.json` | Wallet, strategy ID |
| `runtime.yaml` | Runtime YAML for DSL plugin |

---

## License

MIT — Built by Senpi (https://senpi.ai).
