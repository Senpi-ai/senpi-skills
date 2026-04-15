---
name: spider-strategy
description: >-
  SPIDER v1.0 — Elite Convergence Scanner. Enters only when 2+ ELITE/RELIABLE
  traders with SNIPER/AGGRESSIVE risk independently converge on the same asset
  and direction, with 15m SM velocity confirmation. Two-phase architecture:
  convergence map (5 min) + velocity trigger (90 sec).
  DSL exit managed by plugin runtime via runtime.yaml.
license: MIT
metadata:
  author: jason-goldberg
  version: "1.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🕷️ SPIDER v1.0 — Elite Convergence Scanner

Two or more elite traders agree. Velocity confirms. Enter with conviction.

---

## ⛔ CRITICAL AGENT RULES

### RULE 1: Install path is `/data/workspace/skills/spider-strategy/`
### RULE 2: THE SCANNER DOES NOT EXIT POSITIONS — DSL only.
### RULE 3: MAX 1 POSITION
### RULE 4: Verify runtime on every session start
### RULE 5: Never modify parameters
### RULE 6: MAX 3 ENTRIES PER DAY
### RULE 7: 120-minute cooldown between entries

---

## How It Works

Spider operates in two phases:

**Phase 1 — Convergence Map (every 5 min):**
1. `discovery_get_top_traders` → weekly top traders filtered by TCS=ELITE/RELIABLE
   + Risk=SNIPER/AGGRESSIVE + ROI >3% + has open positions
2. For each qualifying trader → `leaderboard_get_trader_positions` → get their positions
3. Find assets where 2+ qualifying traders are positioned in same direction
4. Cache convergence map to state file

**Phase 2 — Velocity Trigger (every 90 sec):**
1. Read cached convergence map
2. `leaderboard_get_markets` → check 15m velocity on convergence assets
3. Score: convergence quality + trader quality + velocity alignment
4. Enter highest-scoring convergence above threshold

**The convergence tells you WHAT. The velocity tells you WHEN.**

---

## Scoring

| Signal | Points | Notes |
|---|---|---|
| Convergence depth | 2-4 | 2 traders=+2, 3=+3, 5+=+4 |
| Average TCS quality | 0-2 | avg≥80=+2, avg≥50=+1 |
| Risk precision | 0-1 | avg risk score≥75=+1 |
| Weekly ROI of traders | 0-2 | avg≥20%=+2, avg≥10%=+1 |
| SM direction confirms | 0-1 | SM direction matches + ≥5% |
| 15m velocity | -1 to +2 | >0.5=+2, >0.1=+1, <-0.5=-1 |
| 1h acceleration | 0-1 | >1.0=+1 |
| US session | 0-1 | 13-21 UTC |

**Min score: 8.** Leverage: 8-9=7x, 10+=10x.

---

## Runtime Setup

```bash
sed -i 's/${WALLET_ADDRESS}/<WALLET>/' /data/workspace/skills/spider-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/spider-strategy/runtime.yaml
openclaw senpi runtime create --path /data/workspace/skills/spider-strategy/runtime.yaml
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
6. Create scanner cron (90 sec, main)
7. Write `config/bootstrap-complete.json`
8. Send: "🕷️ SPIDER v1.0 online. Scanning for elite convergence. Silence = no convergence."

---

## Risk

| Rule | Value |
|---|---|
| Max positions | 1 |
| Max entries/day | 3 |
| Leverage | 7-10x (conviction-scaled) |
| Cooldown | 120 min between entries |
| Min score | 8 |
| Min convergence | 2 ELITE/RELIABLE traders |
| Margin | 50% |

---

## Files

| File | Purpose |
|---|---|
| `scripts/spider-scanner.py` | Elite convergence scanner (two-phase) |
| `scripts/spider_config.py` | Config helper |
| `config/spider-config.json` | Wallet, strategy ID |
| `runtime.yaml` | Runtime YAML for DSL plugin |

---

## License

MIT — Built by Senpi (https://senpi.ai).


---

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.
