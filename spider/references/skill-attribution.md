# Spider — Skill Attribution

**Skill:** spider-strategy v2.0.0
**Author:** jason-goldberg
**License:** MIT
**Created:** 2026-04-22 (v2.0 rewrite — supersedes v1.0)

## Lineage

### v1.0 (deprecated)
Elite-convergence single-position scanner. Entered when 2+ ELITE/RELIABLE traders with SNIPER/AGGRESSIVE risk independently converged on the same asset and direction, with 15m SM velocity confirmation. Multi-asset. Used the standard fleet pattern: single-position scanner → DSL exit engine.

**Why deprecated:** v1.0 contributed to the same fleet pathology v2.0 exists to correct. Per the Apr 2026 fleet analysis (-25% ROI on $127K volume, 117 trades), v1.0 Spider was over-trading on weak convergence signals and bleeding through DSL stops. Same disease as Cheetah, Roach, Roach-B, Scorpion.

### v2.0 (current — full rewrite)

Spider v2.0 is **the first Senpi strategy that operates at portfolio composition level**. Structurally distinct from v1.0 and from every other Predator skill.

#### Inherited patterns (from elsewhere in the fleet)

- **Persistent JSONL log on disk** — pattern from Wolverine v2.3 (`entry-log.jsonl`). Generalized in Spider to a typed event log (`spider-log.jsonl`) covering scans, decisions, strikes, pilot ramps, exits, aborts, warmup transitions, attributions, and v2.0 warning events.
- **Conviction-scaled sizing** — pattern from Wolverine v2.3 (`LEVERAGE_TIERS`). Generalized in Spider to *portfolio-level* conviction.
- **Fleet-standard SKILL.md / runtime.yaml / scripts/ / references/ structure** — matches existing skills.
- **MCP tool surface** — `leaderboard_get_markets`, `arena_leaderboard`, `market_get_funding_*`, `account_get_portfolio`, `create_position`, `close_position`, `audit_*`.

#### New patterns introduced (no prior Senpi strategy has these)

- **Five-decision agentic action layer** (HOLD / SWAP_BASKET_MEMBER / RESIZE / FULL_REBALANCE / GO_TO_CASH). `GO_TO_CASH` is a first-class output — Spider is allowed to refuse to trade.
- **Cold-start warmup with paper-trading** — 7-day observation window where the agent runs the full decision loop but produces zero trades.
- **Staggered pilot entry** — first strike enters in 3 phases (50% / 75% / 100% anchor, 0% / 25% / 40% basket) with abort triggers at each phase.
- **Counterfactual logging** — every `DECISION` record stores `alternatives_considered` with score gap and why-rejected.
- **Three-population signal philosophy** — Arena leaders (primary positive) + Smart Money (foundation) + Predators fleet (concentration awareness via leverage cap, never a hard veto).
- **Weekly attribution with explicit benchmark** — Spider must beat *"buy and hold the anchor it picks"* on a risk-adjusted basis or its complexity isn't justified.
- **Funding-harvest basket** — first Senpi strategy where the basket's primary alpha source is *funding rate*, not directional thesis.

#### v2.0 fleet-learning constraints (direct response to Apr 2026 fleet analysis)

1. **Trade frequency ceiling** — max 12 fills per rolling 7d window, soft warning at 8. Behavioral constraint enforced in LLM action layer; emits `FILL_CAP_WARNING` when crossed.
2. **Basket member strictness gate** — each member must individually score ≥ 6.5; if fewer than 3 candidates qualify, Spider runs anchor-only mode (no basket).
3. **Fee health monitoring** — `fee_pct_of_gross` is the primary attribution metric; rolling 4w > 25% triggers `FEE_CEILING_WARNING` and the agent must address in next rationale.
4. **Forced-exit tracking** — every leg close writes a typed `EXIT` event; > 1 `safety_stop` in rolling 30d triggers `SAFETY_STOP_WARNING`.

These four constraints encode the lesson from Cheetah / Roach / Vulture / Kodiak directly into the runtime: low turnover + strict gates + fee discipline + control over exits = winning quadrant.

## Dependencies

- `senpi-trading-runtime` — runtime YAML interpreter, scanner orchestration
- `dsl-dynamic-stop-loss` — Spider v2.0 uses per-leg safety stops only as a hard-floor net; the planned `dsl_portfolio` exit engine (not yet shipped) will replace these with portfolio-level exit logic

## What Spider is designed *for* but does not yet have

Spider v2.0 is the first Senpi agent designed against the **proposed data-layer additions** rather than the current bare MCP tool surface. As these primitives ship, Spider's decision quality improves without code change:

- **Funding regime context bundle** — currently Spider computes funding favorability from raw `market_get_funding_history`
- **Volatility / chop regime classifier** — currently Spider infers regime from price history
- **Fleet overlap aggregator** — currently Spider would need to walk all Predator strategy states to compute concentration
- **Trade journal API** — currently Spider's attribution layer relies on its own `spider-log.jsonl` rather than a runtime-provided trade history

Spider is the proof case for why these primitives matter.

## Status

**v2.0 — initial release.** Rationale log module is production-ready and smoke-tested (writer/reader/health helpers all green). Composite-score scanners specified in `runtime.yaml` but not yet implemented as Python — pending decision on whether the runtime materializes them or Spider needs `scripts/spider-scanner.py`. The `dsl_portfolio` exit engine is a runtime addition not yet shipped; Spider enforces portfolio-level risk in the LLM action layer until then.
