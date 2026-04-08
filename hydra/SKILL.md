---
name: hydra-strategy
description: >-
  HYDRA v2.0 — Squeeze Detector. Finds crowded trades about to unwind.
  Funding extreme + SM positioned against the crowd + price starting to move.
  Goes opposite to the funding crowd. Only liquid assets ($20M+ volume).
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

# 🐉 HYDRA v2.0 — Squeeze Detector

Find the crowd. Go against them. Ride the cascade.

---

## ⛔ CRITICAL AGENT RULES

### RULE 1: Install path is `/data/workspace/skills/hydra-strategy/`

### RULE 2: THE SCANNER DOES NOT EXIT POSITIONS

DSL is the ONLY exit mechanism. v1.0 had missing wallet fields causing
positions to run 3+ days unprotected. v2.0 uses the plugin runtime.

### RULE 3: MAX 2 POSITIONS at a time

### RULE 4: Verify runtime is installed on every session start

### RULE 5: Never modify parameters.

### RULE 6: MAX 4 ENTRIES PER DAY

### RULE 7: 120-minute per-asset cooldown

### RULE 8: NEVER trade illiquid assets (BLUR, STABLE, DOOD, DEGEN, etc.)

---

## v1.0 Post-Mortem

- 30 trades, 20% win rate, -$161 loss
- Only 2 of 6 signal sources ever fired (FDD + LCD at minimum 55 conviction)
- Traded illiquid garbage (BLUR, STABLE, DOOD) with wide spreads
- 100% LONG bias — missed short squeezes entirely
- FDD threshold bug (0.0001 instead of 0.001) let weak signals through
- Missing wallet fields left positions unprotected for 3+ days

## What v2.0 Fixes

| v1.0 | v2.0 |
|---|---|
| 6 sources, only 2 fired | 2 clean sources: funding + SM divergence |
| Min conviction 55 | Min score 7 with multi-source confirmation |
| Any asset | $20M+ daily volume only |
| 100% LONG | Direction-agnostic (goes against funding crowd) |
| FDD threshold bug | Clean funding threshold |
| Missing wallet fields | Plugin runtime handles everything |

---

## The Squeeze Thesis

1. **Funding extreme** → crowd is piled one direction and paying for it
2. **SM against the crowd** → smart money disagrees with the crowd
3. **Price starting to move** → the squeeze is beginning
4. **Hydra enters opposite to the crowd** → rides the liquidation cascade

Positive funding = crowd is LONG → Hydra goes SHORT.
Negative funding = crowd is SHORT → Hydra goes LONG.

---

## Scoring (10 points max)

| Signal | Points | Description |
|---|---|---|
| Funding extremity | 1-3 | How crowded is the trade? |
| SM against crowd | 1-3 | How committed is smart money against the crowd? |
| Price moving against crowd | 0-2 | Is the squeeze starting? |
| 1H momentum confirms | 0-1 | Short-term follow-through |
| Volume | 0-1 | High volume = more liquidations |

**Min score: 7.** Requires strong funding + SM divergence + at least one confirming signal.

---

## Runtime Setup

```bash
sed -i 's/${WALLET_ADDRESS}/<WALLET>/' /data/workspace/skills/hydra-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/hydra-strategy/runtime.yaml
openclaw senpi runtime create --path /data/workspace/skills/hydra-strategy/runtime.yaml
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
8. Send: "🐉 HYDRA v2.0 online. Hunting squeezes. Silence = no crowding."

---

## Risk

| Rule | Value |
|---|---|
| Max positions | 2 |
| Max entries/day | 4 |
| Leverage | 7x |
| Min daily volume | $20M |
| Cooldown | 120 min |
| Banned assets | BLUR, STABLE, DOOD, DEGEN |

---

## Files

| File | Purpose |
|---|---|
| `scripts/hydra-scanner.py` | Squeeze detector — funding crowd vs SM divergence |
| `scripts/hydra_config.py` | Config helper |
| `config/hydra-config.json` | Wallet, strategy ID |
| `runtime.yaml` | Runtime YAML for DSL plugin |

---

## License

MIT — Built by Senpi (https://senpi.ai).
