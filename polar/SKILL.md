---
name: polar-strategy
description: >-
  POLAR v2.5.0 — ETH Alpha Hunter (v2-runtime-native + asymmetry-
  optimized DSL). Single-asset ETH focus. Hyperfeed primary direction
  + structural-veto gates from candle data + multi-factor scoring +
  conviction-tiered leverage. v3.0's structural-veto thesis preserved
  with MIN_SCORE relaxed 14 → 12 (v3.0 had 16 trades / 10 days = too
  few). Producer pushes signals via external_scanner; runtime LLM-
  gates them, executes via FEE_OPTIMIZED_LIMIT (maker-first), and
  manages DSL exits autonomously. Asymmetry-optimized DSL: tight
  Phase 1 (15% max_loss = 3% price stop at 5x — cut losers FAST) +
  wide Phase 2 trail (10/20, 18/40, 30/60, 45/75, 75/88 — RIDE
  WINNERS HARD). 32% WR is profitable when asymmetry holds.
license: MIT
metadata:
  author: jason-goldberg
  version: "2.5.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🐻‍❄️ POLAR v2.5.0 — ETH Alpha Hunter (asymmetry-optimized)

One asset. Every signal. Producer scores. Runtime executes. DSL rides asymmetry.

---

## What changed in v2.5.0

| Layer | v3.0.6 | v2.5.0 |
|---|---|---|
| Trading loop | Scanner runs every 3 min and calls `create_position` | Producer pushes signals via `external-scanner ingest`; runtime owns execution |
| MIN_SCORE | 14 (too restrictive — 16 trades / 10 days) | **12** (between v2.4's 10 and v3.0's 14) |
| Entry order | FEE_OPTIMIZED_LIMIT (already maker-first) | Same — preserved |
| Exit order | DSL + MARKET orders | DSL + **FEE_OPTIMIZED_LIMIT** (maker-first, 60s, taker fallback) |
| Phase 1 max_loss | 25% | **15%** — cut losers FAST |
| Phase 1 retrace | 8% | **5%** — quicker retrace exit |
| Phase 1 breaches | 3 | **2** — don't wait 3 ticks |
| Phase 2 first tier | 8/25 | **10/20** — let runners breathe |
| Phase 2 top tier | 50/85 | **75/88** — extreme runners ride to 75% ROE |
| Risk gates | Agent enforces in scanner code | Declarative `runtime.risk.guard_rails` |

**Naming note:** v2.5 (not v3.x). v3.0's structural-veto diagnostic was correct (v2.4's 32% WR + 'fee drag dominated' was the problem). But v3.0 over-corrected — MIN_SCORE 14 + over-strict gates produced too few entries (16 trades / 10 days / -$72). v2.5 keeps v3.0's structural veto but lowers MIN_SCORE to 12 and combines with maker-exit fee recovery + asymmetry-optimized DSL.

**Thesis preserved verbatim from v3.0:**
- Hyperfeed primary direction (SM concentration via `leaderboard_get_markets`)
- 4h trend structure (NEUTRAL block)
- trend_strength_4h ≥ 0.75 (4 of 5 candles)
- **Structural direction MUST match SM direction** (v3.0's anti-chop gate)
- 1h trend matches 4h
- 15m momentum confirms direction
- Base-tech-score floor (strong_15m OR aligned_5m)
- RSI gate (74 cap on long, 26 floor on short)
- 240min per-asset cooldown
- ETH only

---

## The asymmetry math

At 5x leverage with v2.5.0 DSL:

| Outcome | Frequency | Per-trade $ |
|---|---|---|
| Phase 1 stop hit | 0.40 | -$15 (3% price stop on $250 margin × 5x = $1250 notional × 1.2% = $15) + $0.40 fee = **-$15.40** |
| Phase 2 tier 1 lock | 0.20 | +$10 |
| Phase 2 tier 2 lock | 0.20 | +$25 |
| Phase 2 tier 3+ winner | 0.20 | +$50-150 |

**Expectancy at 32% Phase-1 WR:** 0.40 × -$15.40 + 0.60 × $30 (avg winner) = **+$11.84/trade × ~25 trades/month = +$296/month.**

This is the math the v3.0 → v2.5 change unlocks. v3.0's loose Phase 2 (8% trigger first lock) cut profits too early; v3.0's loose Phase 1 (25% max_loss) bled too long. v2.5 fixes both ends.

---

## Files

| File | Purpose |
|---|---|
| `runtime.yaml` | v2 runtime spec |
| `scripts/polar-producer.py` | Cron-driven producer (3 min cadence) |
| `scripts/polar_config.py` | Shared MCP helper + atomic state I/O |
| `config/polar-config.json` | Operator-tunable defaults |

---

## Required env vars

Runtime YAML uses:

| Var | Purpose |
|---|---|
| `${WALLET_ADDRESS}` | Strategy wallet address |
| `${TELEGRAM_CHAT_ID}` | Telegram chat ID |
| `${POLAR_DECISION_MODEL}` | Bare model name (no provider prefix) |

Producer reads:

| Var | Purpose | Default |
|---|---|---|
| `POLAR_WALLET` | Wallet (must match runtime YAML). **Agent-specific by design — do NOT use generic `STRATEGY_ADDRESS`.** Per Turbine v2.0.9 contamination fix. | — (required; producer fails loud) |
| `OPENCLAW_BIN` | CLI binary | `openclaw` |
| `EXTERNAL_SCANNER_NAME` | Scanner ID | `polar_signals` |
| `POLAR_MARGIN_PCT` | Fraction of account value per slot | `0.25` |

---

## Producer install (on OpenClaw host)

Source path in repo: `polar/`. Install destination: `/data/workspace/skills/polar-strategy/`.

```bash
# 1. Pull the skill files
mkdir -p /data/workspace/skills/polar-strategy/{scripts,config}

for f in runtime.yaml scripts/polar-producer.py scripts/polar_config.py config/polar-config.json; do
  curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/polar/$f \
    -o /data/workspace/skills/polar-strategy/$f
done

# Remove the v3 scanner if still present
rm -f /data/workspace/skills/polar-strategy/scripts/polar-scanner.py

# 2. Install the runtime
WALLET_ADDRESS=0x... \
TELEGRAM_CHAT_ID=... \
POLAR_DECISION_MODEL=gemini-3.1-pro-preview \
  openclaw senpi runtime create --path /data/workspace/skills/polar-strategy/runtime.yaml

# 3. Schedule the producer (3 min cadence — preserves v3.0 cadence)
# RECOMMENDED: cron-as-Claude-turn pattern with NO_REPLY filter
openclaw cron add \
  --name "polar-v2-5-producer" \
  --cron "*/3 * * * *" \
  --session isolated \
  --wake now \
  --message $'export POLAR_WALLET=0x... && python3 /data/workspace/skills/polar-strategy/scripts/polar-producer.py\n\nIf output has "status":"ok" AND ("signals_pushed":0 OR "signals_pushed" is absent), reply EXACTLY NO_REPLY. Only report on errors, crashes, or signals_pushed >= 1.' \
  --no-deliver

# 4. Verify
openclaw senpi runtime list                          # expect: polar-tracker v1.5.0
openclaw cron list --json | grep polar-v2-5
openclaw cron runs --id <polar-cron-id> --limit 5
```

---

## Risk envelope (declarative, runtime-enforced)

| Setting | Value |
|---|---|
| Slots | 1 (single-asset) |
| Margin per slot | 25% of account value (~$250) |
| Default leverage | 5x |
| Conviction tiers | 5x (score 12-14), 7x (15-16), 10x (17+) |
| Daily loss halt | 10% |
| Drawdown halt | 25% |
| Max entries per day | 6 |
| Max consecutive losses | 4 |
| Per-asset cooldown | 240 min |
| Asset universe | ETH only |

---

## Expected behavior

| Metric | Expected |
|---|---|
| Trades per day | 0-2 (most days 0-1 — high MIN_SCORE bar with structural veto) |
| Avg hold | 15-180 min |
| Phase 1 hit rate | 30-40% (cut losers fast) |
| Phase 2 reached | 60-70% of trades |
| Win rate | 32-50% target |
| Per-trade expectancy | +$10-15 at 32% WR with v2.5 asymmetry |

**Silence is correct.** The structural-veto gate (4h direction must match SM direction) blocks chop entries — long stretches of ETH chop produce zero trades.

---

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills

---

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.
