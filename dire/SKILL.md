---
name: dire-strategy
description: >-
  DIRE v2.0.0 — BRENTOIL XYZ specialist, helpers-native rewrite (2026-05-12).
  Plumbing-only migration from v1.7.0: producer + v2 runtime
  (Wolverine v5 / Grizzly v6 / Polar / Kodiak template). NO thesis change.
  The scanner becomes a producer that emits signals via
  SenpiClient.push_signal(); the v2 runtime owns execution, DSL exits, and
  risk guard rails. v1.7.0 thesis preserved verbatim: 4TF alignment hard
  gate, SM HARD BLOCK via mark/oracle premium proxy, OI velocity scoring,
  volume spike scoring, price cleanliness check, MIN_SCORE 11, ALL FIVE
  soft confirmations required (FP-003), FP-001 quiet hours (00-04 UTC
  unless apex 12+), conviction-scaled sizing (3x / 5x / 7x / 10x by score),
  DSL preset with v1.7 T0/T1 patch (T0 5%/35% lock, T1 8%/50%). First
  Kodiak-family port to a non-crypto asset.
license: MIT
metadata:
  author: jason-goldberg
  version: "2.0"
  platform: senpi
  exchange: hyperliquid
  dex: xyz
  base_skill: kodiak-v5.0
  requires:
    - senpi-trading-runtime
---

# 🦡 DIRE v2.0.0 — BRENTOIL XYZ Specialist (helpers-native)

One asset. News-driven. Producer emits. Runtime owns execution. DSL exits.

---

## ⛔ CRITICAL AGENT RULES

### RULE 1: Install path is `/data/workspace/skills/dire-strategy/`

### RULE 2: PRODUCER ONLY EMITS SIGNALS — RUNTIME OWNS EXECUTION

v2.0.0 plumbing flip: the producer (`dire-producer.py`) emits
`SenpiClient.push_signal(...)` payloads. The v2 runtime's `dire_entry`
action consumes the signal and opens the position. DSL owns all exits.
No direct `create_position` / `close_position` / `ratchet_stop_*` calls
from the producer.

### RULE 3: MAX 1 POSITION — xyz:BRENTOIL only

No multi-asset rotation. No cross-asset hedges. Dire trades oil, period.

### RULE 4: XYZ asset — `coin` field MUST be `xyz:BRENTOIL`

The `xyz:` prefix is mandatory for Hyperliquid HIP-3 DEX. Producer
emits `asset="xyz:BRENTOIL"`; runtime preserves it through the LLM gate.

### RULE 5: ISOLATED margin only

XYZ DEX does not support CROSS margin. Runtime enforces ISOLATED via
strategy config.

### RULE 6: Producer output is a SIGNAL, not an order

The producer logs a single JSON line per tick. The runtime API (HTTP
POST to `127.0.0.1:8787`) accepts the signal, runs the `dire_entry`
LLM gate, and submits the order. Producer output is for telemetry; the
runtime is the source of truth for what executes.

### RULE 7: Verify the senpi-trading-runtime plugin is installed and the
runtime is registered on every session start

`openclaw senpi runtime list` must show `dire-tracker`. The DSL exit
engine is owned by the plugin runtime via `runtime.yaml`.

### RULE 8: Leverage scales with conviction. Hard-capped at 10x. One position.

Sizing is tiered by score, not fixed. Producer computes leverage AND
margin and emits both in `signal.data`:

| Score | Leverage | Margin % | Notional / account | Tier |
|---|---|---|---|---|
| 9 | 3x | 20% | 0.6x | cautious |
| 10 | 5x | 25% | 1.25x | standard |
| 11 | 7x | 30% | 2.1x | conviction |
| 12+ | 10x | 30% | 3.0x | apex |

10x cap = 50% of Hyperliquid's 20x BRENTOIL max. ISOLATED margin
bounds each trade's risk to its own margin allocation.

### RULE 9: Drawdown circuit breaker owned by `risk.guard_rails`

`runtime.yaml → risk.guard_rails.drawdown_halt_pct: 15`. No Python state.

### RULE 10: Helpers-native execution

Producer calls MCP via `SenpiClient.mcp_call()` (direct HTTPS) and emits
signals via `SenpiClient.push_signal()` (direct HTTP POST to the runtime
API). No mcporter subprocess. No openclaw cron — `producer_daemon` owns
tick scheduling with stale-PID auto-recovery.

### RULE 11 (FP-002): User-conversation Claude sessions MUST NOT trade

**This is a hard rule, not a heuristic.** When a Claude Code session is
responding to a user message (Telegram ping, status check, etc.), it
MUST NOT call any of:

- `create_position`
- `close_position`
- `edit_position`
- `ratchet_stop_add` / `ratchet_stop_edit` / `ratchet_stop_delete`
- `cancel_order`
- `strategy_close_positions` / `strategy_close`

These tools are reserved for the **producer daemon + runtime** (entry
path) and the **DSL ratchet engine** (exit path). A user-conversation
Claude session is read-only.

### RULE 12 (FP-001): Quiet hours for low-liquidity windows

Producer skips emission during 00:00-04:00 UTC by default. xyz:BRENTOIL
liquidity is structurally thin in that window (Asia overnight, EU
pre-open). Apex setups (score >= 12) bypass.

Configurable via `quietHoursStartUtc`, `quietHoursEndUtc`,
`quietHoursApexBypassScore` in `dire-config.json`. Disable by setting
start == end.

### RULE 13 (FP-003): All five soft confirmations required

Even at score ≥ 11, every soft component (Volume + OI velocity + SM
premium + Price cleanliness, in addition to the 4TF / SM hard gates)
must contribute ≥ +1. Pattern completeness, not just summed score.
Set `requireAllConfirmations: false` in `dire-config.json` to disable
(NOT recommended).

---

## Thesis

Oil responds to discrete news events (OPEC decisions, geopolitical
conflict, inventory releases) with sharp directional moves. Typical
clean move: 2-5% over 1-4 hours. Typical reversal after overshoot:
3-8% correction over 30 min to 2 hours.

Dire's edge: catch the clean directional move early (4TF alignment +
SM consensus + volume spike + OI acceleration), ride with DSL tier
protection, exit on DSL-locked profit floor or Phase 1 stop.

What Dire does NOT do:
- Predict news before it happens
- Fade overextended moves (Vulture's thesis on crypto)
- Trade multiple XYZ assets (one asset, one thesis)
- Pyramid into winners (one position max)

## Architecture (v2.0.0)

**Producer + v2 runtime, helpers-native.** Producer scores; runtime executes.

- Producer: `scripts/dire-producer.py`
  - Long-lived daemon (`producer_daemon`, 180s tick interval)
  - Per-tick: pull MCP market data → build thesis → push signal if all gates pass
  - NO execution code; NO Python state files
- Runtime: `runtime.yaml`
  - `dire_signals` external_scanner consumes producer signals
  - `dire_entry` LLM action gates and submits orders
  - `risk.guard_rails` enforces daily caps, drawdown, cooldown
  - `exit.dsl_preset` owns all exits

### Tick cadence

180 seconds (helpers-native daemon). Producer-side `producer_daemon` owns
the lock with stale-PID auto-recovery.

### DSL ladder (v1.7 T0/T1 patch, preserved verbatim)

| Tier | Trigger ROE | Lock HW % |
|---|---|---|
| T0 | +5% | 35% (v1.7) |
| T1 | +8% | 50% (v1.7) |
| T2 | +20% | 70% |
| T3 | +35% | 80% |
| T4 | +50% | 90% |

Phase 1: max_loss 20%, retrace 8, 1 consecutive breach.
Time cuts: ALL DISABLED (single-asset family pattern).

### Gate order (all must pass in the producer)

1. 4TF alignment HARD GATE (5m, 15m, 1h, 4h all same direction)
2. SM HARD BLOCK (mark/oracle premium direction must match)
3. SM conviction scoring (premium tier bonus)
4. OI velocity scoring (flat-path extraction)
5. Volume ratio (15m vs 1h, tiered 2.5x / 5x)
6. Price action cleanliness (no 5m wicks > 1.5% in last 30 min)
7. MIN_SCORE ≥ 11
8. FP-003: all five soft confirmations fire ≥ +1
9. FP-001: quiet hours block sub-apex during 00-04 UTC

Then `risk.guard_rails` adds: per-asset cooldown (120 min), daily cap (2),
drawdown halt (15%), consecutive-loss halt (3), daily loss limit (10%).

Max attainable score: 6 (base) + 2 (SM) + 2 (OI) + 2 (vol) + 1 (clean) = **13**.

## Configuration

- `config/dire-config.json` — wallet, strategyId, MIN_SCORE, quietHours, sizingTiers
- `runtime.yaml` — guard rails, DSL preset, LLM gate prompt
- `scripts/dire_config.py` — SDK shim + MCP wrapper + helpers
- `scripts/dire-producer.py` — main producer loop

## Monitoring

### Per-tick log

`status=ok` every 180s. `signals_pushed=1` when a setup clears all
gates. `note` field documents which gate blocked when 0.

### Week 1 review

- Signals pushed: 3-10 per week (gates are tight)
- Win rate: ≥50% at >2:1 avg_win:avg_loss
- DSL trigger distribution: T0 hits often, T2+ on the runners

## Version history

- **v2.0.0** (2026-05-12) — helpers-native producer migration.
  Drops self-executing scanner; runtime owns execution. v1.7.0
  thesis preserved verbatim. Uses `SenpiClient.push_signal()`,
  `producer_daemon`, `risk.guard_rails`. First non-crypto Kodiak
  family to ship v2.
- v1.7.0 — Fleet-wide DSL ratchet T0/T1 patch. T0 lock 25→35,
  T1 trigger 10→8.
- v1.6.0 — "Hit fewer, win bigger" patch. minScore 9→11.
  FP-003 require-all-confirmations gate. FP-001 quiet hours.
- v1.1 — Conviction-scaled sizing (leverage 3x→10x by score).
- v1.0 — Initial release. First XYZ specialist. Kodiak-family port.
