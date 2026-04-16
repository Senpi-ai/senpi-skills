---
name: scorpion-strategy
description: >-
  SCORPION v3.0 — Multi-Market Active Trader. Trades BOTH crypto AND XYZ
  DEX commodities/indices using SM concentration + 4H trend alignment.
  Up to 3 concurrent positions, short holds, Arena winner playbook.
license: MIT
metadata:
  author: jason-goldberg
  version: "3.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# SCORPION v3.0 — Multi-Market Active Trader

The only predator that hunts across both crypto and commodities.

## The Thesis

Arena winners #2 and #3 share a playbook: trade across BOTH the main Hyperliquid
DEX (crypto) AND the XYZ DEX (commodities, indices), follow SM direction with 4H
price trend confirmation, hold for hours not days, and run multiple positions
simultaneously.

No other Senpi predator trades both markets. Scorpion v3.0 fills that gap.

### Universe

| Market | Assets |
|---|---|
| Crypto majors | BTC, ETH, SOL, HYPE |
| Crypto mid-caps | ZEC, LIT, GRASS, FARTCOIN, TAO, ONDO, SUI, ARB, WLD, DOGE, AVAX |
| XYZ commodities | CL (crude oil), BRENTOIL, GOLD |
| XYZ indices | SPX |

### What Makes This Different

| Feature | Most Predators | Scorpion v3.0 |
|---|---|---|
| Markets | Crypto only | Crypto + XYZ DEX |
| Max positions | 1 | 3 concurrent |
| Hold time | Hours to days | Hours (12h max) |
| Daily entries | 2-4 | Up to 6 |
| Direction | Fixed or contrarian | Signal-driven both ways |

## CRITICAL AGENT RULES
### RULE 1: Install path is `/data/workspace/skills/scorpion-strategy/`
### RULE 2: THE SCANNER DOES NOT EXIT POSITIONS — DSL only.
### RULE 3: MAX 3 POSITIONS at a time.
### RULE 4: MAX 6 ENTRIES PER DAY. 120-minute per-asset cooldown.
### RULE 5: XYZ assets use "xyz:" prefix for create_position (e.g. coin="xyz:CL").
### RULE 6: XYZ assets require leverageType="ISOLATED".
### RULE 7: USE FEE_OPTIMIZED_LIMIT for all entries with ensureExecutionAsTaker=true.

## Scanner Logic (1 API call)

```
1. leaderboard_get_markets (limit=100)

For each asset in the universe (crypto + XYZ):
  - Check SM direction and concentration
  - Check 4H price alignment with SM direction
    (crypto: >= 1.0%, XYZ: >= 0.5%)
  - Score: SM concentration + 4H trend + 15m velocity +
           1H acceleration + trader depth + 4H conviction
  - MIN_SCORE: 6

Pick up to (3 - current_positions) best candidates.
Enter with 30% margin, 5-10x leverage (score-scaled).
```

## DSL Configuration (Short-Hold Profile)

**Phase 1 (Loss Protection):**
- Max loss: -15%
- Retrace from high water: 8%
- 3 consecutive breaches required

**Phase 2 (Profit Locking):**
- +5% -> lock 20%
- +10% -> lock 40%
- +15% -> lock 60%
- +20% -> lock 75%
- +30% -> lock 85%
- +50% -> lock 90%

**Timeouts:**
- Hard timeout: 720 minutes (12 hours)
- Weak peak cut: 60 minutes at +3% minimum
- Dead weight cut: 30 minutes

## Runtime Setup
```bash
sed -i 's/${WALLET_ADDRESS}/<WALLET>/' /data/workspace/skills/scorpion-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/scorpion-strategy/runtime.yaml
openclaw senpi runtime create --path /data/workspace/skills/scorpion-strategy/runtime.yaml
openclaw senpi runtime list && openclaw senpi status
```

## Bootstrap Gate
Verify runtime + status + scanner cron (90s, main).
CRITICAL: The cron must be configured to call `create_position` via Senpi MCP
when the scanner outputs a signal with an entry block.
Send: "SCORPION v3.0 online. Multi-market active trader. Hunting crypto + XYZ."

## License
MIT — Built by Senpi (https://senpi.ai).


---

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.
