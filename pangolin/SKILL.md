---
name: pangolin-strategy
description: >-
  PANGOLIN v3.0.0 — Extreme Funding Rate Fader, senpi_runtime_helpers
  migration. Plumbing-only flip from openclaw-CLI subprocess + mcporter
  subprocess to in-process SenpiClient. Pangolin is the canonical
  reference producer for the senpi_runtime_helpers SDK wrapper pattern.
  Thesis preserved verbatim from v2.2.0:
  funding-fade entries opposite to elevated funding rates (>0.015%/8h
  ≈ 20% annualized), persistence ≥3h, regime confirms or neutral.
  Conservative 3-5x leverage, wide DSL (12h hard_timeout). Collects
  funding every 8h while crowded positions mean-revert (24-48h thesis).
  Phase 2 v2.2 ratchet ladder T0 8/50, T1 16/65, T2 30/85, T3 50/92.
license: MIT
metadata:
  author: jason-goldberg
  version: "3.0.1"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime>=2.0.0
---

# 🦔 PANGOLIN v3.0.0 — Funding Rate Fader. senpi_runtime_helpers.

An entirely new strategy archetype for the Predators fleet. No other agent trades on funding rate signals.

---

## What changed in v2.0

**Architecture (not thesis):**

| Layer | v1.x | v2.0 |
|---|---|---|
| Trading loop | Agent runs scanner, agent calls `create_position` | Producer pushes signals via `SenpiClient.push_signal()`; runtime owns execution |
| Entry gate | Agent decides | LLM pass-through gate (producer already filtered) |
| Entry order | FEE_OPTIMIZED_LIMIT, taker fallback OFF | Same — `ensure_execution_as_taker: false` preserved (v1 patience) |
| Exit order | DSL + MARKET (taker) | DSL + **FEE_OPTIMIZED_LIMIT** (maker-first, 60s, taker fallback as safety floor) |
| Risk gates | Agent enforces in scanner code | Declarative `runtime.risk.guard_rails` |
| Per-asset cooldown | Producer state file | Runtime + producer (defense in depth) |

**Why v2:** v1 entries were already maker-first (Pangolin shipped with FEE_OPTIMIZED_LIMIT in v1), but EXITS used MARKET orders. Per-trade fee saving from maker exits is small ($0.10-0.20 given Pangolin's small notional) but architectural alignment matters. The bigger v2 win is declarative risk + runtime-managed lifecycle.

**Thesis preserved from v1.5/v1.7:**
- Funding rate >= 0.00015 (~20% annualized)
- Persistence >= 3 hours (reject fresh spikes)
- Regime confirms fade OR is neutral (no fighting the crowd)
- OI >= $1M (liquidity floor)
- Score >= 9 with funding extremity + persistence + trend + regime + SM concentration scoring
- Per-asset 240min cooldown
- XYZ banned

---

## Files

| File | Purpose |
|---|---|
| `runtime.yaml` | v2 runtime spec (scanners, actions, exit DSL, guard_rails) |
| `scripts/pangolin-producer.py` | Cron-driven producer — emits funding-fade signals to runtime |
| `scripts/pangolin_config.py` | Shared MCP helper + atomic state I/O |
| `config/pangolin-config.json` | Operator-tunable defaults (informational; producer constants WIN) |

---

## Producer behavior

Runs every 5 minutes via cron. On each tick:

1. **Reentrancy guard:** acquires `state/<wallet-hash>/producer.lock`. If a prior run hasn't released it (cron faster than MCP latency), this run skips cleanly.
2. **Read account value** via `strategy_get_clearinghouse_state` for dynamic-cap math + sizing.
3. **Apply dynamic daily cap** (v1 carryover): 12 entries at +5% PnL → 0 entries at -25% PnL.
4. **Fetch markets:** `market_list_instruments` + `leaderboard_get_markets` + `market_get_funding_regime` (one call each).
5. **Detect funding extremes:** for each candidate, fetch `market_get_funding_history` (one call per candidate) for persistence + trend.
6. **Apply hard gates:** funding >= threshold, persistence >= 3h, regime confirms or neutral, OI >= $1M, score >= 9, asset not in cooldown.
7. **Emit top candidate** via `client.push_signal()` (direct HTTP POST to the runtime API).
8. **Persist cooldown + counter state** under `state/<wallet-hash>/`.

NO execution code. NO position-tracking. NO DSL state. The runtime owns all of that.

---

## Entry flow

```
Producer daemon (5 min tick)
  ↓ funding-fade candidate detected (score >= 9)
  ↓ client.push_signal(scanner="pangolin_signals", ...)
Runtime
  ↓ Schema-validates fields against runtime.yaml
  ↓ LLM gate (decision_model = ${PANGOLIN_DECISION_MODEL})
  ↓ Pass-through unless malformed (producer already filtered)
  ↓ OPEN_POSITION via FEE_OPTIMIZED_LIMIT (maker-only, 60s, NO taker fallback — preserves v1 patience)
DSL (runtime-managed, exits via maker-first)
  ↓ Phase 1 max_loss 30% / 3-breach
  ↓ Phase 2 tiers (12/50, 20/70, 30/85, 50/92)
  ↓ hard_timeout 720m, weak_peak DISABLED, dead_weight 480m (one funding cycle)
  ↓ Exit via FEE_OPTIMIZED_LIMIT (maker-first, 60s, taker fallback as safety)
```

---

## Required env vars

The runtime YAML uses these substitutions:

| Var | Purpose |
|---|---|
| `${WALLET_ADDRESS}` | Strategy wallet address |
| `${TELEGRAM_CHAT_ID}` | Telegram chat ID for notifications |
| `${PANGOLIN_DECISION_MODEL}` | Bare model name for LLM gate (e.g. `gemini-3.1-pro-preview`, `claude-sonnet-4-20250514`). NO provider prefix — OpenClaw double-prefixes and rejects. |

The producer reads:

| Var | Purpose | Default |
|---|---|---|
| `PANGOLIN_WALLET` | Wallet (must match runtime YAML's wallet). **Agent-specific by design — do NOT use generic `STRATEGY_ADDRESS`.** Per Turbine v2.0.9 contamination fix: a shared env var is a fleet-wide vector if multiple agents share an install. | — (required; producer fails loud) |
| `OPENCLAW_BIN` | CLI binary name | `openclaw` |
| `EXTERNAL_SCANNER_NAME` | Scanner ID | `pangolin_signals` |
| `PANGOLIN_MARGIN_PCT` | Fraction of account value per slot | `0.25` |

---

## Producer install (on OpenClaw host)

Source path in the senpi-skills repo: `pangolin/`. Install destination on the OpenClaw host: `/data/workspace/skills/pangolin-strategy/`. The two names differ — repo uses `pangolin`, install dir uses the SKILL.md `name` field (`pangolin-strategy`).

```bash
# 1. Pull the skill files (source: senpi-skills/main/pangolin/)
mkdir -p /data/workspace/skills/pangolin-strategy/scripts
mkdir -p /data/workspace/skills/pangolin-strategy/config

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/pangolin/runtime.yaml \
  -o /data/workspace/skills/pangolin-strategy/runtime.yaml
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/pangolin/scripts/pangolin-producer.py \
  -o /data/workspace/skills/pangolin-strategy/scripts/pangolin-producer.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/pangolin/scripts/pangolin_config.py \
  -o /data/workspace/skills/pangolin-strategy/scripts/pangolin_config.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/pangolin/config/pangolin-config.json \
  -o /data/workspace/skills/pangolin-strategy/config/pangolin-config.json

# Remove the v1 scanner if it's still there from a prior install
rm -f /data/workspace/skills/pangolin-strategy/scripts/pangolin-scanner.py

# 2. Install the runtime
# PANGOLIN_DECISION_MODEL is REQUIRED — pick any model supported by the runtime's
# model registry. Examples: gemini-3.1-pro-preview, claude-sonnet-4-20250514.
# Pass BARE model name only — NO provider prefix (OpenClaw double-prefixes).
WALLET_ADDRESS=0x... \
TELEGRAM_CHAT_ID=... \
PANGOLIN_DECISION_MODEL=gemini-3.1-pro-preview \
  openclaw senpi runtime create --path /data/workspace/skills/pangolin-strategy/runtime.yaml

# 3. Launch the producer daemon (5-min tick — funding doesn't change that fast).
# PANGOLIN_WALLET (NOT generic STRATEGY_ADDRESS) is required by the producer.
# Producer fails loud at startup if not set — misconfig is visible immediately.
SENPI_AUTH_TOKEN=<your-token> \
PANGOLIN_WALLET=0x... \
  nohup python3 -u /data/workspace/skills/pangolin-strategy/scripts/pangolin-producer.py \
  > /tmp/pangolin-producer.log 2>&1 &

# 4. Verify
openclaw senpi runtime list                              # expect: pangolin-tracker running
openclaw senpi status --runtime pangolin-tracker
senpi-helpers list                                       # daemon visible with recent LAST_TICK
senpi-helpers health pangolin-<wallet-suffix>            # exit 0 = healthy
senpi-helpers stats pangolin-<wallet-suffix> --hours 1   # signals posted + error histogram

# Inspect producer state (per-wallet cooldown / counter):
ls -la /data/workspace/skills/pangolin-strategy/state/<wallet-hash>/
# Expect: producer.lock + asset-cooldowns.json + trade-counter.json with
# mtime in last few minutes. A fresh trade-counter.json (after first emit)
# or producer.lock mtime confirms the daemon is ticking.
```

---

## Risk envelope (declarative, runtime-enforced)

| Setting | Value |
|---|---|
| Slots (max concurrent positions) | 2 |
| Margin per slot | 25% of account value (~$250 on $1k starting) |
| Default leverage | 3x (5x at score >= 13) |
| Daily loss halt | 10% |
| Drawdown halt | 25% |
| Max entries per day | 12 (runtime ceiling); producer applies dynamic cap by PnL |
| Max consecutive losses | 5 (loose — funding fade has long loss streaks) |
| Per-asset cooldown | 240 min |
| XYZ equities | Banned at producer scan level |

**Dynamic daily cap (producer-side, v1 carryover):**

| PnL % since deploy | Max entries / day |
|---|---|
| ≥ +5% | 12 |
| 0 to +5% | 8 |
| -5 to 0 | 5 |
| -15 to -5 | 3 |
| -25 to -15 | 1 |
| < -25% | 0 (also triggers runtime drawdown halt) |

---

## Expected behavior

| Metric | Expected |
|---|---|
| Trades/day (calm funding) | 0-1 |
| Trades/day (extreme funding) | 1-3 |
| Days with zero trades | Common when no asset has 3h+ persistent extreme funding |
| Hold duration | 8-48h (one or more funding cycles) |
| Avg fee per close | Lower than v1 (maker fills on exits) |

**Patience IS the edge.** Pangolin will skip days when no funding regime is extreme enough. The thesis requires waiting for the crowd to actually crowd in for hours before fading — most days don't have that setup.

---

## What was removed in v2

Removed (responsibilities moved to runtime):
- `pangolin-scanner.py` (replaced by `pangolin-producer.py`)
- Position counting / has_resting_orders bookkeeping (runtime owns position lifecycle)
- `create_position` calls (runtime owns execution)
- `cancel_order` calls for stale resting orders (runtime cancels on timeout)
- Per-asset cooldown ENFORCEMENT in scanner (still in producer as defense-in-depth, but runtime is authority)

Permanently disabled (decided in v1.6):
- `weak_peak_cut` (funding fade takes 24-48h, time cut pre-empts thesis)

Banned (decided in v1.0):
- XYZ equities (no funding signal applicable)

---

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills

---

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.
