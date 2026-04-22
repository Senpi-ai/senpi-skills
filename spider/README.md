# 🕷️ SPIDER v2.0 — Agentic Portfolio Operator

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

The first Senpi agent that decides at **portfolio composition** level — anchor + hedged short basket — instead of single-position level. Long horizon (7+ day holds), low turnover, funding-harvest oriented, fee-aware, forced-exit-tracking.

> The spider builds a web. One anchor point. Several radial threads. Then it waits. The web does the work. Vibrations come to it.

## What v2.0 is

Spider v2.0 is a **complete rewrite from v1.0**. v1.0 was an Elite-convergence single-position scanner that contributed to the same fleet pathology v2.0 is designed to correct: too many fills, weak signals, repeated DSL stops. v2.0 is structurally a different agent.

## What Spider does

- **Picks an anchor** (high-conviction long, max 3x leverage, min 7-day hold) from top-15 volume perps using Arena leader exposure (40%) + SM consensus delta (30%) + funding favorability (15%) + 30d relative strength (15%)
- **Builds a basket** of 3–5 shorts from top-50 volume perps using funding descending (40%) + SM rotation negative (25%) + relative weakness vs anchor (20%) + squeeze risk inverse (15%) — **each member must individually score ≥ 6.5** or it doesn't make the cut
- **Sizes the basket** to 40% of anchor notional, risk-parity weighted
- **Decides daily** via LLM action layer with five outcomes: HOLD, SWAP_BASKET_MEMBER, RESIZE, FULL_REBALANCE, GO_TO_CASH
- **Logs every decision** to `state/spider-log.jsonl` with rationale + counterfactuals (survives session clears)
- **Caps fills at 12 per rolling 7d** — beyond that the agent must justify each in writing
- **Surfaces fee health** every weekly attribution (`fee_pct_of_gross` is the primary metric)
- **Tracks every leg close** with typed `exit_reason` — forced exits (safety_stop, circuit_breaker) are flagged loudly
- **Refuses to trade** for the first 7 days (warmup) and on insufficient conviction (GO_TO_CASH is first-class)

## Why a portfolio operator

Three of the four diagnosed Predator-fleet diseases vanish by construction:

| Disease | Why Spider is immune |
|---|---|
| Fee disease | ~10–20 fills/week vs hundreds. Fees become rounding error. |
| Chop disease | 7-day horizons don't care about 4h chop. |
| Trailing-stop mismatch | No per-leg trailing stop — basket *is* the position. |

What Spider captures that no other Predator does: **funding harvest**. Over 7-day holds, funding rates dwarf intraday price noise on most alts. A basket of high-funding shorts is net-positive carry even if prices don't move.

## v2.0 fleet-learning constraints

Spider v2.0 directly bakes in the lessons of the Apr 2026 fleet analysis:

> "Bleeders are over-trading — relying on weak signals and hitting DSL repeatedly. The way to win big is high conviction + longer hold times. Most bleeders have positive gross edge eaten by fees."

Concrete observations from the fleet at the time of v2.0:

| Agent | Fills | Gross | Fees | Net |
|---|---|---|---|---|
| Cheetah | 409 | +$1,317 | ~$1,600 | **-$283** |
| Roach | 506 | +$1,720 | ~$2,000 | **-$280** |
| Roach-B | 407 | +$1,378 | ~$1,600 | **-$222** |
| Scorpion | 423 | +$1,489 | ~$1,700 | **-$211** |
| Lemon | 215 | +$660 | ~$860 | **-$200** |
| **Vulture** | **72** | +$247 | ~$216 | **+$31** ✅ |
| **Kodiak** | **316** | +$1,636 | ~$1,580 | **+$56** ✅ |

The winners aren't winning by gross edge — Cheetah's +$1,317 gross is bigger than Vulture's +$247. The winners win by **paying less to capture less**. Spider v2.0 is built to operate in the Vulture / Kodiak quadrant.

Four runtime constraints encode this directly:

1. **`trade_frequency_constraint`** — max 12 fills per 7d, soft warning at 8
2. **`risk_envelope.basket.member_min_score: 6.5`** — basket members must individually qualify; <3 qualifiers → anchor-only mode
3. **`fee_health_monitoring`** — `FEE_CEILING_WARNING` event when rolling 4w `fee_pct_of_gross` > 25%
4. **`forced_exit_monitoring`** — every leg close typed; `SAFETY_STOP_WARNING` when >1 safety_stop in 30d

## Cold start — the most important behavior

Spider does **not** trade for the first 7 days. It runs the full decision loop daily, paper-trades every decision, and writes the rationale to `spider-log.jsonl`. Operator reviews 7 days of agent reasoning before a dollar moves.

After warmup, first strike requires:
1. Top anchor candidate scores ≥ 7.0 for **2 consecutive daily scans** (no single-day spikes)
2. No catastrophic regime signal (BTC -10% in 48h, funding regime flipping, vol expansion spike)
3. Then enters in **3 staggered steps**: 50% anchor day 1, 75% anchor + 25% basket day 2, 100% + 40% day 3 — abort to cash if anchor drops >5% or score falls below 6.5

The first trade Spider ever makes has the worst signal-to-noise ratio of any decision it will make. Pilot sizing is the agent encoding epistemic humility.

## Signal philosophy

| Population | Use |
|---|---|
| **Arena leaders** (7d ROE) | Primary positive signal for anchor |
| **Smart Money** (broader HL leaderboard) | Foundation signal for both anchor and basket |
| **Predators fleet** (incl. Spider itself, self-excluded) | Concentration awareness — leverage modifier only, never a hard veto |

Predators read is a **portfolio-risk discipline**, not a quality judgment. Same check a real fund runs before adding exposure.

## Install

```bash
mkdir -p /data/workspace/skills/spider-strategy/{config,scripts,state}

gh repo clone Senpi-ai/senpi-skills /tmp/senpi-skills
cp -r /tmp/senpi-skills/spider/* /data/workspace/skills/spider-strategy/

cp /data/workspace/skills/spider-strategy/config/spider-config.example.json \
   /data/workspace/skills/spider-strategy/config/spider-config.json

openclaw runtime start spider-tracker
```

## Files

- `runtime.yaml` — agent runtime configuration (scanners, action, cold-start, risk envelope, cycle, v2.0 fleet-learning constraints)
- `SKILL.md` — what the LLM agent reads at runtime
- `scripts/spider_rationale_log.py` — typed JSONL log writer/reader (the warmup artifact + v2.0 EXIT/FEE/SAFETY/FILL warning events)
- `scripts/spider_config.py` — constants + JSON config overlay
- `config/spider-config.example.json` — operator-facing config template
- `references/rationale-log-schema.md` — full event taxonomy with example payloads
- `references/skill-attribution.md` — lineage from v1.0 → v2.0

## Operator checklist for the first 7 days

- **Day 1:** confirm `spider-log.jsonl` is being written (one `SCAN` + one `DECISION` per cycle)
- **Day 3:** read the rationale on each `DECISION` record. Does the reasoning match what you'd want?
- **Day 5:** check `warmup_status()` — how many qualifying strike windows has the agent observed?
- **Day 7:** review `WARMUP_END` record. Top-score percentiles in a sane range?
- **Day 8+:** Spider can strike when the next qualifying signal appears. Could be day 8, could be day 30. Patience is the strategy.

## Benchmark — what success looks like

Spider only justifies its complexity if it beats *"buy and hold the anchor it picks"* on a risk-adjusted basis. If not, the basket is noise and we kill it. Tracked explicitly in weekly `ATTRIBUTION` records.

**Three v2.0 guardrails — Spider must stay inside all three:**

| Guardrail | Threshold | Warning event |
|---|---|---|
| Trade frequency | ≤ 12 fills per rolling 7d | `FILL_CAP_WARNING` |
| Fee health | `fee_pct_of_gross` ≤ 25% on rolling 4w | `FEE_CEILING_WARNING` |
| Loss control | ≤ 1 `safety_stop` per rolling 30d | `SAFETY_STOP_WARNING` |

All three holding = operating in the Vulture / Kodiak quadrant (low turnover, strict gates, asymmetric payoffs).
Any breach = drifting toward the Cheetah / Roach quadrant (bleeding fees through positive gross edge).

## Status

**v2.0 — initial release.** The rationale log module is production-ready and smoke-tested. Composite-score scanners are specified in `runtime.yaml` but not yet implemented as Python — pending decision on whether the Senpi runtime materializes `type: composite_score` natively or Spider needs `scripts/spider-scanner.py`. The `dsl_portfolio` exit engine is a runtime addition not yet shipped; Spider enforces portfolio-level risk in the LLM action layer until then.
