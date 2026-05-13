---
name: phoenix-strategy
description: >-
  PHOENIX v3.0 — Contribution Velocity Scanner. Same battle-tested signal.
  v3.0 adopts the Lemon DSL profile (removes weak_peak_cut, 480m timeout,
  wider Phase 2 tiers) and resets starting budget to current equity
  ($637.93) so the pnl-aware daily cap unblocks from the prior circuit
  breaker lock.
license: MIT
metadata:
  author: jason-goldberg
  version: "3.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🔥 PHOENIX v3.0 — Contribution Velocity Scanner

SM profit velocity diverging from price. The signal works. The DSL profile is finally letting it breathe.

## v3.0 changelog (fleet-fix batch 4)

Phoenix self-diagnosed 54% of losers killed by `weak_peak_cut` while
locked behind a -36.3% drawdown circuit breaker. Adopting Lemon's DSL
profile + resetting the budget baseline:

- `weak_peak_cut` removed entirely (was cutting winners too early)
- `hard_timeout` 45m → 480m (winners need time to run)
- `dead_weight_cut` → 20m
- Phase 1 loosened: `max_loss_pct` 25 → 15, `retrace_threshold` 8,
  `consecutive_breaches_required` 1 (Senpi runtime is single-breach only)
- Phase 2 tiers widened to Lemon's ladder
  (5/20, 10/40, 15/60, 20/75, 30/85, 50/92)
- `STARTING_BUDGET` 1000.0 → 637.93 (current equity) — unblocks the
  pnl-aware daily cap from the circuit breaker

## ⛔ CRITICAL AGENT RULES
### RULE 1: Install path is `/data/workspace/skills/phoenix-strategy/`
### RULE 2: THE SCANNER DOES NOT EXIT POSITIONS — DSL only.
### RULE 3: MAX 3 POSITIONS
### RULE 4: MAX 4 ENTRIES PER DAY — the counter increments in the scanner. Do NOT bypass.
### RULE 5: Never modify parameters.

## Runtime Setup
```bash
sed -i 's/${WALLET_ADDRESS}/<WALLET>/' /data/workspace/skills/phoenix-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/phoenix-strategy/runtime.yaml
openclaw senpi runtime create --path /data/workspace/skills/phoenix-strategy/runtime.yaml
openclaw senpi runtime list && openclaw senpi status
```

## Bootstrap Gate
On EVERY session start, verify runtime + status + scanner cron (2 min, main).
VERIFY trade counter: `cat /data/workspace/skills/phoenix-strategy/state/trade-counter.json`
If the date is not today, delete it and let the scanner recreate it.
Send: "🔥 PHOENIX v3.0 online. Lemon DSL profile. Counter: X/4."

## License
MIT — Built by Senpi (https://senpi.ai).


---

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.
