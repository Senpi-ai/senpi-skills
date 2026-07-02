# senpi-improve-trades

A Senpi skill that answers **"review my trades — did I sell too early or late? What did I miss this week?
How could I make more gains?"** with a disciplined retrospective review + improvement coaching — not
hindsight bias and invented forward numbers.

It is the counterpart to `senpi-portfolio`: portfolio answers *"where is my money / how are my strategies
doing now"*; improve-trades answers *"how did my **closed** trades do, what did the market do, and how do I
get better."*

Modeled on the same shape as `senpi-portfolio`: a **hidden deterministic engine** (`scripts/review.py`)
does the precise data work; the **LLM (`SKILL.md`)** does the coaching under strict guardrails.

## What it does

Reconstructs every **closed** trade in a window and, per trade, computes:
- the **exit mechanism** — which DSL tier or hard stop fired (`exit_reason`, authoritative);
- the honest **"if I'd held to now" counterfactual** (`if_held_delta_usd`), direction-adjusted so a short
  gains when price falls — reported as **context, never a verdict**;
- whether the realized exit **beat / was worse than / matched** holding-to-now (`exit_vs_hold`).

Then it aggregates into **process-framed counts** (`timing_summary`: "N of M exits beat holding") — never a
$/week projection — crosses the book against **what the market did** (`book_vs_market`: movers, participation,
and gaps the book had no exposure to), and gives a **per-strategy read judged against each strategy's own
mandate**.

The engine is the anti-fabrication mechanism: because the timing table and the market gap are computed for the
LLM, it can't skip the current-price comparison or invent forward numbers — it narrates real values under the
seven guardrails in `SKILL.md` (process over outcome · it's the strategy not you · no fabricated $/week · no
chasing · inherit the portfolio rules · honest sourcing · user chooses the fix depth).

## Reconstructed sources (v1)

| Need | Source |
|---|---|
| Closed trades (entry/exit px, signed size, realized PnL, timing) | `discovery_get_trader_history` — **not** `audit_*` |
| Exit attribution (which tier / hard stop / liquidation fired) | `ratchet_stop_list` (`status: ALL`) |
| Current price for the "if held to now" counterfactual | `market_get_asset_data` (current mark only; no historical candles in v1) |
| Strategy mandate + DSL ladder (which lever a bad exit maps to) | deployed `runtime.yaml` in `installed_runtimes.json` |
| Market movers this window | `leaderboard_get_markets` |
| Whale positioning | composes `senpi-smart-money` |

Every trade is tagged `source: "reconstructed"`.

> **v2 telemetry note.** Telemetry (the successor to the removed `audit_*` tools) will carry the user's richer
> per-trade record — the actual entry thesis + score and the exact exit trigger. It slots into the engine's
> `_collect_trades()` source boundary as a higher-fidelity / primary source (trades then read
> `source: "telemetry"`) **with no change to the guardrails, narration, or output shape.** v1 ships on the
> reconstructed sources so it works today.

## Composition

Composes rather than re-implements: `senpi-market-pulse` (the movers narrative), `senpi-smart-money` (the whale
comparison), and `senpi-portfolio` (live state + current mandate/DSL posture).

## Architecture

```
scripts/review.py     hidden engine — strategy_list + per-wallet discovery_get_trader_history +
                      ratchet_stop_list(ALL) + market_get_asset_data + leaderboard_get_markets →
                      per-trade timing/exit attribution + process-framed counts + book-vs-market gap
scripts/mcp_client.py self-contained streamable-HTTP MCP client (stdlib only; read-only) — vendored
scripts/_yaml.py      stdlib YAML loader for the runtime.yaml registry / DSL ladder — vendored
SKILL.md              the coach — the seven guardrails + the four-part output contract + fix-depth CTA
tests/                offline fixture test (no network)
```

Fails open end-to-end; partial data still returns valid JSON with `meta.warnings` (and `meta.degraded` when
there's no usable data).

## Install

Copy the whole skill directory (the engine needs the entire `scripts/` folder — `review.py` imports the
vendored `mcp_client.py` and `_yaml.py`). Then via the skills installer:

```sh
npx skills add https://github.com/Senpi-ai/senpi-skills --list
```

## Run

```sh
python3 scripts/review.py                 # last ~7d review (all strategy wallets)
python3 scripts/review.py --window 30     # last 30 days
python3 scripts/review.py --last 20       # cap to the last 20 closed trades
python3 scripts/review.py --no-market     # skip the current-price + book-vs-market pull
python3 scripts/review.py --dry           # dump raw MCP responses for schema debugging
python3 scripts/review.py --fixture tests/fixtures/review_fixture.json   # offline (tests)
```

## Tests

```sh
python3 tests/test_review.py    # prints "N/N passed" (pytest not required)
```

Offline, no network. Guards the load-bearing math: `if_held_delta` / `since_exit_pct` (incl. the short sign),
the `timing_summary` beat-vs-worse counts, `exit_reason` mapping `SL_TRIGGERED → tier`, the `book_vs_market`
gap surfacing an unheld mover, and fail-open behavior when a source is missing.

## ⚠ Token scope

Every tool here is **USER-scoped** (the user's own account): needs a USER-scoped `SENPI_AUTH_TOKEN`. An
app-scoped token returns no strategy/trade data and the engine sets `meta.degraded` — the SKILL says so
rather than reporting "no trades."
```
