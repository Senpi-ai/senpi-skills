# 🗺️ ATLAS v0.1 — Agentic Portfolio Operator

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

The first Senpi agent that decides at **portfolio composition** level — anchor + hedged short basket — instead of single-position level. Long horizon (7+ day holds), low turnover, funding-harvest oriented.

## What Atlas does

- **Picks an anchor** (high-conviction long, max 3x leverage, min 7-day hold) from top-15 volume perps using Arena leader exposure (40%) + SM consensus delta (30%) + funding favorability (15%) + 30d relative strength (15%)
- **Builds a basket** of 3–5 shorts from top-50 volume perps using funding descending (40%) + SM rotation negative (25%) + relative weakness vs anchor (20%) + squeeze risk inverse (15%)
- **Sizes the basket** to 40% of anchor notional, risk-parity weighted
- **Decides daily** via LLM action layer with five outcomes: HOLD, SWAP_BASKET_MEMBER, RESIZE, FULL_REBALANCE, GO_TO_CASH
- **Logs every decision** to `state/atlas-log.jsonl` with rationale + counterfactuals (survives session clears)
- **Refuses to trade** for the first 7 days (warmup) and on insufficient conviction (GO_TO_CASH is first-class)

## Why a portfolio operator

Three of the four diagnosed Predator-fleet diseases vanish by construction:

| Disease | Why Atlas is immune |
|---|---|
| Fee disease | ~10–20 fills/week vs hundreds. Fees become rounding error. |
| Chop disease | 7-day horizons don't care about 4h chop. |
| Trailing-stop mismatch | No per-leg trailing stop — basket *is* the position. |

What Atlas captures that no other Predator does: **funding harvest**. Over 7-day holds, funding rates dwarf intraday price noise on most alts. A basket of high-funding shorts is net-positive carry even if prices don't move.

## Cold start — the most important behavior

Atlas does **not** trade for the first 7 days. It runs the full decision loop daily, paper-trades every decision, and writes the rationale to `atlas-log.jsonl`. Operator reviews 7 days of agent reasoning before a dollar moves.

After warmup, first strike requires:

1. Top anchor candidate scores ≥ 7.0 for **2 consecutive daily scans** (no single-day spikes)
2. No catastrophic regime signal (BTC -10% in 48h, funding regime flipping, vol expansion spike)
3. Then enters in **3 staggered steps**: 50% anchor day 1, 75% anchor + 25% basket day 2, 100% + 40% day 3 — abort to cash if anchor drops >5% or score falls below 6.5

The first trade Atlas ever makes has the worst signal-to-noise ratio of any decision it will make. Pilot sizing is the agent encoding epistemic humility.

## Signal philosophy

| Population | Use |
|---|---|
| **Arena leaders** (7d ROE) | Primary positive signal for anchor |
| **Smart Money** (broader HL leaderboard) | Foundation signal for both anchor and basket |
| **Predators fleet** (incl. Atlas itself, self-excluded) | Concentration awareness — leverage modifier only, never a hard veto |

Predators read is a **portfolio-risk discipline**, not a quality judgment. Same check a real fund runs before adding exposure.

## Install

```bash
mkdir -p /data/workspace/skills/atlas-strategy/{config,scripts,state}

gh repo clone Senpi-ai/senpi-skills /tmp/senpi-skills
cp -r /tmp/senpi-skills/atlas/* /data/workspace/skills/atlas-strategy/

cp /data/workspace/skills/atlas-strategy/config/atlas-config.example.json \
   /data/workspace/skills/atlas-strategy/config/atlas-config.json

openclaw runtime start atlas-tracker
```

## Files

- `runtime.yaml` — agent runtime configuration (scanners, action, cold-start, risk envelope, cycle)
- `SKILL.md` — what the LLM agent reads at runtime
- `scripts/atlas_rationale_log.py` — rationale log writer/reader (the warmup artifact)
- `scripts/atlas_config.py` — constants
- `references/rationale-log-schema.md` — full event taxonomy for `atlas-log.jsonl`
- `config/atlas-config.example.json` — operator-facing config template

## Status

**v0.1 — initial skeleton.** The rationale log module is production-ready. The composite-score scanners (`anchor_candidates`, `basket_candidates`) are specified in `runtime.yaml` but not yet implemented as Python — the runtime will need to materialize them or Atlas will need a `scripts/atlas-scanner.py` to compute them from raw MCP tool calls. The `dsl_portfolio` exit engine is a runtime addition not yet shipped; Atlas enforces portfolio-level risk in the LLM action layer until then.

## Operator checklist for the first 7 days

- **Day 1:** confirm `atlas-log.jsonl` is being written (one `SCAN` + one `DECISION` per cycle)
- **Day 3:** read the rationale on each `DECISION` record. Does the reasoning match what you'd want?
- **Day 5:** check `warmup_status()` — how many qualifying strike windows has the agent observed?
- **Day 7:** review `WARMUP_END` record. Top-score percentiles in a sane range?
- **Day 8+:** Atlas can strike when the next qualifying signal appears. Could be day 8, could be day 30. Patience is the strategy.

## Benchmark

Atlas only justifies its complexity if it beats *"buy and hold the anchor it picks"* on a risk-adjusted basis. If not, the basket is noise and we kill it. Tracked explicitly in weekly `ATTRIBUTION` records.
