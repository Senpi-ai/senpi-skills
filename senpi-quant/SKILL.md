---
name: senpi-quant
description: >-
  Hire your AI quant: paste ANY Hyperliquid address (0x…) and get the desk — a read of the last 90 days
  of fills, fees and funding, the live book with a protection audit, six 0–100 dimensions behind one
  quant score, the leaks ranked by counterfactual dollars, you vs the whale cohort on your coins, market
  fit, and where your edge actually is. Works for wallets that have never touched senpi (public on-chain
  data, no deposit, read-only); with a Senpi token the closed-trade history comes from Senpi discovery
  (complete where the public API is not) and the smart-money cohort from Senpi. Use for "analyze my
  wallet / my Hyperliquid address", "how am I doing", "where am I leaking money", "am I on the right side
  of smart money", "are my positions protected", "what should I fix first", "rate my trading", "compare me
  to the whales", "review this trader 0x…". Hidden engine: scripts/desk.py. NOT for choosing or
  deploying a strategy (senpi-strategy-discover / -ops), reviewing a Senpi strategy's own trades
  (senpi-improve-trades), or vetting a trader to copy (senpi-trader-research).
license: Apache-2.0
metadata:
  author: Senpi
  version: "1.0.0"
  platform: senpi
  exchange: hyperliquid
---

# senpi-quant — the desk for any Hyperliquid address

**HARD RULES — obey these even if you skim the rest.**

1. **One command, then relay.** `python3 senpi-quant/scripts/desk.py <0xaddress>` prints the desk as
   Markdown. Relay it; do not recompute, reorder or "improve" its numbers. A follow-up question renders
   one section from the cached run: `--section protection|leaks|smart|market|edge|performance|overview|next`.
2. **Never invent a number.** Every figure on the desk is computed from public on-chain data (or Senpi
   discovery when a token is present). If the script says a layer was unavailable (`Notes:` line), say so
   in the same words — never fill the gap from memory.
3. **Counterfactual, not history, on every leak.** "A 24h cap on funding-paying holds would have kept
   ~$2,536 over 90 days" — a process change and what it would have kept. Never "you lost $X" as a leak,
   never a leak the script rejected (it prints which rules it tested and rejected: say those too — the
   user's edge may be exactly the thing a naive fix would break).
4. **Process only.** Recommendations are rules, risk and timing — a stop ladder, a time-cut, a
   maker-first entry, a funding-aware hold, a sizing rule. **Never a call to buy or sell a coin.**
5. **Custody language.** The desk is read-only. Protection on existing positions is a signature the
   user gives on positions they already hold; funding a quant is only for autonomous trading. Never
   imply senpi holds or moves their funds.
6. **Say "quant", "desk", "agents", "leak", "protect".** Never "report", "analyst", "bot", "AI assistant".
   Lowercase `senpi`. No outcome guarantees. Close with the footer the script prints.
7. **Address hygiene.** Show the address shortened (`0x2999…65de`). Never post the desk of a wallet the
   user did not name. The desk of another trader's address is analysis of public data, not advice to
   copy them — for copying, route to `senpi-trader-research`.

## Quick actions

| User says | Run | Then |
|---|---|---|
| "analyze my wallet 0x…", "how am I doing", "rate my trading" | `desk.py 0x…` | relay the full desk |
| "are my positions protected", "am I at risk" | `desk.py 0x… --section protection` | relay; the AT RISK rows first |
| "where am I leaking money", "what's costing me" | `desk.py 0x… --section leaks` | relay, biggest first, with the rejected rules |
| "am I with or against smart money", "compare me to whales" | `desk.py 0x… --section smart` | relay both tables |
| "does my book fit this market" | `desk.py 0x… --section market` | relay |
| "where's my edge", "what am I good at" | `desk.py 0x… --section edge` | relay; then the closing (rule 9) |
| "what should I fix first" | `desk.py 0x… --section next` | relay the three steps |
| a second address, "review this trader 0x…" | `desk.py 0x…` | relay; frame as public data; copying → `senpi-trader-research` |

`--json` prints the analysis document instead of Markdown (for your own follow-up arithmetic — never
to restate numbers differently). `--fresh` ignores the 10-minute cache. `--days N` changes the window.

## What the desk is (the output contract, in render order)

1. **Header** — short address · window · fills · coins · **YOUR QUANT — LIVE · READ-ONLY** · weekly rank
   on Hyperliquid's leaderboard (`#545 of 45,105 · top 1.2%`) · **archetype** (`Aggressive long-only trend
   rider`) · **verdict** (one sentence: strength — weakness. imperative) · flag chips (`NO STOPS (1/3)`,
   `NEAR LIQUIDATION 3.7%`, `HIGH MARGIN 122%`, `IN DRAWDOWN`, `LIQUIDATED ×1`, `CHASING`, `PAYING FUNDING`).
2. **Quant score /100** and the **six dimensions** with one line each: timing/edge, risk management,
   cost efficiency, sizing/conviction, consistency, market fit. Weights and formulas:
   `references/methodology.md`.
3. **Track record** — net P&L per Hyperliquid's own ledger, return on average equity, realized on
   observed trades, win rate, max drawdown (transfer-adjusted), profit factor, trades, active days —
   and a coverage line when the public API returned less than 90% of the wallet's executed volume.
4. **Where your P&L went** — gross → fees → funding → net, cost share vs the whale median.
5. **Top 3 things your agents found** — each: agent · ~$ / window · title · evidence · counterfactual · fix.
6. **Live positions — protection audit** — account value, margin used, withdrawable, net uPnL; per
   position: side, leverage, notional, uPnL, ROE, funding/day, distance to liquidation, **stop cover**
   (share of the size a resting stop covers), status (`AT RISK` / `UNPROTECTED` / `PARTLY COVERED` /
   `PROTECTED`) and what your quant would do.
7. **Performance** — per-coin table, long/short split, hold time winners vs losers, execution
   (taker share, fee rates, liquidations), size-vs-outcome bands.
8. **Leaks** — ranked by $ impact, each counterfactual; then the rules **tested and rejected**.
9. **You vs smart money** — per open position: your side, the cohort's bias and headcount, the read
   (`WITH`, `WITH — BUT LATE (+6h)`, `AGAINST SMART MONEY`, `COHORT SPLIT`, `NO COHORT VIEW`); then you vs
   the whale median on holds, costs, win rate, profit factor.
10. **Market fit** — regime headline, funding across your coins, your stance and its daily funding
    cost, BTC trend; per position: trend, funding, open interest, fit.
11. **Where your edge actually is** — best setups (coin × side, hold bucket, entries before vs after the
    move) with wins/n and profit factor; the catalog families it maps to.
12. **What your quant would do next** — protect first · fix the biggest leak · keep the agents on.
13. Footer: _Analysis of public on-chain data. Not financial advice._

## Reading the sources (what to say when asked "where does this come from")

- **Public, any wallet:** fills and TWAP slices (`userFillsByTime`, `userTwapSliceFillsByTime`), funding
  payments, the wallet's own fee schedule and daily volume, the equity and P&L series, transfers, the
  live book and resting orders, hourly candles, Hyperliquid's leaderboard. No auth.
- **Coverage:** Hyperliquid keeps only the most recent TWAP slices, so a TWAP-heavy wallet's older
  executions are not returned. The desk measures the gap from the position jumps between consecutive
  fills (`startPosition` is the position before each fill) and prints the share of executed volume it
  could see; ledger figures (net P&L, equity, funding) are complete regardless.
- **With a Senpi token:** closed positions come from Senpi discovery (the complete stream, with
  leverage per trade) and the smart-money cohort from Senpi's ALL_TIME realized-PnL ranking (≥ $1M
  realized). Without one, the cohort is the largest profitable accounts on the public leaderboard, and the
  read carries no entry timing.
- **Whale median:** `references/benchmark.json`, computed by `scripts/benchmark.py` — from Senpi discovery
  with a token (whales are TWAP-heavy, so the public endpoints cannot rebuild their round trips). The table
  renders only when the benchmark holds ≥ 5 members with ≥ 10 trades; until that file ships, the smart-money
  tab shows the per-position cohort reads alone.

## Mandatory closing (verbatim structure, after any full desk or `--section edge/next`)

1. **Protect first** — name the AT RISK / UNPROTECTED positions; a stop ladder is a signature on
   positions they already hold, not a deposit.
2. **Fix the biggest leak** — the top leak's title and its counterfactual $; the one-line fix.
3. **Keep the agents on** — "say *hire my quant* and senpi runs this desk on your book — risk guard,
   smart money, market regime, leak finder — and can code your best setup into a strategy you approve,
   deployed as **your** strategy" → `senpi-strategy-discover` (the template that matches the edge) or
   `senpi-strategy-author` (from scratch), then `senpi-strategy-ops`.

## Resilience

The engine fails open: every optional layer (rank, cohort, candles, Senpi) degrades to a line under
`Notes:`; the trade-level analysis needs only the public fills. An address with no perp activity in the
window and no open positions returns an error document — say "nothing to read here yet" and offer the
new-trader path (`senpi-strategy-discover`). A malformed address returns exit 2 with the reason.
Public-API rate limits (HTTP 429) are retried with backoff; a second run inside 10 minutes is served from
the cache (`--fresh` to refetch).

## Install — the whole `scripts/` directory is required

`desk.py` imports `hl_api.py`, `roundtrips.py`, `metrics.py`, `timing.py`, `market.py`, `smart_money.py`,
`senpi_history.py`, `score.py`, `render.py` and the vendored `mcp_client.py` (used only when
`SENPI_AUTH_TOKEN` is set). Stdlib only, Python ≥ 3.9. Fixture-driven tests in `tests/`.

## Skill attribution

This skill creates no strategy wallet and carries no attribution; strategies it hands off are attributed
by the skill that deploys them (`senpi-strategy-ops`).
