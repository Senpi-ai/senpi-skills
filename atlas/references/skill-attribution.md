# Atlas — Skill Attribution

**Skill:** atlas-strategy v0.1.0
**Author:** jason-goldberg
**License:** MIT
**Created:** 2026-04-22

## Lineage

Atlas is **the first Senpi strategy that operates at portfolio composition level**. It is structurally distinct from all prior Predator skills, which are single-position scanners.

### Inherited patterns

- **Persistent JSONL log on disk** — pattern from Wolverine v2.3 (`entry-log.jsonl`). Generalized in Atlas to a typed event log (`atlas-log.jsonl`) covering scans, decisions, strikes, pilot ramps, aborts, warmup transitions, attributions, and errors.
- **Conviction-scaled sizing** — pattern from Wolverine v2.3 (`LEVERAGE_TIERS`). Generalized in Atlas to *portfolio-level* conviction, where the agent's self-rated conviction (0.0–10.0) modulates anchor sizing.
- **Fleet-standard SKILL.md / runtime.yaml / scripts/ / references/ structure** — matches existing skills (Wolverine, Polar, Vulture, etc.).
- **MCP tool surface** — `leaderboard_get_markets`, `arena_leaderboard`, `market_get_funding_*`, `account_get_portfolio`, `create_position`, `close_position`, `audit_*`.

### New patterns introduced

- **Five-decision agentic action layer** (HOLD / SWAP / RESIZE / FULL_REBALANCE / GO_TO_CASH). No prior Senpi strategy has GO_TO_CASH as a first-class output.
- **Cold-start warmup with paper-trading** — 7-day observation window where the agent runs the full decision loop but produces zero trades. Operator-facing rationale review.
- **Staggered pilot entry** — first strike enters in 3 phases (50% / 75% / 100% anchor, 0% / 25% / 40% basket) with abort triggers at each phase. Encodes epistemic humility into the strategy.
- **Counterfactual logging** — every `DECISION` record stores `alternatives_considered` with score gap and why-rejected. Enables retrospective scoring of whether the agent *chose* vs. defaulted to top score.
- **Three-population signal philosophy** — Arena leaders (primary positive signal) + Smart Money (foundation) + Predators fleet (concentration awareness via leverage cap, never a hard veto).
- **Weekly attribution** with explicit benchmark — Atlas only justifies its complexity if it beats *"buy and hold the anchor it picks"* on a risk-adjusted basis.

## Dependencies

- `senpi-trading-runtime` — runtime YAML interpreter, scanner orchestration
- `dsl-dynamic-stop-loss` — Atlas v0.1 uses per-leg safety stops only; the planned `dsl_portfolio` exit engine (not yet shipped) will replace these with portfolio-level exit logic

## What Atlas is designed *for* but does not yet have

Atlas is the first Senpi agent designed against the **proposed data-layer additions** rather than the current bare MCP tool surface. As these primitives ship, Atlas's decision quality improves without code change:

- **Funding regime context bundle** — currently Atlas computes funding favorability from raw `market_get_funding_history`
- **Volatility / chop regime classifier** — currently Atlas infers regime from price history
- **Fleet overlap aggregator** — currently Atlas would need to walk all Predator strategy states to compute concentration
- **Trade journal API** — currently Atlas's attribution layer relies on its own `atlas-log.jsonl` rather than a runtime-provided trade history

Atlas is the proof case for why these primitives matter.

## Status

**v0.1 — initial skeleton.** Rationale log module is production-ready. Composite-score scanners specified in `runtime.yaml` but not yet implemented as Python — pending decision on whether the runtime materializes them or Atlas needs `scripts/atlas-scanner.py`. The `dsl_portfolio` exit engine is a runtime addition not yet shipped; Atlas enforces portfolio-level risk in the LLM action layer until then.
