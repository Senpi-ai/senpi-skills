---
name: condor-strategy
description: >-
  CONDOR v2.0 — Multi-Asset Thesis Picker. Evaluates BTC, ETH, SOL, HYPE
  simultaneously and enters the strongest thesis. Conviction-scaled margin.
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

# 🦅 CONDOR v2.0 — Multi-Asset Thesis Picker

Four assets. One position. Maximum conviction.

---

## ⛔ CRITICAL AGENT RULES

### RULE 1: Install path is `/data/workspace/skills/condor-strategy/`
### RULE 2: THE SCANNER DOES NOT EXIT POSITIONS — DSL only.
### RULE 3: MAX 1 POSITION (best thesis only)
### RULE 4: Verify runtime on every session start
### RULE 5: Never modify parameters
### RULE 6: MAX 3 ENTRIES PER DAY
### RULE 7: 120-minute per-asset cooldown

---

## How It Works

Condor scans BTC, ETH, SOL, and HYPE every 3 minutes. Each asset is scored
independently on SM consensus, 4H trend, 1H confirmation, contribution velocity,
funding alignment, and leaderboard rank. The asset with the highest score wins.

**Key design decisions:**
- 4H trend must agree with SM direction (hard gate)
- 1H is bonus points, not a blocker (v1.0's 1H gate caused zero trades)
- Only 1 position at a time — forces the scanner to pick the BEST thesis
- Conviction-scaled margin: more capital when conviction is higher

## Scoring (12 points max)

| Signal | Points | Gate? |
|---|---|---|
| SM consensus | 1-4 | **Yes (min 3% + 20 traders)** |
| 4H trend aligned | 1-2 | **Yes (hard block if opposing)** |
| 1H confirms | 0-1 | No |
| Contribution velocity | 0-2 | No |
| Funding confirms | 0-2 | No |
| Top 10 rank | 0-1 | No |

**Min score: 8.** Conviction scaling: 8-9 = 25% margin, 10-11 = 35%, 12+ = 45%.

---

## Runtime Setup

```bash
sed -i 's/${WALLET_ADDRESS}/<WALLET>/' /data/workspace/skills/condor-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/condor-strategy/runtime.yaml
openclaw senpi runtime create --path /data/workspace/skills/condor-strategy/runtime.yaml
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
8. Send: "🦅 CONDOR v2.0 online. Scanning BTC/ETH/SOL/HYPE. Silence = no conviction."

---

## Risk

| Rule | Value |
|---|---|
| Max positions | 1 |
| Max entries/day | 3 |
| Leverage | 7x |
| Cooldown | 120 min per asset |
| Min score | 8 |

---

## Files

| File | Purpose |
|---|---|
| `scripts/condor-scanner.py` | Multi-asset thesis evaluator |
| `scripts/condor_config.py` | Config helper |
| `config/condor-config.json` | Wallet, strategy ID |
| `runtime.yaml` | Runtime YAML for DSL plugin |

---

## License

MIT — Built by Senpi (https://senpi.ai).
