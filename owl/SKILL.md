---
name: owl-strategy
description: >-
  OWL v7.0 — Pure contrarian crowding-unwind hunter (v2-runtime-native rewrite).
  Wait for the crowd to overcommit. Wait for them to exhaust. Then eat their
  liquidations. Producer pushes signals to the runtime; runtime LLM gates them,
  executes via FEE_OPTIMIZED_LIMIT (maker-first), and manages DSL exits
  autonomously. Conviction-scaled leverage (7/8/10 by score) per the fleet-
  winning Polar v2.4 / Bald Eagle v3.0 pattern. Entry direction is OPPOSITE
  of crowd direction — Owl is the only Senpi agent that fades crowding.
license: Apache-2.0
metadata:
  author: jason-goldberg
  version: "7.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🦉 OWL v7.0 — Pure Contrarian. v2-Runtime-Native. Maker Exits.

Wait for the crowd to overcommit. Wait for them to exhaust. Then eat their liquidations.

**One thesis: the crowd is wrong.** Every other skill in the fleet enters WITH momentum, WITH the trend, WITH smart money. OWL is the only skill that enters AGAINST the crowd. The edge: crowded trades unwind violently and predictably.

---

## What changed in v7.0

**Architecture (not thesis):**

| Layer | v6.x | v7.0 |
|---|---|---|
| Trading loop | Self-executing scanner calls `create_position` directly | Producer pushes signals via `external-scanner ingest`; runtime owns execution |
| Entry gate | Scanner decides + executes | LLM pass-through gate (producer already filtered) |
| Entry order | FEE_OPTIMIZED_LIMIT (already maker-first) | Same — `ensure_execution_as_taker: false` preserved |
| Exit order | DSL + MARKET (taker) | DSL + **FEE_OPTIMIZED_LIMIT** (maker-first, 60s, taker fallback as safety floor) |
| Risk gates | Scanner-side dynamicSlots + drawdown circuit breaker | Declarative `runtime.risk.guard_rails` + producer dynamic cap (defense in depth) |
| State | `state/state.json` shared | `state/<wallet-hash>/` isolated (multi-wallet safe) |
| Wallet env var | `OWL_WALLET` | `OWL_WALLET` (preserved — already correct) |

**Why v7.0:** v6.x already had wide DSL and good entry filters. The v7 win is on EXITS — v6 used MARKET orders, paying ~3 bp HL taker on every close. v7's `FEE_OPTIMIZED_LIMIT` with 60s maker window + taker fallback recovers ~50% of HL exit fees. Plus declarative risk gates, wallet isolation, fail-loud guards, and the cleaner v2 architecture.

**Thesis preserved verbatim from v6.2:**
- Crowding score: funding extremity (0-4) + SM tilt (0-3, +1 if confirms funding) + OI concentration (0-2). Floor 6.
- Persistence: 1+ hour above floor, with 2-tick tolerance for noise (v5.3)
- Exhaustion score: volume declining + price stalling + volume spike no follow-through + RSI divergence. ≥ 2 distinct signals, score ≥ 5.
- Combined score ≥ 12 to fire
- 6h post-loss per-asset cooldown
- Universe: ALL crypto perps with OI > $3M (v6.1 expansion, no top-30 truncation)
- XYZ banned (different unwind dynamics)

---

## Files

| File | Purpose |
|---|---|
| `runtime.yaml` | v2 runtime spec (scanners, actions, exit DSL, guard_rails) |
| `scripts/owl-producer.py` | Cron-driven producer — emits contrarian signals to runtime |
| `scripts/owl_config.py` | Shared MCP helper + atomic state I/O |
| `config/owl-config.json` | Operator-tunable defaults (informational; producer constants WIN) |

---

## Producer behavior

Runs every 15 minutes via cron (preserved cadence from v6). On each tick:

1. **Reentrancy guard:** acquires `state/<wallet-hash>/producer.lock`. fcntl LOCK_EX | LOCK_NB.
2. **Read account value** via `strategy_get_clearinghouse_state` for sizing + dynamic cap.
3. **Apply dynamic daily cap** (defense-in-depth alongside runtime guard_rails).
4. **Fetch universe**: `market_list_instruments` (~80-100 crypto perps with OI > $3M).
5. **Fetch SM positioning map** (one MCP call shared across all assets — v7.0 optimization vs v6.x per-asset call).
6. **Score crowding per asset** + update persistence history.
7. **Filter to persisted candidates** (≥ 1h above crowding floor).
8. **Detect exhaustion** for persisted candidates only (saves MCP calls).
9. **Filter combined score ≥ 12** + per-asset cooldown.
10. **Emit top contrarian candidate** with conviction-scaled leverage (7/8/10 by score).
11. **Persist crowding history + cooldown state** under `state/<wallet-hash>/`.

NO execution code. NO position-tracking. NO DSL state. The runtime owns all of that.

**Direction is OPPOSITE of crowd direction.** This is the entire edge — Owl is the only fleet agent that fades crowding.

---

## Entry flow

```
Producer cron (15 min)
  ↓ Crowding score >= 6, persisted >= 1h
  ↓ Exhaustion score >= 5 with >= 2 signals
  ↓ Combined score >= 12
  ↓ external-scanner ingest --scanner owl_signals
Runtime
  ↓ Schema-validates fields against runtime.yaml
  ↓ LLM gate (decision_model = ${OWL_DECISION_MODEL})
  ↓ Pass-through unless malformed; rejects if direction == crowdDirection
  ↓ OPEN_POSITION via FEE_OPTIMIZED_LIMIT (maker-only, 60s, NO taker fallback)
DSL (runtime-managed, exits via maker-first)
  ↓ Phase 1 max_loss 35% / 3-breach (wide for contrarian retrace)
  ↓ Phase 2 6-tier ladder starting at 5% trigger (Lemon-pattern first leg)
  ↓ hard_timeout 480m, weak_peak 120m@2%, dead_weight 30m
  ↓ Exit via FEE_OPTIMIZED_LIMIT (maker-first, 60s, taker fallback)
```

---

## Required env vars

The runtime YAML uses these substitutions:

| Var | Purpose |
|---|---|
| `${WALLET_ADDRESS}` | Strategy wallet address |
| `${TELEGRAM_CHAT_ID}` | Telegram chat ID for notifications |
| `${OWL_DECISION_MODEL}` | Bare model name for LLM gate. NO provider prefix. |

The producer reads:

| Var | Purpose | Default |
|---|---|---|
| `OWL_WALLET` | Wallet (must match runtime YAML's wallet). **Agent-specific by design — do NOT use generic `STRATEGY_ADDRESS`.** Per Turbine v2.0.9 contamination fix. | — (required; producer fails loud) |
| `OPENCLAW_BIN` | CLI binary name | `openclaw` |
| `EXTERNAL_SCANNER_NAME` | Scanner ID | `owl_signals` |
| `OWL_MARGIN_PCT` | Fraction of account value per slot | `0.25` |
| `OWL_MIN_OI_USD` | Override OI floor | `3000000` |

---

## Producer install (on OpenClaw host)

Source path: `owl/`. Install destination: `/data/workspace/skills/owl-strategy/`.

```bash
# 1. Pull the skill files (source: senpi-skills/main/owl/)
mkdir -p /data/workspace/skills/owl-strategy/{scripts,config}

for f in runtime.yaml scripts/owl-producer.py scripts/owl_config.py config/owl-config.json; do
  curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/owl/$f \
    -o /data/workspace/skills/owl-strategy/$f
done

# Remove the v6.x scanner if it's still there from a prior install
rm -f /data/workspace/skills/owl-strategy/scripts/owl-scanner.py

# 2. Install the runtime
WALLET_ADDRESS=0x... \
TELEGRAM_CHAT_ID=... \
OWL_DECISION_MODEL=gemini-3.1-pro-preview \
  openclaw senpi runtime create --path /data/workspace/skills/owl-strategy/runtime.yaml

# 3. Schedule the producer (15 min cadence — preserve v6 cadence)
# OWL_WALLET (NOT generic STRATEGY_ADDRESS) is required by the producer.
# RECOMMENDED: cron-as-Claude-turn with NO_REPLY filter (silence-is-correct pattern).
openclaw cron add \
  --name "owl-v7-producer" \
  --cron "*/15 * * * *" \
  --session isolated \
  --wake now \
  --message $'export OWL_WALLET=0x... && python3 /data/workspace/skills/owl-strategy/scripts/owl-producer.py\n\nCRITICAL INSTRUCTION: If the output shows "status": "ok" AND ("signals_pushed": 0 OR "signals_pushed" is absent), you MUST reply EXACTLY with NO_REPLY to remain silent. Only report on errors, crashes, or signals_pushed >= 1.' \
  --no-deliver

# 4. Verify
openclaw senpi runtime list                          # expect: owl-tracker v1.7.0
openclaw senpi status --runtime owl-tracker
openclaw cron list --json                            # find the cron id
openclaw cron runs --id <cron-id> --limit 5

# Inspect producer state:
ls -la /data/workspace/skills/owl-strategy/state/<wallet-hash>/
# Expect: producer.lock + crowding-history.json + (after first emit) asset-cooldowns.json,
# trade-counter.json with mtime in last few minutes.
```

---

## Risk envelope (declarative, runtime-enforced)

| Setting | Value |
|---|---|
| Slots | 2 |
| Margin per slot | 25% of account value (default ~$250 on $1k) |
| Default leverage | 8x (conviction tiers: 7x at 12-13, 8x at 14-15, 10x at 16+) |
| Daily loss halt | 10% |
| Drawdown halt | 25% (also producer-side circuit breaker) |
| Max entries per day | 4 (runtime ceiling); producer dynamic cap by PnL |
| Max consecutive losses | 4 |
| Per-asset cooldown | 360 min (6h post-loss) |
| Asset universe | All crypto perps with OI > $3M (XYZ banned) |

**Dynamic daily cap (producer-side, v6 carryover):**

| Account state | Max entries / day |
|---|---|
| PnL < -25% | 0 (HARD STOP — runtime also halts) |
| PnL < -15% | 1 |
| PnL < -5% | 2 |
| Day realized PnL < +$150 | 2 (base) |
| Day realized PnL ≥ +$150 | 3 |
| Day realized PnL ≥ +$400 | 4 |

---

## Expected behavior

| Metric | Expected |
|---|---|
| Crowded assets per scan | 5-15 above floor |
| Persisted ≥ 1h | 1-3 typically |
| Exhaustion confluence | 0-1 per scan |
| Trades per day | 0-2 |
| Avg hold | 1-8h |
| Win rate target | 60-70% (contrarian unwinds, when timed right, are reliable) |

**Silence is correct.** Owl waits for both the crowd state AND the exhaustion trigger. Most days, one or both is missing. Don't improvise.

---

## What was removed in v7.0

Removed (responsibilities moved to runtime):
- `owl-scanner.py` (replaced by `owl-producer.py`)
- `create_position` calls (runtime owns execution)
- `cancel_order` for stale resting orders (runtime cancels on FEE_OPTIMIZED_LIMIT timeout)
- Drawdown circuit breaker in scanner code (runtime guard_rails owns it)
- Trade counter / record_trade_result (runtime tracks lifecycle)

Permanently disabled (decided in v6.x):
- Top-30 OI truncation (v6.1 universe expansion)
- 4-hour persistence requirement (v6.0 lowered to 1h)
- 14 minScore (v6.0 lowered to 12)

---

## License

Apache-2.0 — Built by Senpi (https://senpi.ai). Attribution required for derivative works.
Source: https://github.com/Senpi-ai/senpi-skills

---

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.
