# Detector library — senpi-signals

Each detector is one of the scanner families the 100+ templates already use, run in **observe mode**.
For every detector: the MCP source, the fields, the threshold, the normalized signal it emits, and
the human framing. **Confirmed** = response shape verified; **VERIFY-LIVE** = call the tool once and
read the real response before writing extraction (KeyError > silent zero).

## The normalized signal schema
Everything downstream (scoring, framing) consumes this one shape:
```json
{
  "asset": "OIL",                     // exact HL instrument (case-sensitive; xyz: prefix for stocks)
  "dex": "" ,                          // "" = main HL, "xyz" = xyz DEX
  "detector": "oi_surge",             // see families below
  "direction": "long",                // "long" | "short" | null
  "numbers": ["OI +10% (1h)", "price flat"],   // the cited facts, each from a real read
  "notional_vol": 4200000,            // liquidity, for the credibility filter
  "concrete_entity": null,            // a public 0x… wallet when the signal is about one trader
  "magnitude": 0.10                   // normalized size the scorer reads (see each detector)
}
```
Diff-based detectors (OI, smart-money, funding) are fired *inside* `score.py` from `asset_metrics` +
the prior snapshot — you supply the metrics, not the signal. Event-based detectors (whale, momentum,
cross-asset) you assemble as pre-formed `events[]`.

---

## Diff-based detectors (fire in score.py from asset_metrics vs prior snapshot)

### 1. `oi_surge` — open-interest build / OI-price divergence  *(oi-tracker family)*
- **Source:** open interest per asset. **VERIFY-LIVE:** HL Info API `metaAndAssetCtxs` →
  `openInterest` per asset (the template scanners in `strategies/*/main/scanners/scan.py` read this;
  copy their extraction). Price from `market_get_prices` / `market_get_asset_data` (candles keyed
  `o/h/l/c/v` as **strings**).
- **Metric fields:** `oi`, `price`.
- **Fires when:** `(oi − prior_oi)/prior_oi ≥ 0.10`. If `|price change| < 1%` at the same time →
  tag it OI-price **divergence** (positioning building with no price move — a conflict bonus).
- **Why non-obvious:** OI is invisible on a price chart. Magnitude = the OI %.
- **Framing:** `OI +10% on <ASSET> <longs|—> while price sat flat.`

### 2. `sm_divergence` — smart money vs the crowd  *(smart-money + divergence families)*
- **Source — the proven cohort, NOT the leaderboard.** Smart-money *direction* = **senpi-smart-money**
  engine (the **≥$1M-lifetime-realized** cohort's **net positioning**: bias, members, **net $**, crowd
  side, divergence flag — it already computes all of this; consume it). **Do NOT derive smart-money
  direction from `leaderboard_get_markets` `pct_of_top_traders_gain`** — that's the *live-4h*
  leaderboard cohort (momentum / survivorship-biased: for a rising asset the "top traders" are just
  whoever's long it). Confusing the two is what makes senpi-signals contradict the pulse read (e.g.
  proven cohort net-**short** HYPE while the 4h winners ride HYPE longs). Crowd side: from
  senpi-smart-money, or the funding-sign proxy (positive ⇒ crowd LONG, negative ⇒ crowd SHORT).
- **Metric fields:** `smart_dir`, `crowd_dir`, `smart_share` (= `pct_of_top_traders_gain`).
- **Fires when:** `smart_dir != crowd_dir` AND `smart_share ≥ 25`. Flip bonus if `prior.smart_dir`
  existed and differs (smart money *just* shifted). Conflict bonus (it's a divergence).
- **crowd_dir (how to get it, in order):** (1) HL OI long/short split per asset (VERIFY-LIVE
  `metaAndAssetCtxs`); (2) **funding sign** as a live proxy — positive funding ⇒ crowd LONG, negative
  ⇒ crowd SHORT. **Divergence = the smart dominant direction *opposite* crowd_dir.** Smart-long-while-
  price-*down* is NOT a divergence (that's smart-vs-price) — don't call it one.
- **Framing:** `Smart money is <SHORT> on <ASSET> — <X>% of the proven cohort, ~$<Nm> notional, <share>% of their open PnL — while the crowd is <LONG>.`

### 3. `sm_conviction` — hot-money crowding (a *momentum* read, fwiw)  *(momentum family)*
- **This is momentum, not positioning.** It measures the **live-4h leaderboard's** PnL concentration
  shifting — "what's winning right now" — NOT the proven cohort's direction (that's #2 /
  senpi-smart-money). Label it as such; don't headline a single 4h wiggle — require persistence
  across reads or a durable corroborator before featuring it.
- **Source:** `leaderboard_get_markets` `pct_of_top_traders_gain` / `trader_count`, diffed vs prior.
- **Metric fields:** `smart_share`, `trader_count`.
- **Fires when:** `|smart_share − prior.smart_share| ≥ 12` points — **both directions**: a jump = top
  traders **piling in**, a drop = top traders **unwinding / exiting** (the BTC-exodus case is a real
  signal, not noise). Always state which flow *and* the side (long/short).
- **Prefer the API's own `contribution_pct_change_4h`** (percentage-point change vs 4h ago) when
  present — it fires on the first run with no state and reconciles with the absolute share. Use the
  state-diff only for longer horizons. (A "+57pp to 56%" that implies a negative prior means the two
  sources got mixed — pick one.)
- **Framing:** `Top traders are <piling into|unwinding> <ASSET> <LONG|SHORT> — concentration <+/−>Npp to <share>%.`

### 4. `funding_dislocation` — funding at an extreme / flipped  *(funding family — the biggest)*
- **Source (partly Confirmed):** `market_get_funding_history` (asset) → 8h rate, annualized %,
  funding_direction (who collects), persistence, trend; `market_get_funding_regime`. (Needs a
  market-scoped token — it 401s on an under-scoped one; then skip this detector, don't fake it.)
- **Metric fields:** `funding_pctile` (0–100), `funding_annualized_pct`, optional prior sign.
- **Fires when:** `funding_pctile ≥ 95` OR the sign flipped vs prior.
- **Framing:** `<Shorts|Longs> are paying <annualized%>/yr to hold <ASSET> — <pctile>th-percentile funding.`

## Event-based detectors (assemble as pre-formed `events[]`)

### 5. `whale_move` — a proven wallet *just moved* (a change, not a holding)  *(whale / position_tracker)*
- **The whole point is the MOVE, not the holding.** A whale *sitting on* a big position — even a
  $78M one — is **not a signal** if it's been held since an old entry and hasn't changed. "0x… holds
  $78M HYPE long" tells a reader nothing they can act on: they may have opened it months ago and just
  ridden it. What's noteworthy is a **recent change** or a **sudden P&L swing** — that's news; a
  static book is not. **Never emit a whale_move for a position that only *exists* and is large.**
- **Fires only on ONE of:**
  1. **A recent size change** — opened / added / trimmed / **flipped** ≥ ~$1M (or ≥ ~25% of the book)
     **this window**. Set `change_usd` (signed Δnotional) and/or `opened: true` / `flipped: true`.
  2. **A sudden P&L swing** — the position moved ≥ ~$1M **in the last 4h** (a winner suddenly bleeding,
     a loser ripping). Set `pnl_swing_usd`. This catches "an old position that's *now* in motion."
  `score.py` **drops any whale_move that carries none of** `change_usd / pnl_swing_usd / opened /
  flipped` — a bare holding never scores. Magnitude comes from the **change**, not the position size.
- **Recency is required in the framing.** Say **when / how fresh**: "opened today", "added in the last
  4h", "flipped from long to short this window". If you can only tell that it's *large* and *old* (the
  entry price sits far from the recent range and nothing changed), it's a holding — **skip it**.
- **Source (VERIFY-LIVE):** whale set = top-trader cohort from `discovery_get_top_traders`. The **4h
  delta needs no state** — `leaderboard_get_trader_positions` returns each trader's **4h position
  delta** and **4h P&L delta** directly; use them so moves fire even on the **first run**. For longer
  horizons, diff each wallet's per-asset notional vs its prior snapshot (store wallet books in state).
  **Always run this in a sweep** — "any whale shifts?" is a headline detector — but report a shift
  only when there genuinely is one; "no notable whale moves" is a correct, honest answer.
- **`concrete_entity`** = the public `0x…`. **Framing (lead with the change + when):**
  `A top trader (0x12…) just grew their <ASSET> SHORT by $10M to $50M (added this 4h window).`
  or `0x12… flipped <ASSET> long→short today — now $30M short.` Include the entry only as context
  for the *change*, never as the headline of a static hold.

### 6. `cross_asset_laggard` — rotation not yet priced in  *(cross-asset family)*
- **Source:** `market_get_cross_asset_flows` (meaningful only when BTC moved >2% in 4h; `follow_rate`).
- **Framing:** `BTC ran <x%> but <ASSET> hasn't followed (follow-rate <r>) — the laggard.`

### 7. `momentum_event` — the platform's own tiered events  *(momentum family)*
- **Source:** `leaderboard_get_momentum_events` (tiers + behavioral tags + notification decisions —
  it already scores "is this worth notifying"). Pass through the high-tier ones as events.

### 8. `regime_shift` — sector/asset regime flip  *(regime family)*  *(v1: optional / focus mode)*
- **Source:** the regime read (bull/bear/range/event-driven) from weekly-daily structure; flag flips.

---

## Framing rules (apply to every signal)
- **Direction is mandatory** — LONG or SHORT, plus the flow for OI/conviction (building/unwinding).
  A surge with no side is useless. Unknown side → say "side unresolved" + pull the OI split.
- **Quantify: % of cohort + $ notional + % of PnL** — never a raw "44 traders". Say
  "**X% of the proven traders are short GOOGL — ~$204M notional — and Y% of their open PnL**".
  X = `trader_count / source_trader_count`; **$** = sum of the cohort's *actual* position notional on
  that asset+side (VERIFY-LIVE via `leaderboard_get_trader_positions`; surface it for featured
  signals; **never estimate — omit if you can't sum a real figure**). Keep the headcount-% distinct
  from the PnL-concentration-% (`pct_of_top_traders_gain`).
- **Name the asset — never a bare ticker.** Assume the reader has never heard of it. One clause on
  *what it is*: "ACE (a low-cap gaming alt)", "ZEC (Zcash, a privacy coin)", "OIL (crude oil, an xyz
  perp)". A ticker + a % with no idea what the thing is can't be judged noteworthy. Well-known majors
  (BTC/ETH/SOL/HYPE) need no gloss. Source the descriptor from `market_list_instruments` metadata or
  common knowledge; if you genuinely don't know what it is, say so rather than posting it blind.
- **Price context on positioning signals.** A divergence or whale move needs price to hang on: the
  recent move (e.g. "−6% today, −18% on the week") and *where price sits* (near range low/high, a
  round level). Positioning without price is half a signal — pull `market_get_asset_data` candles.
- **Define the jargon inline** — never "the leaderboard", "top traders", "4h window", or "% of
  top-trader PnL" without a plain-English gloss the first time (see Glossary below).
- **Weight by size; lead with the robust facts.** Lead with what a sharp reader can independently
  check (price move + volume, OI building/draining); use thin positioning (few traders, low %,
  tiny market) as *corroboration*, never the headline. Never upgrade "leans short (1.23%)" to
  "smart money is short." See `worked-examples.md` (WLFI).
- **Severity flag** — 🔥 ≥ 80 · 🟠 65–79 · 🟡 45–64; ⭐ top; ⚑ named wallet (score.py emits these).

### Glossary (use these plain-English definitions in output)
- **Top traders / the cohort** — the top ~C most-profitable traders on Hyperliquid by realized PnL
  (`source_trader_count` from `leaderboard_get_markets`) — "the proven money," not "everyone."
- **The leaderboard** — `leaderboard_get_top` = Hyperliquid's *live 4-hour rolling* board
  ("Predators"); `discovery_*` = historical track record. Always say **which**.
- **4h window** — the last four hours (the live leaderboard's rolling window).
- **Cohort headcount %** (`trader_count / source_trader_count`) — what share of the proven cohort
  holds this position: "44% of the top traders are short X." Distinct from % of top-trader PnL below.
- **Aggregate notional $** — the dollar size of the cohort's combined position on an asset+side,
  summed from their actual positions (`leaderboard_get_trader_positions`): "~$204M in GOOGL shorts."
  Real summed figure only — never estimated.
- **% of top-trader PnL** (`pct_of_top_traders_gain`) — of all the open profit that cohort is holding
  right now, the share sitting in this one token+side. High = the proven money is concentrated there.
- **Concentration jump/drop** (`contribution_pct_change_4h`) — how much that share moved vs 4h ago.

## Ways to play (the opt-in follow-up — never in public/tweet copy)
Per detector, the thesis the agent can *offer* to build (consent-gated, via senpi-trade /
senpi-trader-research / senpi-strategy-author), always a **simulated** setup with a stop, executed
only on the user's confirmation:
- `sm_divergence` → **align with the smart-money side** (or fade the crowd).
- `sm_conviction` piling-in → **follow the crowding**; unwinding → **de-risk / fade**.
- `whale_move` → **mirror the whale** (senpi-trade mirror) at your budget, with a stop.
- `funding_dislocation` → **harvest the funding** — take the side that *collects*, sized for the carry.
- `oi_surge` / `cross_asset_laggard` → position for the build / the catch-up.
Observation stays public; the play stays private.

## Noteworthiness scoring (in score.py — keep in sync)
`score = 100 × ( 0.35·non_obvious + 0.25·magnitude + 0.20·conflict + 0.10·concrete + 0.10·credibility )`
- **non_obvious** (per detector): `oi_surge, funding_dislocation, sm_divergence, whale_move` = 1.0;
  `sm_conviction, cross_asset_laggard` = 0.8; `momentum_event, regime_shift` = 0.6.
- **magnitude:** normalized 0–1 (OI%/0.25; funding_pctile/100; smart_share/100; whale_$/ $10M; capped 1).
- **conflict:** divergence detectors (`sm_divergence`, OI-price-divergence flavor of `oi_surge`) = 1, else 0.
- **concrete:** `concrete_entity` set (a named wallet) = 1, else 0.3.
- **credibility:** `notional_vol ≥ VOL_FLOOR` → 1, scaled below; **below HARD_VOL_FLOOR → dropped entirely.**

## Anti-noise (drop before ranking)
- Below `HARD_VOL_FLOOR` notional volume (illiquid micro-cap / wash).
- Score below `MIN_SCORE`.
- Duplicate per asset — keep the single highest-scoring signal (a 2nd only if a different detector
  *and* both high).
- The obvious — a plain price move with no OI/funding/smart-money/whale angle is not a signal here.

## Defaults (mirrored in score.py — change in both)
`OI_SURGE_PCT 0.10 · PRICE_FLAT 0.01 · SMART_SHARE_MIN 25 · SMART_JUMP_PP 12 · FUNDING_PCTILE 95 ·
WHALE_MIN_USD 1_000_000 · VOL_FLOOR 1_000_000 · HARD_VOL_FLOOR 250_000 · MIN_SCORE 45 · TOP_N 6`
