---
name: owl-strategy
description: >-
  OWL v8.0 — Pure contrarian crowding-unwind hunter (senpi_runtime_helpers).
  Wait for the crowd to overcommit. Wait for them to exhaust. Then eat their
  liquidations. Producer pushes signals to the runtime via direct HTTP POST;
  runtime LLM gates them (pass-through), executes via FEE_OPTIMIZED_LIMIT
  (maker-first), and manages DSL exits autonomously. Conviction-scaled
  leverage (7/8/10 by score) per the fleet Polar v2.4 / Bald Eagle v3.0
  pattern. Entry direction is OPPOSITE of crowd direction — Owl is the
  only Senpi agent that fades crowding.
license: Apache-2.0
metadata:
  author: jason-goldberg
  version: "8.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🦉 OWL v8.0 — Pure Contrarian. Helpers-Native. Maker Exits.

Wait for the crowd to overcommit. Wait for them to exhaust. Then eat their liquidations.

**One thesis: the crowd is wrong.** Every other skill in the fleet enters WITH momentum, WITH the trend, WITH smart money. OWL is the only skill that enters AGAINST the crowd. The edge: crowded trades unwind violently and predictably.

---

## What changed in v8.0.0

**Plumbing-only migration. NO thesis change.** v7.1's crowding/exhaustion scoring, persistence gates, MACRO_TREND_GATE, conviction leverage, DSL preset are all preserved verbatim.

Six-layer plumbing flip on @senpi-ai/runtime 1.1.0 (same pattern as Wolverine/Lemon/Cheetah v3.0+ migrations):

| Layer | v7.1 | v8.0 |
|---|---|---|
| MCP calls | `mcporter` subprocess | `SenpiClient.mcp_call()` direct HTTPS |
| Signal emit | `openclaw senpi external-scanner ingest` subprocess | `SenpiClient.push_signal()` direct HTTP POST |
| Reentrancy | hand-rolled `fcntl` flock on `producer.lock` | `producer_daemon.scanner_lock` (stale-PID auto-recovery) |
| Tick scheduling | openclaw cron + agentTurn | long-lived `producer_daemon` process |
| /state alive_check | none | `wallet=`/`scanner=` kwargs → daemon self-terminates if runtime/scanner removed |
| Signal type tagging | inferred from payload | `signal_type="OWL_CONTRARIAN_FADE"` passed explicitly |

**Why v8.0:** keeps Owl on the same plumbing as the rest of the fleet. Eliminates per-tick subprocess overhead (~30-60s → ~1-3s ticks). No thesis change — v7.1 was the most recent thesis-level work.

---

## Thesis (preserved from v7.1 / v6.2)

- **Crowding score:** funding extremity (0-4) + SM tilt (0-3, +1 if confirms funding) + OI concentration (0-2). Floor 6.
- **Persistence:** 1+ hour above floor, with 2-tick tolerance for noise (v5.3)
- **Exhaustion score:** volume declining + price stalling + volume spike no follow-through + RSI divergence. ≥ 2 distinct signals, score ≥ 5.
- **Combined score ≥ 12** to fire.
- **Macro trend gate:** block crypto fades when `|BTC 4h move|` > 3% (v7.1).
- **6h post-loss per-asset cooldown.**
- **Universe:** ALL crypto perps with OI > $3M (v6.1 expansion, no top-30 truncation).
- **XYZ banned** (different unwind dynamics; needs separate calibration).

---

## Files

| File | Purpose |
|---|---|
| `runtime.yaml` | Runtime spec (scanners, actions, exit DSL, guard_rails) — schema version 1.8.0 |
| `scripts/owl-producer.py` | Long-lived helpers-native producer daemon (15-min cadence) |
| `scripts/owl_config.py` | Shared MCP helper + `_wrapper_client` proxy + atomic state I/O |
| `config/owl-config.json` | Operator-tunable defaults (informational; producer constants WIN) |

---

## Producer behavior

Runs every 15 minutes (long-lived daemon, NOT openclaw cron). On each tick:

1. **Resolve wallet** (`OWL_WALLET` env var, falls back to `config.wallet`); fail loud if both empty.
2. **Read account value** via `strategy_get_clearinghouse_state` for sizing + dynamic cap.
3. **Apply dynamic daily cap** (defense-in-depth alongside runtime guard_rails).
4. **Fetch universe**: `market_list_instruments` (~80-100 crypto perps with OI > $3M).
5. **Fetch SM positioning map** (one MCP call shared across all assets).
6. **MACRO_TREND_GATE:** if `|BTC 4h move|` > 3%, skip tick.
7. **Score crowding per asset** + update persistence history.
8. **Filter to persisted candidates** (≥ 1h above crowding floor).
9. **Detect exhaustion** for persisted candidates only (saves MCP calls).
10. **Filter combined score ≥ 12** + per-asset cooldown.
11. **Emit top contrarian candidate** via `cfg._wrapper_client.push_signal(..., signal_type="OWL_CONTRARIAN_FADE", ...)` with conviction-scaled leverage (7/8/10 by score).
12. **Persist crowding history + cooldown state** under `state/<wallet-hash>/`.

NO execution code. NO position-tracking. NO DSL state. The runtime owns all of that.

**Direction is OPPOSITE of crowd direction.** This is the entire edge — Owl is the only fleet agent that fades crowding.

---

## Entry flow

```
Producer daemon tick (every 900s)
  ↓ Crowding score >= 6, persisted >= 1h
  ↓ Exhaustion score >= 5 with >= 2 signals
  ↓ Combined score >= 12
  ↓ SenpiClient.push_signal(scanner="owl_signals", signal_type="OWL_CONTRARIAN_FADE", ...)
Runtime (127.0.0.1:8787)
  ↓ Schema-validates fields against runtime.yaml
  ↓ LLM gate (decision_model = ${OWL_DECISION_MODEL})
  ↓ Pass-through unless malformed; rejects if direction == crowdDirection
  ↓ OPEN_POSITION via FEE_OPTIMIZED_LIMIT (maker-only, 60s, NO taker fallback)
DSL (runtime-managed)
  ↓ Phase 1 max_loss 35% / single-breach (wide for contrarian retrace)
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
| `SENPI_AUTH_TOKEN` | Required for MCP calls + signal POST | — (required; producer fails loud) |
| `OWL_WALLET` | Wallet (must match runtime YAML's wallet). Agent-specific by design. Per Turbine v2.0.9 contamination fix. | falls back to `config.wallet` |
| `OWL_MARGIN_PCT` | Fraction of account value per slot | `0.25` |
| `OWL_MIN_OI_USD` | Override OI floor | `3000000` |

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

## v8.0.0 changelog

- **Plumbing-only.** Six-layer flip per Wolverine v5.0 / Lemon v2.0 template. Thesis preserved verbatim from v7.1.
- `owl_config.py`: `mcporter_call()` now routes through `SenpiClient.mcp_call()` (direct HTTPS). Exposes `_wrapper_client` proxy for `push_signal()`.
- `owl-producer.py`: drops `fcntl` lockfile + `subprocess.run([...ingest])`. Uses `producer_daemon(fn=main, interval_seconds=900, ...)` and `cfg._wrapper_client.push_signal(signal_type="OWL_CONTRARIAN_FADE", ...)`.
- `runtime.yaml`: schema version bump 1.7.1 → 1.8.0. Description rewritten. DSL preset, scanner `owl_signals`, action `owl_entry`, `consecutive_breaches_required: 1` unchanged.
- State files unchanged. `crowding-history.json` / `asset-cooldowns.json` / `trade-counter.json` continue to live under `state/<wallet-hash>/`.

---

## License

Apache-2.0 — Built by Senpi (https://senpi.ai). Attribution required for derivative works.
Source: https://github.com/Senpi-ai/senpi-skills

---

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.
