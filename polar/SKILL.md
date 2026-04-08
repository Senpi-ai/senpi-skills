---
name: polar-strategy
description: >-
  POLAR v2.3 — ETH Alpha Hunter. Single-asset ETH lifecycle scanner with
  conviction-scaled leverage, move-exhaustion scoring, and same-direction
  re-entry cooldown. DSL exit managed by plugin runtime via runtime.yaml.
license: MIT
metadata:
  author: jason-goldberg
  version: "2.3"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🐻‍❄️ POLAR v2.3 — ETH Alpha Hunter

Best gross trader in the fleet. Single asset. Maximum conviction.

---

## ⛔ CRITICAL AGENT RULES

### RULE 1: Install path is `/data/workspace/skills/polar-strategy/`
### RULE 2: THE SCANNER DOES NOT EXIT POSITIONS — DSL only.
### RULE 3: MAX 1 POSITION
### RULE 4: Verify runtime on every session start
### RULE 5: Never modify parameters
### RULE 6: MAX 4 ENTRIES PER DAY
### RULE 7: 120-minute cooldown between entries
### RULE 8: 60-minute same-direction cooldown after wins

---

## How It Works

Polar scans ETH every 3 minutes. It scores using SM consensus, multi-timeframe
contribution velocity (15m, 1h, 4h), price alignment, funding, and trader depth.

**v2.3 key behaviors:**
- Move-exhaustion: if ETH 4h price change ≥4% in entry direction, -2 points.
  If ≥2.5%, -1 point. Creates tension with 4H confirmation scoring.
- Same-direction lock: after a winning exit, won't re-enter the same direction
  for 60 minutes. Prevents chasing the same move at worse prices.
- Midnight-safe cooldowns: timestamps persist across UTC date rollover.

## Scoring (max ~18 points)

| Signal | Points | Notes |
|---|---|---|
| SM consensus | 1-3 | ≥15% dominant = +3 |
| Trader depth | 0-1 | ≥100 traders = +1 |
| 4H alignment | -1 to +2 | Confirms direction |
| Move exhaustion | -2 to 0 | **v2.3** penalizes ≥2.5% existing moves |
| 1H momentum | 0-1 | Confirms direction |
| 15m velocity | -1 to +4 | Extreme spike = +4 |
| 1h acceleration | 0-2 | Strong accel = +2 |
| 4H major shift | 0-1 | ≥5% contribution change |
| Acceleration pattern | 0-1 | 15m > 1h = accelerating |
| Funding alignment | 0-1 | Funding pays your direction |
| US session | 0-1 | 13-21 UTC |

**Min score: 8.** Leverage: 8→7x, 9→10x, 10→15x, 11+→20x.

---

## Runtime Setup

```bash
sed -i 's/${WALLET_ADDRESS}/<WALLET>/' /data/workspace/skills/polar-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/polar-strategy/runtime.yaml
openclaw senpi runtime create --path /data/workspace/skills/polar-strategy/runtime.yaml
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
8. Send: "🐻‍❄️ POLAR v2.3 online. Hunting ETH. Silence = no conviction."

---

## Risk

| Rule | Value |
|---|---|
| Max positions | 1 |
| Max entries/day | 4 |
| Leverage | 7-20x (conviction-scaled, capped at 20x for ETH) |
| General cooldown | 120 min |
| Same-direction cooldown | 60 min after wins |
| Min score | 8 |
| Margin | 50% |

---

## Files

| File | Purpose |
|---|---|
| `scripts/polar-scanner.py` | ETH scoring + entry execution |
| `scripts/polar_config.py` | Config helper (MCP, state, positions) |
| `config/polar-config.json` | Wallet, strategy ID |
| `runtime.yaml` | Runtime YAML for DSL plugin |

---

## License

MIT — Built by Senpi (https://senpi.ai).
