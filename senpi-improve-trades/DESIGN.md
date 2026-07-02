# senpi-improve-trades — Design Spec

_Status: approved design (2026-07-02). Feeds the implementation plan; not the SKILL.md itself._

## Purpose

A **retrospective trade-review + improvement-coaching** skill for the Senpi trading agent. It owns the "Improve My Trades" quick-action cluster:

- "Improve my last N trades — did I sell too early or late?"
- "What did I miss this week?" / "Master my week"
- "Compare my positions and trades to the best market whales" / "…to the broader market"
- "Suggest improvements to my portfolio" / "How could I be making more gains?"

It **owns** the retrospective analysis + the improvement coaching, and **composes** `senpi-market-pulse` (what moved), `senpi-smart-money` (whales), and the `senpi-portfolio` registry approach (mandate + DSL) for context — it does not re-implement them. It is the counterpart to `senpi-portfolio`: portfolio answers "where is my money / how are my strategies doing **now**"; improve-trades answers "how did my **closed** trades do, what did the market do, and how do I get better."

## Why it exists — the failure modes it fixes

Taken directly from live agent responses to these prompts. The skill exists to constrain these into a disciplined flow:

1. **Hindsight / outcome bias** — grading an exit "too early" purely because the asset later reversed ("COIN −$9 → +$126 if held"). Results-oriented, not process evaluation.
2. **Fabricated $/week projections** — inventing forward numbers ("+$1,400–2,200/week," "deploy 25% = +$800–1,200") that read as outcome guarantees.
3. **Recency / performance-chasing** — recommending the user abandon a strategy's mandate to buy last window's winners (crypto longs, semi shorts).
4. **User-vs-strategy confusion** — "you sold too early, widen your stops" on trades an **autonomous strategy** exited. The lever is the strategy config, not the user's behavior.
5. **Re-breaking the multi-wallet rule** — "close the duplicate Ox/Cougar/Lion wallets" (those are `core`+`ballast` / `long`+`short` **sleeves**).
6. **Reliability gaps** — skipped the entry→exit→**current-price** comparison until prompted; repeated `market_get_prices` / `execution_get_closed_position_details` failures.
7. **Dependency on `audit_*` tools**, which are being deleted (see `senpi-audit` removal).

## Architecture

Standalone skill, mirroring the `senpi-portfolio` shape:

```
senpi-improve-trades/
  SKILL.md                     # narration + guardrails (the spine)
  scripts/
    review.py                  # hidden engine — deterministic data work
    mcp_client.py              # vendored MCP helper (stdlib)
    _yaml.py                   # vendored YAML parser (for the runtime.yaml registry / DSL)
  tests/
    test_review.py             # fixture-based, offline
    fixtures/…
  README.md
```

The engine does the precise, deterministic data work; the SKILL.md does the prose, the process-framing, and the CTAs under the guardrails. **The engine is the anti-fabrication mechanism**: because the timing table and market-gap are computed for the LLM, it cannot skip the current-price comparison or invent forward numbers — it narrates real values.

## Engine (`review.py`) — outputs

Returns one JSON document the LLM narrates. All sources read-guarded + fail-open (partial data → valid JSON + `meta.warnings`). Requires a USER-scoped token.

```
{
  window: { from, to, label },                 # the review window (default ~7d; overridable)

  trades: [                                     # per CLOSED trade
    { asset, strategy_label, instance, direction, leverage,
      entry_px, exit_px, entry_time, exit_time,
      realized_pnl, roe_pct,
      price_now, price_since_exit_pct,          # subsequent action (market_get_asset_data/prices)
      if_held_delta_usd,                        # counterfactual — CONTEXT, not verdict
      exit_reason: {                            # authoritative, from ratchet_stop_events
        terminal: "SL_TRIGGERED"|"HARD_STOP"|"TIME_CUT"|"MANUAL_CLOSE"|"SIGNAL"|"UNKNOWN",
        tier_reached, high_water_roe, final_lock_pct },
      exit_vs_hold: "beat"|"worse"|"flat" }     # engine verdict of exit vs holding-to-now (process input)
  ],

  timing_summary: {                             # PROCESS-framed COUNTS, never $ projections
    trade_count, exits_beat_holding, exits_worse, exits_flat,
    realized_pnl_total,
    if_all_reclosed_now_total,                  # the honest counterfactual aggregate
    by_asset_class: {...} },

  book_vs_market: {                             # the "what did I miss" gap
    top_movers: [ { asset, asset_class, pct } ],        # biggest movers this window
    participation: [ { asset, held: bool, side, aligned: bool } ],
    gaps: [ assets that moved big and the book had no exposure to ] },

  strategies: [                                 # per strategy: judged vs ITS mandate
    { label, mandate, closed_trade_count, realized_pnl, on_mandate_note } ],

  meta: { warnings[], sources[], window, degraded } }
```

Notes:
- **`exit_reason` is authoritative**, reconstructed from `ratchet_stop_events({strategyId, strategy_wallet_address, since})` (+ `ratchet_stop_list` for the tier ladder), mapped to each closed trade by asset + time / positionId. This gives the real exit mechanism (which tier locked, hard stop, time-cut) — i.e. **which lever to tune**. Not best-effort; the BE provides it.
- **No forward projections** anywhere. The engine reports realized PnL + engine-computed counterfactuals (`if_held_delta`, `if_all_reclosed_now_total`). It never emits "+$X/week."
- `exit_vs_hold` + `timing_summary` counts exist so the agent leads with the **aggregate** ("N of M exits beat holding"), countering the urge to cherry-pick the few reversals.
- Whale comparison is **not** in the engine — the SKILL.md composes `senpi-smart-money` for that (keeps the engine focused).

## Guardrails (SKILL.md spine — the reason this skill exists)

1. **Process over outcome.** `if_held_delta` is **context, never the verdict**. A disciplined exit is not "wrong" because the asset later reversed. Lead with `timing_summary` (e.g. "10 of 14 exits beat holding") before discussing any single reversal.
2. **It's the strategy, not you.** These are autonomous trades. Improvements tune the **strategy** — the DSL preset (per-asset volatility) or entry gates — reported in strategy terms and routed to `senpi-strategy-author` / `-ops`. Never "you should have held."
3. **No fabricated numbers.** Only realized PnL + engine counterfactuals. Never project "+$X/week," "deploy 25% = +$Y," or any guaranteed-gain figure.
4. **No chasing.** Do not recommend abandoning a strategy's mandate to buy last window's winners. One window is noise; weigh turnover cost + regime durability. (Inherits the portfolio "don't tear down a deliberate book to chase a short signal" rule.)
5. **Inherit the portfolio rules.** Multi-wallet = ONE strategy (never "close a duplicate sleeve" — that's a naked leg); a flat sleeve / idle is often by design; judge each strategy vs its own mandate, not a momentum benchmark.
6. **Honest sourcing.** Missing price / horizon / event → say so; never invent. Closed trades from `discovery_get_trader_history` (**not** `audit_*`). Exit attribution from `ratchet_stop_events`.
7. **User chooses the fix depth** (below) — the skill never auto-acts.

## Actionability — the user chooses depth

After diagnosing, the skill **offers a choice** and never acts unprompted:
- **Explain only** — the diagnosis + the plain-terms fix.
- **Hand to author/ops** — route the concrete change (e.g. "widen `phase1.retrace_threshold` for high-vol crypto in Lion") to `senpi-strategy-author` / `-ops` to apply.
- **Draft the config change** — produce the specific `runtime.yaml` / DSL-preset diff for the user to review before it's applied.

## Data sources (authoritative)

| Need | Source |
|---|---|
| Closed trades | `discovery_get_trader_history` (per strategy wallet) — **not** `audit_*` |
| Subsequent / current price | `market_get_asset_data` / `market_get_prices` |
| Exit attribution (tier / hard-stop / time-cut) | `ratchet_stop_events` (+ `ratchet_stop_list` for the ladder) |
| Strategy mandate + DSL config | `installed_runtimes.json` registry (reuse `senpi-portfolio`'s `load_runtime_registry`) |
| Market movers this window | `leaderboard_get_markets` / compose `senpi-market-pulse` |
| Whale positioning | compose `senpi-smart-money` |

## Output contract (what the agent produces)

1. **Timing teardown** — the per-trade table, **process-framed**, led by the aggregate (`timing_summary`), each exit attributed via `exit_reason`.
2. **Book-vs-market gap** — what moved vs what you held (the honest "what did I miss").
3. **Per-strategy read** — each strategy judged vs its own mandate (realized PnL as evidence).
4. **Improvements** — each tied to a **strategy lever** (DSL / entry gate), no guaranteed-gain language, then the **fix-depth choice**.

## Non-goals

- Not real-time portfolio state — that's `senpi-portfolio`.
- No forward dollar projections; no "you'd make $X/week."
- No auto-trading and no auto-applying config changes.
- Does not re-implement market-pulse / smart-money / portfolio — it composes them.

## Testing

Fixture-based, offline (mirrors `senpi-portfolio/tests`): recorded `discovery_get_trader_history` + `market_*` prices + `ratchet_stop_events` + a registry fixture → assert:
- the `if_held_delta` and `timing_summary` counts compute correctly (incl. an exit that "beat holding" and one that "was worse");
- `exit_reason` maps a `SL_TRIGGERED` event to the right trade + tier;
- `book_vs_market.gaps` surfaces a mover the book didn't hold;
- fail-open when any single source is missing (valid JSON + `meta.warnings`).
Plus a SKILL.md guardrail checklist (no forward-$ language; process-first; strategy-not-user).

## Ship / integration

- New skill → register in `senpi-trading-runtime` `skills-manifest.json` **and** `manifest.ts` (11 → 12 skills).
- Add a routing row to the `senpi-agent` AGENTS.md map: "review my trades / did I sell too early or late / what did I miss / master my week / how could I make more gains" → **senpi-improve-trades** (composes market-pulse · smart-money · portfolio).
- Version starts at `1.0.0`.
