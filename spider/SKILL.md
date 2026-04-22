---
name: spider-strategy
description: >-
  SPIDER v2.0 — Agentic portfolio operator. Complete rewrite from v1.0
  (single-position elite-convergence scanner). The first Senpi strategy
  that decides at portfolio composition level: one anchor (high-conviction
  long, max 3x, min 7-day hold) plus 3-5 funding-driven shorts, risk-parity
  sized to 40% of anchor notional. Long horizon, low turnover, fee-aware,
  forced-exit-tracking. 7-day paper-trading warmup before first strike.
  Staggered pilot entry. Reads Arena leaders as primary positive signal,
  Predators fleet as concentration-risk overlay. Direct response to the
  fleet-learning hypothesis: bleeders over-trade through positive gross
  edge; winners take fewer trades through stricter gates.
license: MIT
metadata:
  author: jason-goldberg
  version: "2.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🕷️ SPIDER v2.0 — Agentic Portfolio Operator

The first Senpi agent that operates **above the position level**.

Every other Predator decides "open or close this trade?" every few minutes. Spider builds a web — one anchor point, several radial threads — then waits. The web does the work. Vibrations come to it.

## What changed in v2.0

v1.0 was an Elite-convergence single-position scanner. It contributed to the same fleet pathology that v2.0 is designed to correct: too many fills, weak signals, repeated DSL stops. v2.0 is a structural rewrite from a different thesis entirely.

## The shape of the strategy

Spider runs a **two-leg portfolio**:

1. **Anchor** — a single high-conviction long (e.g. HYPE 3x), held minimum 7 days
2. **Basket** — 3–5 shorts on the highest-funding alt-coins, risk-parity sized to 40% of anchor notional

The basket isn't a directional hedge — it's a **funding harvest**. Shorts on coins paying the most positive funding to longs collect rate while providing partial downside cushion to the anchor.

Net portfolio: long-biased, partially hedged, funding-positive. Designed to throw off carry whether the anchor moves up modestly, sideways, or even slightly down.

## Why this shape sidesteps three fleet diseases

| Disease | Why Spider is immune |
|---|---|
| Fee disease | ~10–20 fills/week vs. hundreds. Fees become a rounding error. |
| Chop disease | 7-day horizons don't care about 4h chop. |
| Trailing-stop mismatch | No per-leg trailing stop — basket *is* the position. |

What Spider captures that no other Predator does: **funding harvest**. Over 7-day holds, funding rates dwarf intraday price noise on most alts.

## v2.0 fleet-learning constraints

The Apr 2026 fleet hypothesis was concrete:
> "Bleeders are over-trading — relying on weak signals and hitting DSL repeatedly. The way to win big is high conviction + longer hold times. Most bleeders have positive gross edge eaten by fees."

Cheetah: $1,317 gross, $1,600 fees, $283 net loss. Roach: $1,720 gross, ~$2,000 fees, $280 net loss. Same pattern across 6+ agents. Spider v2.0 bakes the fix into the runtime:

### 1. Trade frequency ceiling
**Max 12 fills per rolling 7d window.** Soft warning at 8. Beyond 8 the agent must justify every additional fill in its rationale. Beyond 12 is technically not blocked but every fill demands written justification — behavioral constraint.

### 2. Basket member strictness gate
Basket members must individually score **≥ 6.5** to qualify. If fewer than 3 candidates clear the floor, Spider runs **anchor-only mode** — no basket that week. Better to hold a pure anchor than pad with weak-signal shorts.

### 3. Fee health monitoring
Every weekly `ATTRIBUTION` record carries `gross_pnl`, `fees_paid`, `funding_harvested`, and `fee_pct_of_gross` as the **primary health metric**. If rolling 4-week `fee_pct_of_gross` exceeds **25%**, a `FEE_CEILING_WARNING` event fires and the agent must address it in next week's rationale.

### 4. Forced-exit tracking
Every leg close writes an `EXIT` event with a typed `exit_reason`:

| Category | Reasons |
|---|---|
| **Agent-controlled** (good) | `agent_chosen`, `thesis_break`, `funding_flip`, `weekly_review`, `min_hold_expiry` |
| **Forced** (bad) | `safety_stop`, `circuit_breaker` |

If more than 1 `safety_stop` fires in any rolling 30d window, a `SAFETY_STOP_WARNING` event fires — the agent has lost control of position sizing or is ignoring score collapses until the hard stop bails it out (the bleeder signature).

## The daily decision loop

Each cycle (13:00 UTC, skipping Thursday morning UTC because Arena resets) Spider:

1. Reads four scanner outputs:
   - `anchor_candidates` — top 5 from top-15-volume universe, scored on Arena leader exposure (40%) + SM consensus delta (30%) + funding favorability (15%) + 30d relative strength (15%)
   - `basket_candidates` — top 10 from top-50-volume universe, scored on funding descending (40%) + SM rotation negative (25%) + relative weakness vs anchor (20%) + squeeze risk inverse (15%)
   - `portfolio_state` — current legs, days held, P&L, funding accrued, **fees_paid_per_leg**, **fills_in_rolling_7d**
   - `fleet_overlap` — what other Predators are positioned in (concentration awareness, never a hard veto)

2. Outputs one of five decisions with a reported `fill_budget_remaining_7d`:

| Decision | When |
|---|---|
| `HOLD` | Portfolio still scoring well, no member's thesis broken |
| `SWAP_BASKET_MEMBER` | One basket member's score collapsed (funding flipped, squeeze risk, SM rotation) |
| `RESIZE` | Drawdown trigger or thesis strengthening |
| `FULL_REBALANCE` | Weekly review or regime shift detected |
| `GO_TO_CASH` | No high-conviction anchor available, or circuit-breaker fired |

3. Writes a `DECISION` record to `state/spider-log.jsonl` with full rationale + counterfactuals (alternatives considered + why rejected).

`GO_TO_CASH` is a **first-class output**. Spider is allowed — and expected — to refuse to trade when conviction is insufficient.

## Cold start — the most important section

Spider does not trade for the first 7 days of its life.

### Phase A — Warmup (days 1–7)
Spider boots in `GO_TO_CASH`. Runs the full decision loop daily. **Produces zero trades.** Every day it logs to `spider-log.jsonl`: *"I would have picked anchor=X, basket=[A,B,C,D], conviction=7.2, because..."*

The operator reviews these records before any capital moves. The agent earns trust through observable judgment, not through marketing.

### Phase B — First strike
Strike condition (all three required):
1. Warmup complete (7 days observed)
2. Top anchor candidate scores **≥ 7.0 for 2 consecutive daily scans** (persistence — never a single-day spike)
3. No catastrophic regime signal (BTC down >10% in 48h, funding regime broadly flipping, vol-regime expansion spike)

When strike fires, Spider enters in three staggered steps:

| Day | Anchor size | Basket size | Condition |
|---|---|---|---|
| 1 | 50% | 0% | Strike fires |
| 2 | 75% | 25% | Thesis confirming (anchor up or flat, score still ≥ 7.0) |
| 3 | 100% | 40% | Thesis confirming |

If at any pilot day the anchor drawdown exceeds 5% or the score collapses below 6.5 → **abort**: close everything, return to `GO_TO_CASH`, reset warmup to 3 days, retry.

The first trade Spider ever makes has the worst signal-to-noise ratio of any decision it will ever make. Pilot sizing is the agent encoding epistemic humility into the strategy.

### Phase C — Steady state
Day 4 onward. Normal daily decision loop with full positions and real P&L feedback.

## Signal source philosophy

Three populations, three uses:

| Population | Use |
|---|---|
| **Arena leaders** (7d ROE, top 10) | Primary positive signal for anchor selection |
| **Smart Money** (broader HL leaderboard) | Foundation signal for both anchor and basket |
| **Predators fleet** (incl. Spider itself, self-excluded) | Concentration awareness — leverage modifier only, never a hard veto |

The Predators read is a **portfolio-risk discipline**, not a quality judgment. Same check a real fund runs: *"before I add HYPE long, what's the firm's existing HYPE exposure across all books?"* Concentration only modifies sizing — it never blocks a trade.

## The rationale log

Every scanner output, decision, pilot ramp, exit, abort, weekly attribution, and fleet-learning warning writes a typed JSONL record to `state/spider-log.jsonl`. The log:

- Survives session clears (lives on disk, not in LLM context)
- Is append-only (no record is ever mutated; attributions reference prior decisions by id)
- Captures counterfactuals on every decision (alternatives + score gap + why rejected)
- Calibrates the conviction threshold during warmup (top score percentiles)
- Surfaces fee, fill, and forced-exit pathologies via dedicated WARNING events
- Powers weekly retrospective scoring (`ATTRIBUTION` event with gross/fees/funding/net + benchmark comparison)

See `references/rationale-log-schema.md` for the full event taxonomy.

## What's not yet built (and that's the point)

Spider v2.0 is the agent layer. Two runtime additions are needed for it to operate at full quality:

1. **`dsl_portfolio` exit engine** — portfolio-level exits with `gross_exposure_cap`, `net_delta_target`, `weekly_drawdown_circuit`. Until this ships, Spider enforces these in the LLM action layer (fragile but functional).
2. **Data-layer primitives** — funding regime context, vol regime classifier, fleet overlap aggregator, trade journal API. Spider is the first agent designed *for* these abstractions; without them it has to compute equivalents on the fly.

Spider will get strictly better as both ship. No code change required.

## Install

```bash
mkdir -p /data/workspace/skills/spider-strategy/{config,scripts,state}

gh repo clone Senpi-ai/senpi-skills /tmp/senpi-skills
cp -r /tmp/senpi-skills/spider/* /data/workspace/skills/spider-strategy/

cp /data/workspace/skills/spider-strategy/config/spider-config.example.json \
   /data/workspace/skills/spider-strategy/config/spider-config.json

openclaw runtime start spider-tracker
```

## Operator checklist for the first 7 days

- **Day 1:** confirm `spider-log.jsonl` is being written (one `SCAN` + one `DECISION` per cycle)
- **Day 3:** read the rationale text on each `DECISION` record. Does the agent's reasoning match what you'd want?
- **Day 5:** check `warmup_status()` — how many qualifying strike windows has the agent observed?
- **Day 7:** review `WARMUP_END` summary. Are the threshold-calibration percentiles in a sane range? If top score never exceeded 6.5, either the threshold is wrong or the regime is bad — both useful facts.
- **After day 7:** Spider can strike when the next qualifying signal appears. Could be day 8, could be day 30. Patience is the strategy.

## Benchmark

Spider only justifies its complexity if it beats *"buy and hold the anchor it picks"* on a risk-adjusted basis. If it doesn't, the basket is noise and we kill it. Track this benchmark explicitly in weekly `ATTRIBUTION` records (`benchmark_buy_and_hold_pnl` field).

Secondary success criteria — Spider should also:
- Stay under 12 fills per rolling 7d window (no `FILL_CAP_WARNING` events)
- Keep `fee_pct_of_gross` under 25% on rolling 4w (no `FEE_CEILING_WARNING` events)
- Fire `safety_stop` no more than once per 30d (no `SAFETY_STOP_WARNING` events)

If all three guardrails hold, Spider is operating in the Vulture / Kodiak quadrant: low turnover, strict gates, asymmetric payoffs. If any breach, Spider is drifting toward the Cheetah / Roach quadrant: bleeding fees through positive gross edge.
