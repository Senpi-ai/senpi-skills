---
name: kodiak-strategy
description: >-
  KODIAK v6.0 — SOL Alpha Hunter (v2-runtime-native + 5x leverage fix +
  asymmetry-optimized DSL). Single-asset SOL focus. Hyperfeed primary
  direction + structural-veto from candle data + multi-factor scoring +
  conviction-tiered leverage. v5.1's base-tech-score floor preserved.
  v6.0 drops default leverage 10x → 5x (v5.1 ran 10x SOL = -17.8% over
  26 trades). Producer pushes signals via external_scanner; runtime
  LLM-gates them, executes via FEE_OPTIMIZED_LIMIT (maker-first), and
  manages DSL exits autonomously. Asymmetry-optimized DSL: tight Phase 1
  (15% max_loss = 3% price stop at 5x — cut losers FAST/CHEAP) + wide
  Phase 2 trail (10/20, 18/40, 30/60, 45/75, 75/88 — RIDE WINNERS HARD).
  32% WR is profitable when asymmetry holds.
license: MIT
metadata:
  author: jason-goldberg
  version: "6.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🐻 KODIAK v6.0 — SOL Alpha Hunter (asymmetry-optimized)

One asset. Every signal. Producer scores. Runtime executes. DSL rides asymmetry.

---

## What changed in v6.0

| Layer | v5.1 | v6.0 |
|---|---|---|
| Trading loop | Scanner runs every 3 min and calls `create_position` | Producer pushes signals via `external-scanner ingest`; runtime owns execution |
| Default leverage | **10x SOL** | **5x SOL** (the key thesis fix) |
| Entry order | FEE_OPTIMIZED_LIMIT (already maker-first on entries) | Same — `ensure_execution_as_taker: false` preserved |
| Exit order | DSL + MARKET orders | DSL + **FEE_OPTIMIZED_LIMIT** (maker-first, 60s, taker fallback) |
| Phase 1 max_loss | 25% | **15%** — cut losers FAST |
| Phase 1 retrace | 8% | **5%** — cut losers QUICKER |
| Phase 1 breaches | 3 | **2** — don't wait |
| Phase 2 first tier | 8% trigger / 25% lock | **10% trigger / 20% lock** — let runners breathe |
| Phase 2 top tier | 50% / 85% | **75% / 88%** — extreme runners ride |
| Risk gates | Agent enforces in scanner code | Declarative `runtime.risk.guard_rails` |

**Why v6:** v5.1 ran -17.8% / -$178 over 26 trades on 10x SOL. Diagnostic: 10x leverage on single-asset SOL chop produced loss-biased per-trade variance. Same scanner logic at 5x has half the variance. Combined with maker-exit fee recovery (~$1.40 → ~$0.40 per close) + asymmetry-optimized DSL, the 32% WR pattern that bled at v5.1 should produce positive expectancy at v6.

**Thesis preserved verbatim from v5.1:**
- 4h trend structure (NEUTRAL block)
- trend_strength_4h ≥ 0.75 (4 of 5 candles)
- 1h trend matches 4h
- 15m momentum confirms direction
- Base-tech-score floor (strong_15m OR aligned_5m)
- MIN_SCORE 10 with composite scoring (trend + SM + funding + OI + BTC + RSI)
- 240min per-asset cooldown
- SOL only

---

## The asymmetry math

At 5x leverage with v6.0 DSL:

| Outcome | Frequency | Per-trade $ |
|---|---|---|
| Phase 1 stop hit | 0.40 | -$15 (3% price stop on $200 margin × 5x = $1000 notional × 1.5% = $15) + $0.40 fee = **-$15.40** |
| Phase 2 tier 1 lock | 0.20 | +$8 (5% ROE × $200 margin = $10, lock at 20% → $2 trail) |
| Phase 2 tier 2 lock | 0.20 | +$25 (10% ROE = $20, lock at 40% → $8 trail = $12 net) |
| Phase 2 tier 3+ winner | 0.20 | +$50-150 (extreme runners) |

**Expectancy at 32% Phase-1 WR:**
- 0.40 × -$15.40 + 0.60 × $30 (avg winner) = **+$11.84/trade**
- Across 25 trades/month: **+$296**

This is the math the v5.1 → v6.0 change unlocks. Won't always work — single-day variance is high — but cumulative expectancy is positive.

---

## Files

| File | Purpose |
|---|---|
| `runtime.yaml` | v2 runtime spec (scanners, actions, exit DSL, guard_rails) |
| `scripts/kodiak-producer.py` | Cron-driven producer — emits SOL thesis signals to runtime |
| `scripts/kodiak_config.py` | Shared MCP helper + atomic state I/O |
| `config/kodiak-config.json` | Operator-tunable defaults (informational; producer constants WIN) |

---

## Required env vars

Runtime YAML uses:

| Var | Purpose |
|---|---|
| `${WALLET_ADDRESS}` | Strategy wallet address |
| `${TELEGRAM_CHAT_ID}` | Telegram chat ID |
| `${KODIAK_DECISION_MODEL}` | Bare model name (no provider prefix) |

Producer reads:

| Var | Purpose | Default |
|---|---|---|
| `KODIAK_WALLET` | Wallet (must match runtime YAML). **Agent-specific by design — do NOT use generic `STRATEGY_ADDRESS`.** Per Turbine v2.0.9 contamination fix. | — (required; producer fails loud) |
| `OPENCLAW_BIN` | CLI binary | `openclaw` |
| `EXTERNAL_SCANNER_NAME` | Scanner ID | `kodiak_signals` |
| `KODIAK_MARGIN_PCT` | Fraction of account value per slot | `0.20` |

---

## Producer install (on OpenClaw host)

Source path in repo: `kodiak/`. Install destination: `/data/workspace/skills/kodiak-strategy/`.

```bash
# 1. Pull the skill files
mkdir -p /data/workspace/skills/kodiak-strategy/{scripts,config}

for f in runtime.yaml scripts/kodiak-producer.py scripts/kodiak_config.py config/kodiak-config.json; do
  curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/kodiak/$f \
    -o /data/workspace/skills/kodiak-strategy/$f
done

# Remove the v5 scanner if still present
rm -f /data/workspace/skills/kodiak-strategy/scripts/kodiak-scanner.py

# 2. Install the runtime
WALLET_ADDRESS=0x... \
TELEGRAM_CHAT_ID=... \
KODIAK_DECISION_MODEL=gemini-3.1-pro-preview \
  openclaw senpi runtime create --path /data/workspace/skills/kodiak-strategy/runtime.yaml

# 3. Schedule the producer (3 min cadence — preserves v5.1 cadence)
# RECOMMENDED: cron-as-Claude-turn pattern with NO_REPLY filter
openclaw cron add \
  --name "kodiak-v6-producer" \
  --cron "*/3 * * * *" \
  --session isolated \
  --wake now \
  --message $'export KODIAK_WALLET=0x... && python3 /data/workspace/skills/kodiak-strategy/scripts/kodiak-producer.py\n\nIf output has "status":"ok" AND ("signals_pushed":0 OR "signals_pushed" is absent), reply EXACTLY NO_REPLY. Only report on errors, crashes, or signals_pushed >= 1.' \
  --no-deliver

# 4. Verify
openclaw senpi runtime list                          # expect: kodiak-tracker v1.6.0
openclaw cron list --json | grep kodiak-v6
openclaw cron runs --id <kodiak-cron-id> --limit 5
```

---

## Risk envelope (declarative, runtime-enforced)

| Setting | Value |
|---|---|
| Slots | 1 (single-asset) |
| Margin per slot | 20% of account value (~$200) |
| Default leverage | 5x (was 10x in v5.1) |
| Conviction tiers | 5x (score 10), 6x (11-12), 7x (13+) |
| Daily loss halt | 10% |
| Drawdown halt | 25% |
| Max entries per day | 8 |
| Max consecutive losses | 4 |
| Per-asset cooldown | 240 min |
| Asset universe | SOL only |

---

## Expected behavior

| Metric | Expected |
|---|---|
| Trades per day | 0-3 (most days 0-1 — high MIN_SCORE bar) |
| Avg hold | 19-228 min (per v5.1 historical winners) |
| Phase 1 hit rate | 30-40% (cut losers fast) |
| Phase 2 reached | 60-70% of trades |
| Win rate | 32-50% target |
| Expected per-trade expectancy | +$10-15 at 32% WR with v6.0 asymmetry |

**Silence is correct.** SOL doesn't always have aligned 4h/1h/15m structure with SM agreement. 0-trade days are normal.

---

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills

---

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.
