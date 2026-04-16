---
name: orca-strategy
description: >-
  ORCA v3.0 — Gen-1 Vanilla Striker revert. Quality confirmation removed
  (it was buying local tops via second-API-call latency per Orca's own
  self-diagnosis). Pure FIRST_JUMP detection + base Striker scoring +
  volume confirmation. Stalker permanently removed (v2.0 pattern kept).
  DSL exit managed by plugin runtime via runtime.yaml.
license: MIT
metadata:
  author: jason-goldberg
  version: "3.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🐋 ORCA v3.0 — Gen-1 Vanilla Striker

Back to pure FIRST_JUMP explosions.

## v3.0 changelog (fleet-fix batch 4)

Reverted Gen-2 quality confirmation. Orca's own self-diagnosis:
"Gen-2 confirmation adds latency and buys local tops after the move."

- Removed `leaderboard_get_momentum_events` API call
- Removed TCS ELITE/RELIABLE gate and ELITE_BONUS booster
- Removed `contribution_pct_change_4h` acceleration booster
- Back to single API call per scan (`leaderboard_get_markets`)
- Leverage clamping applied to emitted entry (`get_safe_leverage`)

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
8. Send: "🐋 ORCA v3.0 online. Vanilla Striker. Silence = no explosions."

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
| `scripts/orca-scanner.py` | Gen-1 vanilla Striker scanner |
| `scripts/orca_config.py` | Config helper |
| `config/orca-config.json` | Wallet, strategy ID |
| `runtime.yaml` | Runtime YAML for DSL plugin |

---

## License

MIT — Built by Senpi (https://senpi.ai).


---

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.
