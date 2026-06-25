# Scope: `market-pulse` skill — "What's happening in the markets today?"

**Owner:** Jason · **Audience:** Senpi skills/runtime team · **Status:** Draft for review · **Date:** 2026-06-23

---

## 1. The problem

"What's happening in the markets today?" is one of the most common things a user asks their agent — and today the agent answers it badly. Without a skill, it:

- **Wanders.** It fires tool calls ad hoc, in no fixed order, and the answer depends on which call it happened to reach for first.
- **Leaves out whole markets.** It answered crypto-only and skipped XYZ equities entirely until the user pushed back — twice.
- **Leads with the wrong thing.** It opened on BTC instead of an overall market read, so the user had to say "start with the entire market, then drill down."
- **Breaks on infra hiccups** (Hyperfeed down, XYZ candles 500ing, needing `dex="xyz"` explicitly) and gives up on data instead of routing around it.
- **Stops at "BTC is down 3%."** It states *what* moved, not *why it's structured the way it is* — which is the only part a human couldn't get themselves from a price screen.

It eventually produced an excellent answer (see Appendix A) — but only after ~5 user prompts, a memory correction, and several dead-ended tool calls. **The job of this skill is to make that gold-standard answer the first answer, every time, in one shot.**

## 2. Goal & non-goals

**Goal:** A single skill that turns "what's happening in the markets today?" into thoughtful, structured, *non-obvious* cross-asset analysis — the kind of read a human can't assemble on their own — with a deterministic data-gathering plan and graceful degradation when data sources fail.

**The bar:** "BTC is up" is a failure. The answer must explain the **structure** of the move (what's dispersing from what, and what that implies), not just enumerate prices.

**Non-goals:**
- Not a trade recommender (it *offers* to build/check strategies at the end — it doesn't place trades).
- Not a backtester or historical research tool.
- Not real-time streaming — it's a point-in-time snapshot on demand.

## 3. Why a skill (not freeform tool use)

There are dozens of valid tool calls and a hundred ways to order them. Left freeform, the agent gets lost navigating them and the output is non-deterministic. A skill lets us:

- **Fix the call plan** (what to pull, in what order, what to parallelize).
- **Fix the output contract** (overview → drill-down → bottom line → CTAs).
- **Encode the resilience rules** (the fallbacks below) once, so every agent inherits them.
- **Encode the analysis framework** — the dispersion/cross-asset reasoning that makes the answer non-obvious — instead of hoping the model rediscovers it each time.

## 4. Output contract (what the user sees)

Fixed structure, **top-down**. Never lead with a single asset.

1. **The Macro Picture** — one paragraph naming the *character* of the day (risk-off rotation vs. broad panic vs. melt-up vs. chop) and the single key tell that proves it.
2. **Global Indices** — SP500, XYZ100, Nikkei/JP225, KR200, NIFTY, VIX. With a one-line "read" per row, not just a number.
3. **Sector epicenter** — wherever the action actually is (today: semis/memory). The drill-down with the gradient (e.g. memory −10%, logic −3%).
4. **The divergence** — what's NOT moving with the crowd (today: software mega-caps green). This is usually the most insightful section.
5. **Commodities & macro** — gold, silver, copper, oil, DXY, key FX. Used as *confirmation signals*, not just quotes (see framework).
6. **Crypto** — BTC/ETH/majors + funding + volume character (flush vs. drift).
7. **Other notables** — outliers, biggest single movers, liquidity standouts.
8. **Bottom line** — the one-paragraph thesis + an explicit **"What to watch"** list of levels/triggers.
9. **Mandatory closing CTAs** (Section 8).

Formatting: tables with a "read/vibe" column, Δ% throughout, sparing emoji as severity markers. Always show the daily move, not just the price.

## 5. The analysis framework (this is the differentiator)

The skill must instruct the agent to reason cross-asset, not list prices. The non-obvious read comes from *relationships*:

- **Dispersion vs. capitulation.** Is the index calm while components break (rotation), or is everything down together (liquidity event)? The tell: SP500 −1% while individual names −10% = dispersion.
- **The gradient within a sector.** "Memory −10%, equipment −6%, logic −3%" tells a supply/demand story; a flat −5% across all of them tells a liquidity story. Name the gradient.
- **The cross-asset confirmation checklist** (these turn data into insight):
  - **Gold** holding while equities dump → no forced-liquidation cascade (orderly). Gold dumping too → liquidity event.
  - **DXY** flat → no flight-to-USD / funding stress. DXY spiking → macro funding crisis.
  - **VIX** level as the broadening gauge — elevated-but-not-spiking = contained rotation; 25+ = selloff broadening.
  - **Crypto funding flip negative + volume spike** → leverage washout (often near-term exhaustion), vs. orderly drift.
- **K-shaped framing** where it applies — asset-light winners vs. asset-heavy losers, on the same tape.
- **Volume as conviction** — flag notional that's institutional-scale ("MU $417M daily notional") so the user knows what's real repositioning vs. noise.

The output should always answer the implicit question: **"why is the market shaped this way, and what would change the read?"**

## 6. Data sources & call plan

### 6.1 Senpi MCP — primary

| Tool | Role in the skill |
|------|-------------------|
| `market_get_prices` | Live prices across the full asset set (crypto + XYZ + indices + commodities + FX). Backbone call. |
| `market_list_instruments` | **`prevDayPx` for daily-move math** — and the resilience fallback when candles are down (compute Δ% from `prevDayPx` vs. live). |
| `market_get_asset_data` | Per-asset depth (candles/volume/momentum) for the headline movers. **Must pass `dex="xyz"` explicitly for XYZ names.** |
| `market_get_funding_regime` | Funding context for the crypto leverage-washout read. |
| `market_get_cross_asset_flows` | BTC-move → laggard-alt detection when BTC moves >2% (only fire when warmed up; filter follow_rate). |
| `leaderboard_get_top` | "Who's hot" — positioning context (smart-money tilt). |
| `leaderboard_get_markets` | **Cohort concentration by asset** — "X% of top-trader gains are on HYPE shorts." High-signal when available. |
| `leaderboard_get_momentum_events` | Live entry/scale/exit flow — is the smart money *adding* or *unwinding*. |
| `leaderboard_get_status` | Health-check the Hyperfeed layer **before** relying on the leaderboard calls. |

### 6.2 External data (gaps the MCP doesn't cover well)

Decisions (see §10 build plan for phasing):
- **Headline/catalyst context [v1]** — *why* today (an earnings miss, a CPI print, a memory-glut headline). **This is the single highest-leverage element for the "humans couldn't find this" bar** — without the cause, the skill is a pretty price table. One web-search call, timestamped, clearly walled off from price data, framed as "reported catalyst" (context, not truth).
- **Economic calendar [v1]** — is today a scheduled-event day (FOMC/CPI/NFP)? Cheap, high-value context; fold into the same catalyst step.
- **Traditional index/VIX/FX/commodity prints [v2]** — authoritative SP500, VIX, DXY, JP225/KR200/NIFTY, gold/oil. XYZ proxies are close enough for the *structural* read (dispersion/K-shape/confirmation all work on proxies), so primary feeds are a v2 accuracy upgrade, not a v1 blocker. **Do not gate v1 on this integration.**

### 6.3 Streamlined orchestration

The skill should **batch and parallelize**, not serialize:

1. **Health check + bulk pull (parallel):** `leaderboard_get_status`, `market_get_prices` (full set), `market_list_instruments` (for prevDayPx). One round.
2. **Branch on health:** if Hyperfeed is up → add `leaderboard_get_markets` + `_momentum_events` + `_top` for the smart-money layer. If down → skip cleanly, note it, proceed on market data alone.
3. **Drill the movers (parallel):** `market_get_asset_data` on the top N movers only (cap it — don't pull all 100 XYZ names), `market_get_funding_regime`, `market_get_cross_asset_flows` if BTC move qualifies.
4. **(Optional) external:** one web-search for catalyst + economic-calendar check.
5. **Synthesize** into the Section 4 contract.

Goal: **two data rounds, fully parallel within each**, instead of the ~15 sequential calls the agent made unguided.

## 7. Resilience rules (encode these — they're the observed failure modes)

- **Hyperfeed / leaderboard down** → don't stall. Fall back to `market_*` calls, note "smart-money layer unavailable," deliver the rest.
- **XYZ candles 500ing** → fall back to `prevDayPx` from `market_list_instruments` to compute daily moves. Never drop XYZ because candles failed.
- **`dex="xyz"` required** → always pass it explicitly for XYZ equities on `market_get_asset_data`; don't let a default-dex miss return empty.
- **Always refresh** at the start of an answer — never serve session-cached prices as "current." (Pairs with the verify-positions-first discipline.)
- **Coverage is mandatory, not optional** (next section).

## 8. Mandatory rules

- **Always include all asset classes:** crypto **and** XYZ equities **and** indices **and** commodities/macro. Never crypto-only. (This was a memory correction the agent had to be given twice — bake it into the skill so it's structural, not learned.)
- **Always lead top-down:** macro overview first, then drill down. Never open on a single coin.
- **Always end with the two CTAs**, verbatim, as the closing block:

  > **1. Want me to check how our strategies and positions are positioned in this?**
  > **2. Want me to create a new strategy catered to this market?**

  CTA 1 → hand to the positions/strategy read (`strategy_list` → `strategy_get_clearinghouse_state` + `discovery_get_trader_history` per wallet). CTA 2 → hand to the strategy-author/discover flow, pre-seeded with the market thesis just generated (e.g. "semi-led risk-off, software resilient" → a long-software / short-memory or a divergence-fade brief).

## 9. Success criteria

- **One-shot:** produces the Appendix-A-quality answer on the first prompt, with zero follow-up corrections for coverage or ordering.
- **Complete:** every answer spans all asset classes + the two CTAs.
- **Non-obvious:** every answer contains at least one cross-asset structural insight (a dispersion call, a confirmation-signal read, a divergence) — not just a price list.
- **Resilient:** degrades gracefully on Hyperfeed-down and XYZ-candle-500 without losing asset classes.
- **Fast:** ≤ 2 parallel data rounds in the common path.

## 10. Build plan (v1 / v1.1 / v2)

**The connective idea across all phases: the skill's thesis is the asset.** Use it to explain *why* (catalyst), to make it *theirs* (personalization), and to seed the *next action* (CTA-2 brief).

### v1 — the core, shippable on Senpi MCP + one external call

- Full top-down output contract (§4) + analysis framework (§5) + resilience rules (§7) + mandatory coverage & both CTAs (§8).
- 2-round parallel orchestration (§6.3).
- **Catalyst web-search + economic-calendar check** — one call, timestamped, walled off from price data. *This is in v1, not deferred* — it's what clears the "non-obvious" bar today.
- **CTA 2 auto-generates a proposed strategy brief from the thesis** — never opens the author skill cold. Pass a structured intent brief (e.g. *"semi-led risk-off, memory −10%/logic −3%, software green, gold/DXY calm = orderly rotation → candidate: long asset-light software / short memory, or fade if washout; risk: timing"*). **It proposes; the user approves before anything is built** — keep the human in the loop on money-moving actions.

### v1.1 — personalization overlay (ship here if it adds cost to v1; pull into v1 if cheap)

- **Weight the read to the user's live book — as a final-pass lens, not a data filter.** Always pull the full market (the whole-board scan is the point); then overlay "here's how this hits your positions" and surface held assets first. Gated on "user has live positions" via a light `strategy_list` + open-positions read; if flat, skip silently (no empty portfolio section). This makes CTA 1 feel earned rather than bolted on.

### v2 — primary data + depth

- Authoritative macro/VIX/FX/foreign-index feeds replacing/augmenting XYZ proxies.
- Richer catalyst sourcing; per-sector narrative memory across days ("yesterday's rotation continued / reversed").

### Resolved implementation params (were open questions)

- **Mover cap:** deep-pull the **top ~8–10 movers by |Δ%| × notional** with `market_get_asset_data`; don't pull all 100 XYZ names.
- **Caching window:** **always re-pull prices** on every ask (never serve session-cached quotes as "current"); **reuse the catalyst/calendar context** within the session.

---

## Appendix A — gold-standard reference output

The target quality bar is the "Market Overview — Tuesday June 23, 2026" answer the agent eventually produced: top-down (macro → indices → semi epicenter → software divergence → commodities → crypto → notables → bottom line + what-to-watch), with the semiconductor-rotation thesis, the gold/DXY/VIX confirmation reads, and the K-shaped framing. That output — reached after 5 prompts and a memory fix — is what this skill must produce in **one**. (Full text on file with Jason.)
