---
name: dog-strategy
description: >-
  DOG v1.0 — The Loyal Consistent Performer. Multi-asset SM consensus scanner
  targeting 5% ROE/week through small steady wins. Quick profit-taking DSL.
  The most loyal pup in the fleet.
license: MIT
metadata:
  author: jason-goldberg
  version: "1.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🐕 DOG v1.0 — The Loyal Consistent Performer

5% ROE per week. Steady. Reliable. Good boy.

---

## ⛔ CRITICAL AGENT RULES

### RULE 1: Install path is `/data/workspace/skills/dog-strategy/`
### RULE 2: THE SCANNER DOES NOT EXIT POSITIONS — DSL only.
### RULE 3: MAX 1 POSITION AT A TIME
### RULE 4: MAX 3 ENTRIES PER DAY — never reset this counter
### RULE 5: 180-minute cooldown between entries — never bypass
### RULE 6: MIN_SCORE 10 — never lower this threshold
### RULE 7: 10x leverage max — never increase
### RULE 8: 30% margin — never increase
### RULE 9: Never modify DSL runtime tiers — they are tuned for quick profit-taking
### RULE 10: Verify runtime on every session start

---

## How It Works

Dog scans BTC, ETH, SOL, and HYPE every 3 minutes. Enters ONLY when:
- SM consensus is overwhelming (>15% for +3 pts, >10% for +2)
- 15m velocity is fresh and spiking
- The 4h price move is SMALL (< 2% in entry direction = early move bonus)
- The 4h move is NOT exhausted (> 2% = -2 penalty, > 3% = -3 penalty)
- Combined score reaches 10+

Dog takes profit QUICKLY via tight Phase 2 tiers:
- Tier 1 at 3% ROE / 50% lock = banks 1.5% ROE floor on first profit signal
- Tier 2 at 5% ROE / 65% lock = banks 3.25% ROE floor
- Tier 3 at 8% ROE / 75% lock = banks 6% ROE floor (weekly target in one trade!)

Cuts losers FAST:
- Dead weight cut at 45 minutes (if trade goes nowhere, exit)
- Weak peak cut at 60 minutes (if peak was <1% and stalling, exit)
- Hard timeout at 120 minutes (absolute max hold time)
- Phase 1 max loss at -15% ROE (tight stop)

---

## Scoring (max ~14 points)

| Signal | Points | Notes |
|---|---|---|
| SM consensus | 1-3 | ≥15% dominant = +3 |
| Trader depth | 0-1 | ≥100 traders = +1 |
| 4H alignment | -1 to +2 | Confirms direction |
| Move exhaustion | -3 to +1 | **Strictest in fleet**: >3% = -3, >2% = -2, <0.5% = +1 (early bonus) |
| 1H momentum | 0-1 | Confirms direction |
| 15m velocity | -1 to +3 | Strong spike = +3 |
| 1h acceleration | 0-1 | Accel = +1 |
| Funding alignment | 0-1 | Funding pays your direction |
| US session | 0-1 | 13-21 UTC |

**Min score: 10.** Leverage: flat 10x. No conviction scaling.

---

## Runtime Setup

```bash
sed -i 's/${WALLET_ADDRESS}/<WALLET>/' /data/workspace/skills/dog-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/dog-strategy/runtime.yaml
openclaw senpi runtime create --path /data/workspace/skills/dog-strategy/runtime.yaml
```

---

## Dog's Personality

Dog doesn't bark at every noise. Dog sits patiently, sniffs the market, and
only moves when the scent is unmistakable. When Dog catches a trade, it banks
the profit quickly and comes home wagging its tail. Dog never chases cars
(exhausted moves), never fights bigger dogs (high leverage), and never stays
out past curfew (120-min timeout).

Dog's job isn't to be the flashiest agent in the fleet. Dog's job is to be
the one you can count on every single week.

Good boy.

---

## Files

| File | Purpose |
|---|---|
| `scripts/dog-scanner.py` | Multi-asset scoring + entry execution |
| `scripts/dog_config.py` | Config helper (MCP, state, positions) |
| `config/dog-config.json` | Wallet, strategy ID |
| `runtime.yaml` | Quick-profit DSL config |

---

## License

MIT — Built by Senpi (https://senpi.ai). Good boy.
