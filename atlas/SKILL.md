---
name: atlas-strategy
description: >-
  ATLAS v0.1 — Agentic portfolio operator. The first Senpi strategy that
  decides at portfolio composition level (anchor + hedged short basket),
  not single-position level. Long horizon (7+ day holds), low turnover.
  Daily LLM decision loop with five outcomes (HOLD, SWAP, RESIZE,
  FULL_REBALANCE, GO_TO_CASH). 7-day paper-trading warmup before first
  strike. Staggered pilot entry. Reads Arena leaders as primary positive
  signal and broader fleet as concentration-risk overlay.
license: MIT
metadata:
  author: jason-goldberg
  version: "0.1"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🗺️ ATLAS v0.1 — Agentic Portfolio Operator

The first Senpi agent that operates **above the position level**.

Every other Predator decides "open or close this trade?" every few minutes. Atlas decides "what should the portfolio look like today?" once a day.

## The shape of the strategy

Atlas runs a **two-leg portfolio**:

1. **Anchor** — a single high-conviction long (e.g. HYPE 3x), held minimum 7 days
2. **Basket** — 3–5 shorts on the highest-funding alt-coins, risk-parity sized to 40% of anchor notional

The basket isn't a directional hedge. It's a **funding harvest** — shorts on coins paying the most positive funding to longs collect rate while providing partial downside cushion to the anchor.

Net portfolio: long-biased, partially hedged, funding-positive. Ideally throws off carry whether the anchor moves up modestly, sideways, or even slightly down.

## Why this shape sidesteps three fleet diseases

- **Fee disease:** ~10–20 fills/week vs. hundreds. Fees become a rounding error.
- **Chop disease:** 7-day horizons don't care about 4h chop.
- **Trailing-stop mismatch:** there is no trailing stop on individual legs — the basket *is* the position.

## The daily decision loop

Each cycle (13:00 UTC, skipping Thursday morning UTC because Arena resets) Atlas:

1. Reads four scanner outputs:
   - `anchor_candidates` — top 5 from top-15-volume universe, scored on Arena leader exposure (40%) + SM consensus delta (30%) + funding favorability (15%) + 30d relative strength (15%)
   - `basket_candidates` — top 10 from top-50-volume universe, scored on funding descending (40%) + SM rotation negative (25%) + relative weakness vs anchor (20%) + squeeze risk inverse (15%)
   - `portfolio_state` — current legs, days held, P&L, funding accrued
   - `fleet_overlap` — what other Predators are positioned in (concentration awareness, never a hard veto)

2. Outputs one of five decisions:

| Decision | When |
|---|---|
| `HOLD` | Portfolio still scoring well, no member's thesis broken |
| `SWAP_BASKET_MEMBER` | One basket member's score collapsed (funding flipped, squeeze risk, SM rotation) |
| `RESIZE` | Drawdown trigger or thesis strengthening |
| `FULL_REBALANCE` | Weekly review or regime shift detected |
| `GO_TO_CASH` | No high-conviction anchor available, or circuit-breaker fired |

3. Writes a `DECISION` record to `state/atlas-log.jsonl` with the full rationale and counterfactuals (alternatives considered + why rejected).

`GO_TO_CASH` is a **first-class output**. Atlas is allowed — and expected — to refuse to trade when conviction is insufficient.

## Cold start — the most important section

Atlas does not trade for the first 7 days of its life.

### Phase A — Warmup (days 1–7)
Atlas boots in `GO_TO_CASH`. Runs the full decision loop daily. **Produces zero trades.** Every day it logs to `atlas-log.jsonl`: *"I would have picked anchor=X, basket=[A,B,C,D], conviction=7.2, because..."*

The operator reviews these records before any capital moves. The agent earns trust through observable judgment, not through marketing.

### Phase B — First strike
Strike condition (all three required):
1. Warmup complete (7 days observed)
2. Top anchor candidate scores **≥ 7.0 for 2 consecutive daily scans** (persistence — never a single-day spike)
3. No catastrophic regime signal (BTC down >10% in 48h, funding regime broadly flipping, vol-regime expansion spike)

When strike fires, Atlas enters in three staggered steps:

| Day | Anchor size | Basket size | Condition |
|---|---|---|---|
| 1 | 50% | 0% | Strike fires |
| 2 | 75% | 25% | Thesis confirming (anchor up or flat, score still ≥ 7.0) |
| 3 | 100% | 40% | Thesis confirming |

If at any pilot day the anchor drawdown exceeds 5% or the score collapses below 6.5 → **abort**: close everything, return to `GO_TO_CASH`, reset warmup to 3 days, retry.

The first trade Atlas ever makes has the worst signal-to-noise ratio of any decision it will ever make. Pilot sizing is the agent encoding epistemic humility into the strategy.

### Phase C — Steady state
Day 4 onward. Normal daily decision loop with full positions and real P&L feedback.

## Signal source philosophy

Three populations, three uses:

| Population | Use |
|---|---|
| **Arena leaders** (7d ROE, top 10) | Primary positive signal for anchor selection |
| **Smart Money** (broader HL leaderboard) | Foundation signal for both anchor and basket |
| **Predators fleet** (incl. Atlas itself, self-excluded) | Concentration awareness — leverage modifier only, never a hard veto |

The Predators read is a **portfolio-risk discipline**, not a quality judgment. Same check a real fund runs: *"before I add HYPE long, what's the firm's existing HYPE exposure across all books?"* Concentration only modifies sizing — it never blocks a trade.

## The rationale log

Every scanner output, decision, pilot ramp, abort, and weekly attribution writes a typed JSONL record to `state/atlas-log.jsonl`. The log:

- Survives session clears (lives on disk, not in LLM context)
- Is append-only (no record is ever mutated; attributions reference prior decisions by id)
- Captures counterfactuals on every decision (alternatives + score gap + why rejected)
- Calibrates the conviction threshold during warmup (top score percentiles)
- Powers weekly retrospective scoring (`ATTRIBUTION` event)

See `references/rationale-log-schema.md` for the full event taxonomy.

## What's not yet built (and that's the point)

Atlas v0.1 is the agent layer. Two runtime additions are needed for it to operate at full quality:

1. **`dsl_portfolio` exit engine** — portfolio-level exits with `gross_exposure_cap`, `net_delta_target`, `weekly_drawdown_circuit`. Until this ships, Atlas enforces these in the LLM action layer (fragile but functional).
2. **Data-layer primitives** — funding regime context, vol regime classifier, fleet overlap aggregator, trade journal API. Atlas is the first agent designed *for* these abstractions; without them it has to compute equivalents on the fly.

Atlas will get strictly better as both ship. No code change required.

## Install

```bash
mkdir -p /data/workspace/skills/atlas-strategy/{config,scripts,state}

# Pull the skill
gh repo clone Senpi-ai/senpi-skills /tmp/senpi-skills
cp -r /tmp/senpi-skills/atlas/* /data/workspace/skills/atlas-strategy/

# Configure
cp /data/workspace/skills/atlas-strategy/config/atlas-config.example.json \
   /data/workspace/skills/atlas-strategy/config/atlas-config.json
# Edit STATE_DIR, ANCHOR_UNIVERSE if you want to override defaults

# Boot — Atlas will begin warmup automatically
openclaw runtime start atlas-tracker
```

## Operator checklist for the first 7 days

- Day 1: confirm `atlas-log.jsonl` is being written (one `SCAN` + one `DECISION` per cycle)
- Day 3: read the rationale text on each `DECISION` record. Does the agent's reasoning match what you'd want?
- Day 5: check `warmup_status()` — how many qualifying strike windows has the agent observed?
- Day 7: review `WARMUP_END` summary. Are the threshold-calibration percentiles in a sane range? If top score never exceeded 6.5, either the threshold is wrong or the regime is bad — both useful facts.
- After day 7: Atlas can strike when the next qualifying signal appears. Could be day 8, could be day 30. Patience is the strategy.

## Benchmark

Atlas only justifies its complexity if it beats *"buy and hold the anchor it picks"* on a risk-adjusted basis. If it doesn't, the basket is noise and we kill it. Track this benchmark explicitly in weekly `ATTRIBUTION` records.
