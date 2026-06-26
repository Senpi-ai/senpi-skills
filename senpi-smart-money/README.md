# senpi-smart-money

A Senpi skill that answers **"where is smart money moving?"** — it shows where the proven,
most-profitable Hyperliquid wallets are positioned, where they diverge from the crowd, and what the
near-term flow looks like. The conversational, read-only counterpart to the **whalehunter** strategy.

Modeled on `senpi-strategy-discover` / `senpi-market-pulse`: a **hidden deterministic engine** does
the heavy data work; the **LLM (SKILL.md)** does the analysis, narration, and the closing CTAs.

## What it reads

Two cohorts, defined by **lifetime realized PnL** (the only honest "who's actually good" measure):

- **Smart money** — wallets with **≥ $1M realized gains** (the proven cohort).
- **The crowd** — wallets with **$10k–$100k realized** (good, but the followers).

It aggregates each cohort's **net positioning** per coin (`bias = net/gross ∈ [−1,+1]`), finds the
**divergences** (where the two are on opposite sides — the core signal), and overlays the near-term
**Leaderboard/Hyperfeed 4h flow** (is the move building or fading).

This mirrors the whalehunter strategy's cohort engine exactly — same definitions, same bias math —
but as a one-shot *read* instead of a running trader.

## Architecture

```
scripts/smartmoney.py   hidden engine — cohort build (paged discovery_get_top_traders) +
                        bias aggregation (discovery_get_trader_state) + divergence detection +
                        health-gated Leaderboard/Hyperfeed near-term layer → JSON
scripts/mcp_client.py         self-contained streamable-HTTP MCP client (stdlib only; read-only)
SKILL.md                the analyst — lead-with-divergence output contract + the two CTAs
references/analysis-framework.md   bias×members, divergence, all-time vs near-term, early-not-wrong
tests/                  offline fixture test (no network)
```

## How it works

1. **Build cohorts.** Page the ALL_TIME realized-PnL ranking (`discovery_get_top_traders`) until both
   the ≥$1M smart band and the $10–100k crowd band are sampled. The crowd sits thousands of ranks
   below the smart top, so a single top-N pull misses it — hence the paging (mirrors whalehunter).
2. **Aggregate bias.** `discovery_get_trader_state` per cohort (batched) → net/gross/long/short per
   coin → `bias ∈ [−1,+1]` with member counts.
3. **Find divergences.** Coins where smart and crowd are on opposite sides (or far apart in
   conviction), ranked opposite-sides-first.
4. **Overlay near-term flow.** Health-gated `leaderboard_*` — concentration, hot traders, momentum
   events (scale-ins = building, exits = fading). Degrades to `null` cleanly if Hyperfeed is down.

Fails open end-to-end: partial data still returns valid JSON with `meta.warnings`.

## Output

`{cohorts, smart_leaning, divergences, near_term, meta}` →  the LLM narrates: headline lean → smart
vs crowd divergence → near-term confirm/contradict → bottom line + watch list → **two CTAs**:

> 1. Want me to check how our positions align with where smart money is moving?
> 2. Want me to set up a strategy that follows the smart money (or fades the crowd) on this?

CTA 2 routes to **senpi-strategy-author** (or names the **whalehunter** template, which trades this
exact divergence) with a brief built from the strongest divergence — proposes, never auto-builds.

## Run

```sh
python3 scripts/smartmoney.py            # full pull (cohorts + divergence + near-term)
python3 scripts/smartmoney.py --no-near  # skip the Leaderboard/Hyperfeed layer
python3 scripts/smartmoney.py --dry      # dump raw MCP responses for schema debugging
python3 scripts/smartmoney.py --fixture tests/fixtures/smartmoney_fixture.json   # offline (tests)
```

## ⚠ Token scope

`discovery_*` needs a **USER-scoped** `SENPI_AUTH_TOKEN` (it resolves a user id). An app-scoped token
returns empty cohorts and the engine sets `meta.cohorts_unavailable` — the SKILL narrates that
honestly rather than reporting an empty smart cohort as "flat." Same requirement as the whalehunter
producer.

## Status / review notes

- **Cohort + bias logic is ported from `whalehunter/scripts/whalehunter-producer.py`** (realized-PnL
  bands, paged sampling, `bias = net/gross`). Field-name fallbacks (`realizedProfitAndLoss`,
  `openPositions`, `szi`/`positionValue`) match the producer's live usage — `--dry` dumps raw
  responses if the schema drifts.
- Cohort thresholds (`SMART_MIN_REALIZED`, crowd band, paging caps) are constants at the top of
  `smartmoney.py`, team-editable.
- Offline fixture test included; no network required.
