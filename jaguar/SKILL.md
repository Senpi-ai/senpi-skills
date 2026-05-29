---
name: jaguar-strategy
description: >-
  JAGUAR v4.0.0 — Striker (rank-jump detector), senpi_runtime_helpers
  migration. Plumbing-only flip from openclaw-CLI subprocess +
  mcporter subprocess + cron-driven self-executing scanner to
  in-process SenpiClient + long-lived producer_daemon + helpers-native
  push_signal. Thesis preserved verbatim from v3.7: violent
  FIRST_JUMP signals, rank-jump ≥ 10, MIN_SCORE 9, conviction-scaled
  leverage (10x apex / 7x conviction), 15m velocity hard gate, $3M
  day-notional liquidity floor, XYZ ban, one-amazing-trade-per-day
  discipline. Risk policy: max 3 entries/day when day is RED,
  unlimited when GREEN (cap losers, ride winners).
license: MIT
metadata:
  author: jason-goldberg
  version: "4.0.1"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🐆 JAGUAR v4.0.1 — Striker (helpers-native)

**Plumbing-only migration from v3.7. NO thesis change.** v3.x striker
scoring + DSL preset + risk.guard_rails preserved verbatim. Producer
flips to in-process `SenpiClient`, daemon replaces cron, runtime owns
execution.

## v4.0.1 changelog (2026-05-29) — order-of-operations bug fix

**Bug:** v4.0 appended `current_scan` to `history["scans"]` BEFORE calling
`detect_striker_signals()`. Inside the detection function, `prev_scans[-1]`
then returned the same scan that was just appended — so every asset's
`rank_jump` computed as `current_rank − current_rank = 0`. No asset ever
met `STRIKER_MIN_RANK_JUMP`. **The scanner went completely silent.**

**Impact:** A historical-replay test on the bug missed 25 valid signals over
2 days, including spikes on VVV, XMR, and GRASS. The agent diagnosed the
bug from its own diagnostics on 2026-05-29 ("score 11 GRASS LONG —
FIRST_JUMP #34→#15" fired within minutes of the hot-patch) and the fix
is now ferried into the repo.

**Fix:** Reordered `main()`:
1. Build `current_scan`
2. Load history
3. If history is empty, seed it and return early (first-ever scan)
4. **Call `detect_striker_signals(current_scan, history)` FIRST** — so
   `prev_scans[-1]` returns the actual previous scan
5. Append `current_scan` to history AFTER detection
6. Save history

The detect-then-append sequence is now the contract; future
`jaguar-producer.py` edits must preserve it.

## v4.0.0 changelog

- `jaguar-producer.py` (NEW) replaces `jaguar-scanner.py` (DELETED).
  Pure producer — no `create_position` calls, no trade counters, no
  cooldown state, no held+pending dedup, no daily-cap state file. The
  runtime's `risk.guard_rails` and `per_asset_cooldown_minutes` own all
  of that.
- MCP calls flip from mcporter subprocess to in-process
  `SenpiClient.mcp_call()` via `jaguar_config.mcporter_call` shim.
- Cron → long-lived daemon via `producer_daemon` (180s tick).
- fcntl reentrancy guard removed — `producer_daemon` owns the per-tick
  `scanner_lock` with stale-PID auto-recovery.
- `runtime.yaml` now declares the `jaguar_signals` external_scanner,
  an LLM-pass-through `jaguar_entry` action with FEE_OPTIMIZED_LIMIT
  entries, and the v3.7 DSL preset + risk.guard_rails verbatim.
- Phase 1 `consecutive_breaches_required` set to 1 (v2 DSL single-breach).
- Trade chain DB now emits LIFECYCLE_RUNTIME_STARTED →
  DECISION_EXECUTED → ACTION_RESULT → DSL_CREATED → DSL_CLOSED for
  every trade. Per-trade telemetry restored.

## What's preserved from v3.7 verbatim

- v3.2 rank-jump threshold floor (`STRIKER_MIN_RANK_JUMP = 10`)
- v3.3 prev-rank floor (`STRIKER_MIN_PREV_RANK = 20`)
- v3.3 reason-count floor (`STRIKER_MIN_REASONS = 3`)
- v3.4 silent-None fix: $3M day-notional absolute liquidity floor
  (replaces unreachable vol_ratio hard gate)
- v3.0 Striker-only thesis (no Stalker, no Hunter, no Pyramiding)
- Conviction-scaled leverage (score≥10 → 10x apex, score≥9 → 7x conviction)
- 15m contribution velocity hard gate (`contrib_15m > 0`)
- 4h direction-alignment gate
- XYZ ban
- v3.7 risk.guard_rails: max_entries_per_day=3, bypass_on_profit=true,
  daily_loss_limit_pct=10, drawdown_halt_pct=25,
  max_consecutive_losses=3, cooldown_minutes=60,
  per_asset_cooldown_minutes=120
- DSL preset: hard_timeout=45min, weak_peak_cut=25min@3%,
  dead_weight_cut=12min, phase1 max_loss=15%, phase2 ladder
  (7/40, 12/55, 15/75, 20/85)
- Per-asset HL leverage clamp via `strategy_get_asset_trading_limits`
  (XMR-class small caps with 5x venue ceilings)

---

## ⛔ Hard Rules

### RULE 1: Install path is `/data/workspace/skills/jaguar-strategy/`

### RULE 2: The producer does NOT exit positions

DSL ratchet engine owns all exits via `runtime.yaml`'s `exit:` block.

### RULE 3: MAX 2 concurrent positions

Enforced via `strategy.slots: 2` in runtime.yaml.

### RULE 4: User-conversation Claude sessions MUST NOT trade

Hard rule. When responding to a user message (Telegram ping, status
check, etc.), the Claude Code session MUST NOT call any of:

- `create_position`
- `close_position`
- `edit_position`
- `ratchet_stop_add` / `ratchet_stop_edit` / `ratchet_stop_delete`
- `cancel_order`
- `strategy_close` / `strategy_close_positions`

These tools are reserved for the **producer daemon** and the **DSL
ratchet engine**. The daemon is the only entry path. The DSL is the
only exit path. User sessions are read-only.

### RULE 5: Verify runtime is installed on every session start

Run `openclaw senpi runtime list`. `jaguar-tracker` must be listed
ACTIVE.

### RULE 6: 120-minute per-asset cooldown (runtime-enforced)

`per_asset_cooldown_minutes: 120` in risk.guard_rails. Producer no
longer tracks this in Python.

---

## Thesis

Violent rank-jump explosions only. When an asset rockets from
rank 20+ into the top 10 with a ≥10 rank jump AND its 15m
contribution velocity is actively building AND 4h price is aligned
with the SM direction, that's a Striker. Rare but high-conviction.

Striker = "one amazing trade per day." The producer is the filter;
the LLM gate and risk.guard_rails are sanity checks, not
second-guessing layers.

## Entry Scoring (preserved verbatim from v3.7)

### Hard gates (all must pass)
- Current rank > 10 (we only want assets climbing into the top 10)
- 4h price ALIGNED with SM direction
- Previous-scan match (must have been on the leaderboard prior tick)
- Rank jump ≥ 10
- Previous rank ≥ 20 (deep-from)
- 15m contribution velocity > 0 (actively building)
- Day-notional volume ≥ $3M (liquidity floor)
- XYZ DEX banned
- Reason count ≥ 3

### Scoring contributors (max ~14 pts)

| Signal | Points |
|---|---:|
| FIRST_JUMP (was below top 50 OR prev_rank ≥ 30) | +3 |
| IMMEDIATE_MOVER (rank_jump ≥ 10 from rank ≥ 20) | +2 |
| HIGH_VELOCITY (contrib velocity > 10) | +2 |
| DEEP_CLIMBER (prev_rank ≥ 40) | +1 |
| STRONG_4H (price chg 4h > 3%) | +1 |
| DEEP_SM (≥30 traders) | +1 |
| 15M_STRONG_SPIKE (contrib_15m > 2.0) | +3 |
| 15M_SPIKE (contrib_15m > 0.5) | +2 |
| 15M_BUILDING (contrib_15m > 0.1) | +1 |
| 1H_ACCEL (contrib_1h > 1.0) | +1 |
| ACCEL_PATTERN (15m > 1h > 0) | +1 |

**MIN_SCORE: 9** — entry floor.

---

## Conviction-Scaled Leverage

| Score | Leverage |
|---|---:|
| ≥10 (apex) | 10x |
| ≥9 (conviction) | 7x |

Producer pre-clamps via `strategy_get_asset_trading_limits` so the
LLM gate's leverage echo is venue-safe (XMR=5x, etc.).

---

## Exit (DSL — preserved verbatim from v3.7)

| Mechanism | Value |
|---|---|
| hard_timeout | 45 min |
| weak_peak_cut | 25 min @ 3% min |
| dead_weight_cut | 12 min |
| Phase 1 max_loss_pct | 15.0 |
| Phase 1 retrace_threshold | 8 |
| Phase 1 consecutive_breaches_required | 1 |
| Phase 2 T0 | 7% trigger → 40% lock |
| Phase 2 T1 | 12% trigger → 55% lock |
| Phase 2 T2 | 15% trigger → 75% lock |
| Phase 2 T3 | 20% trigger → 85% lock |

---

## Risk Management (runtime.guard_rails — preserved verbatim from v3.7)

| Rule | Value |
|---|---|
| Max positions | 2 concurrent |
| Max entries/day (when RED) | 3 |
| Max entries/day (when GREEN) | unlimited (bypass on profit) |
| Per-asset cooldown | 120 min |
| Daily loss cap | 10% |
| Drawdown halt | 25% |
| Max consecutive losses | 3 → 60 min cooldown |
| Asset blocklist | all XYZ |

**Policy:** cap losers, ride winners. When today's PnL > 0 →
`max_entries_per_day` bypass → unlimited entries. When today's PnL ≤ 0
→ enforce 3/day → top 3 by score, then stop.

---

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills

---

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.
