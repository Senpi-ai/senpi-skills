---
name: dire-strategy
description: >-
  DIRE v1.0 — BRENTOIL XYZ specialist. Catches news-driven momentum breakouts
  on oil with tight DSL protection against sharp geopolitical reversals.
  First Kodiak-family port to a non-crypto asset class. 3x leverage cap,
  ISOLATED margin (required for XYZ), Wolverine execution pattern.
  DSL exit managed by plugin runtime via runtime.yaml.
license: MIT
metadata:
  author: jason-goldberg
  version: "1.0"
  platform: senpi
  exchange: hyperliquid
  dex: xyz
  base_skill: kodiak-v5.0
  requires:
    - senpi-trading-runtime
---

# 🐺 DIRE v1.0 — BRENTOIL XYZ Specialist

One asset. News-driven. Scanner enters. DSL exits.

---

## ⛔ CRITICAL AGENT RULES

### RULE 1: Install path is `/data/workspace/skills/dire-strategy/`

### RULE 2: THE SCANNER DOES NOT EXIT POSITIONS

When the scanner sees an active BRENTOIL position, it outputs NO_REPLY. DSL is the
ONLY exit mechanism. Kodiak-family lesson: scanner-driven thesis exits fight DSL
tier locks and chop positions before they can mature. DSL owns all exits.

### RULE 3: MAX 1 POSITION — xyz:BRENTOIL only

No multi-asset rotation. No cross-asset hedges. Dire trades oil, period.

### RULE 4: XYZ asset — `coin` field MUST be `xyz:BRENTOIL`

The `xyz:` prefix is mandatory for Hyperliquid HIP-3 DEX. Passing plain `BRENTOIL`
will fail at the exchange. The scanner auto-applies the prefix; verify.

### RULE 5: ISOLATED margin only

XYZ DEX does not support CROSS margin. Scanner forces `leverageType: "ISOLATED"`.
Do not attempt to change this.

### RULE 6: Scanner output is AUTHORITATIVE

The JSON emitted to stdout is the source of truth for what happened on a scan.
Runtime consumes it; logs trust it.

### RULE 7: Verify runtime is installed on every session start

Run `openclaw senpi runtime list`. Runtime must be listed. The position tracker
and DSL exit engine are handled by the plugin runtime via `runtime.yaml`.

### RULE 8: Max leverage 3x — hardcoded. Max margin 30%. One position. Period.

Oil vol is ~1.5x crypto on typical days, higher during geopolitical events.
3x is plenty for directional conviction. 5x+ risks liquidation on normal spikes.

### RULE 9: Drawdown circuit breaker at 15% from 7-day rolling peak

Scanner auto-pauses HUNTING if account_value / peak_7d < 0.85. Auto-resumes when
account recovers to >90% of peak, OR operator sets `dd_manual_unlock: true` in
state/trade-counter.json.

### RULE 10: Wolverine execution pattern

Scanner calls MCP via mcporter CLI subprocess directly. No LLM parse loop for
entry execution. Required for news-latency-sensitive oil entries. DSL attach
happens inline before scanner returns — if DSL attachment fails, position is
immediately closed to prevent unprotected exposure.

---

## Thesis

Oil responds to discrete news events (OPEC decisions, geopolitical conflict,
inventory releases) with sharp directional moves. Typical clean move: 2-5%
over 1-4 hours. Typical reversal after overshoot: 3-8% correction over 30
minutes to 2 hours.

Dire's edge: catch the clean directional move early (4TF alignment + SM consensus
+ volume spike + OI acceleration), ride with DSL tier protection, exit on SM
flip or DSL-locked profit floor.

What Dire does NOT do:
- Predict news before it happens
- Fade overextended moves (that's Vulture's thesis on crypto)
- Trade multiple XYZ assets (one asset, one thesis)
- Pyramid into winners (one position max)

## Architecture

Single-asset specialist, 3-mode state machine (HUNTING/RIDING/STALKING), 4TF
alignment hard gate, SM HARD BLOCK, OI velocity scoring, Wolverine execution
pattern, tiered DSL. Ported from Kodiak v5.0 with XYZ-specific adaptations.

### State machine

| Mode | Trigger | Behavior |
|---|---|---|
| HUNTING | No open position | Scan every 60s; fire `create_position` via Wolverine pattern when all gates pass |
| RIDING | Open position, ROE < Tier 0 (+5%) | Let position run; DSL engine not yet active |
| STALKING | Open position, ROE ≥ Tier 0 | DSL locked via ratchet_stop_add; scanner watches for SM flip exit |

### Scan cadence

60 seconds (cron `* * * * *`). Faster than crypto's 3-min cadence because oil
news breaks faster than crypto on-chain signals.

### DSL ladder (5 tiers, aggressive early lock)

| Tier | Trigger ROE | Lock ROE (% of HW) |
|---|---|---|
| T0 | +5% | 25% |
| T1 | +10% | 50% |
| T2 | +20% | 70% |
| T3 | +35% | 80% |
| T4 | +50% | 90% |

Aggressive early lock at T0 (+5% → 25% HW) protects against geopolitical V-reversals
like the Apr 17 BRENTOIL wick ($108 → $89 → $97 in 4 hours).

### Sizing

| Parameter | Value |
|---|---|
| max_leverage | 3x |
| max_margin_pct | 30% of account value |
| max_notional | 1x account value (margin × leverage) |
| leverageType | ISOLATED (required for XYZ) |
| positions max | 1 |

### Gate order (all must pass)

1. Drawdown circuit breaker (account ≥ 85% of 7-day peak)
2. Daily entry cap (max 2 per 24h)
3. 4TF alignment HARD GATE (5m, 15m, 1h, 4h all same direction)
4. SM HARD BLOCK (SM direction must match)
5. OI velocity scoring: `oi_velocity.oi_change_pct_1h > +5 → +2`, `> +2 → +1`, `< -3 → -1`, null passes
6. Volume ratio (news-spike proxy): 15m vol / 1h avg > 2.5 → +1
7. Price action cleanliness: no 5m wicks > 1.5% against direction in last 30 min → +1
8. MIN_SCORE ≥ 9 (tunable)

### Execution

Wolverine pattern: Python scanner invokes `mcporter` CLI directly for
`create_position` and `ratchet_stop_add`. Bypasses LLM parse loop. Validates
inner-order JSON in-process.

Entry order config:
- `orderType: "FEE_OPTIMIZED_LIMIT"`
- `feeOptimizedLimitOptions: { ensureExecutionAsTaker: true, executionTimeoutSeconds: 10 }`
- No slippagePercent (incompatible with FOL)

DSL attach is mandatory and inline — if `ratchet_stop_add` fails after
`create_position` succeeds, scanner immediately closes the position via
`close_position` (orderType MARKET, 2% slippage) to prevent unprotected
exposure.

### Exits (priority order)

1. **DSL tiered ladder** (primary, 90%+ of exits)
2. **SM flow flip** (secondary) — market close immediately
3. **4TF break** — tertiary, if both 1h and 4h flip against position
4. No hard TP — DSL replaces TP entirely

## Configuration

- `config/dire-config.json` — wallet, strategy ID, MIN_SCORE, starting budget
- `runtime.yaml` — DSL tier ladder (must match scanner's ladder), position tracker
- `scripts/dire_config.py` — constants, MCP helpers, state I/O
- `scripts/dire-scanner.py` — main scanner loop

## State files

Written to `state/` inside the skill directory:
- `trade-counter.json` — daily entry count, drawdown gate state, last results
- `state.json` — mode, last scan ts, 7-day rolling peak

## Monitoring

### 72-hour checkpoint

**Green:** ≥1 entry attempted, DSL attached cleanly, no ghost trades, equity ≥ 95%

**Yellow:** Scanner firing every 60s but no valid setups (gates too tight OR current market not producing oil momentum)

**Red:** Ghost trade, DSL attach failure, account drops >15% (circuit breaker should have triggered)

### Week 1 review

- Trade count: 3-10 entries expected (2/day cap × 7 days × ~40% gate hit rate)
- Win rate: ≥50% at >2:1 avg_win:avg_loss ratio
- Fees as % of gross PnL: <25%

## Version history

- v1.0 — Initial release. First XYZ specialist. Kodiak-family port.
