---
name: sentinel-strategy
description: >-
  SENTINEL v2.2 — Quality Trader Convergence Scanner. Inverted pipeline:
  find ELITE/RELIABLE traders, see where they converge. When 5+ quality
  traders hold the same asset in the same direction, enter. Batch 4
  widens Phase 2 tiers ([5,10,15] → [15,30,50,75,100]) per Sentinel's
  own rec + rebases starting budget to current equity ($786.60).
license: MIT
metadata:
  author: jason-goldberg
  version: "2.2"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🛡️ SENTINEL v2.2 — Quality Trader Convergence

When the best traders agree, follow them.

## v2.2 changelog (fleet-fix batch 4)

Sentinel was stuck at -21.44% drawdown with daily cap = 1 (pnl-aware
circuit breaker severely restricting entries). 45.2% win rate confirmed
the signal is valid; DSL was bleeding value via slow cuts on 17/23
losers.

- Phase 2 tiers widened to Sentinel's own rec:
  [15/35, 30/60, 50/75, 75/85, 100/92]. Keeps early lock at 15% but
  pushes upper tiers out so winners have room to run.
- `STARTING_BUDGET` 1000.0 → 786.60 (current equity) — unblocks the
  pnl-aware daily cap.

---

## ⛔ CRITICAL AGENT RULES

### RULE 1: Install path is `/data/workspace/skills/sentinel-strategy/`
### RULE 2: THE SCANNER DOES NOT EXIT POSITIONS — DSL only.
### RULE 3: MAX 2 POSITIONS
### RULE 4: Verify runtime on every session start
### RULE 5: Never modify parameters
### RULE 6: MAX 4 ENTRIES PER DAY
### RULE 7: 120-minute per-asset cooldown

---

## Runtime Setup

```bash
sed -i 's/${WALLET_ADDRESS}/<WALLET>/' /data/workspace/skills/sentinel-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/sentinel-strategy/runtime.yaml
openclaw senpi runtime create --path /data/workspace/skills/sentinel-strategy/runtime.yaml
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
6. Create scanner cron (5 min, main)
7. Write `config/bootstrap-complete.json`
8. Send: "🛡️ SENTINEL v2.2 online. Wider Phase 2 tiers. Silence = no consensus."

---

## Files

| File | Purpose |
|---|---|
| `scripts/sentinel-scanner.py` | Quality trader convergence scanner |
| `scripts/sentinel_config.py` | Config helper |
| `config/sentinel-config.json` | Wallet, strategy ID |
| `runtime.yaml` | Runtime YAML for DSL plugin |

---

## License

MIT — Built by Senpi (https://senpi.ai).


---

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.
