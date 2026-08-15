---
name: senpi-signals
description: >
  Surface non-obvious market developments on Hyperliquid — the read you can't get from a price
  screen. Runs the same scanner families used across the 100+ Senpi strategy templates (funding,
  open-interest, smart-money, whale/position, cross-asset, regime, momentum) in READ-ONLY OBSERVE
  mode over the whole HL universe, detects what just *moved* against a rolling baseline, ranks by
  how surprising/credible it is, and frames the top picks into tweet-ready insights. Use for
  "what's noteworthy in the market right now", "any interesting anomalies to tweet", "signals of
  the day", OR a focused ask — "anything notable on OIL / the AI basket / trader 0x1234?".
  Examples it produces: "smart money just flipped SHORT on SPCX while the crowd is LONG", "OI +10%
  on OIL longs", "0x1234 grew their INTC short by $10M to $50M". Read-only, observation not advice,
  every number sourced. Composes senpi-smart-money, senpi-market-pulse, senpi-trader-research;
  ranks/dedupes via scripts/score.py; feeds senpi-social-cards + senpi-trend-reactor. Requires a
  Senpi MCP token.
license: Apache-2.0
metadata:
  author: Senpi
  version: "1.0.0"
  platform: senpi
  exchange: hyperliquid
---

# Senpi Signals — the read you can't get from a price screen

The same scanners that **fire trades** inside the 100+ templates run here in **observation mode over
the whole Hyperliquid universe**, to surface the *non-obvious* developments — the ones that need
open-interest, funding, smart-money, or whale data to see at all — then rank them by how surprising
and credible they are, and frame the best into a post. Senpi sees the market through its agents'
eyes, so it spots what a chart can't, and says it first.

## The core idea (read this or the rest won't make sense)
**Almost every noteworthy development is a *change*** — "OI **up** 10%", smart money "**just
shifted** short", a whale "**increased by** $10M". You cannot detect a change without remembering the
prior state. So Signals is a **stateful diff engine**: each run it snapshots the universe's metrics
to a state file and diffs against the last snapshot. "Here's the current market" is boring and anyone
has it; "here's what just *moved* in a way nobody's watching" is the product.

## Golden rules (never violate)
1. **Number integrity.** Every figure you post came from a live MCP/on-chain read **this run**, and
   you can name the call it came from. Never estimate, round misleadingly, or state a number you
   can't back. Fewer real numbers beat more invented ones.
2. **Observation, not advice.** Describe *what the data shows* — never "buy / sell / long this", no
   price targets, **no returns or outcome language.** It's a market observation, not a call.
3. **Read-only.** This skill never opens/closes a position, never changes a strategy, never trades,
   and never implies Senpi is taking the trade. It reports.
4. **Public data only.** On-chain wallet addresses are public — frame as "a top trader (0x12…)".
   Never attach a real person's identity.
5. **Cadence discipline.** Dedupe hard — never post five versions of one PUMP move — and rotate
   detectors/assets across runs so the feed stays fresh.
6. **Derive the universe, don't hardcode.** Pull it from `market_list_instruments` (a liquidity
   floor + top-N by volume). Identity baskets (e.g. "the AI names") are the only allowed hardcode,
   and only in focus mode.
7. **Always state direction.** Every signal names the side — **LONG or SHORT** — and for OI /
   conviction, the *flow* (building / unwinding, piling in / exiting). A "surge" or "conviction jump"
   with no direction is useless. If you can't resolve the side, say "side unresolved" and pull the OI
   long/short split — never omit it.
8. **Anchor *and define* every reference — assume the reader knows nothing about HL.** Never a bare
   "N traders", "the leaderboard", "4h window", or "% of top-trader PnL"; each is meaningless without
   a plain-English gloss the first time you use it (full glossary in `references/detectors.md`):
   - **who the top traders are** — "the top ~C traders on Hyperliquid by realized PnL — the *proven
     money*"; C = `source_trader_count` from `leaderboard_get_markets`. Not "everyone."
   - **express headcounts as a % of the cohort** — "**44% of the proven traders are short CASHCAT**",
     not "44 traders" (% = `trader_count / source_trader_count`). A % anchors the weight; a raw count
     reads small. Keep this headcount-% **distinct** from the *PnL-concentration* % below.
   - **quantify in dollars when you can** — the aggregate notional the cohort is long/short, e.g.
     "**~$204M in notional GOOGL shorts**", summed from their *actual* positions
     (`leaderboard_get_trader_positions`). Dollars make it tangible. Use it for the signals you
     feature. **Only a real summed figure — never estimate a $ number; omit it if you can't sum one.**
   So a full positioning line reads: *"44% of the proven traders are short CASHCAT — ~$Xm notional —
   and 2.4% of their open PnL."*
   - **which leaderboard + window** — `leaderboard_get_top` = Hyperliquid's **live 4-hour rolling**
     leaderboard; `discovery_*` = historical track record. Say which, and that "4h" = the last 4 hours.
   - **what the metric means** — "% of top-trader PnL" = of all the open profit that proven cohort is
     sitting on right now, this share is in this one bet. **Translate the metric; don't quote it raw.**
9. **Weight signals by their actual size — lead with what's robust.** A thin signal (few traders, low
   % concentration, tiny market) is *color*, never the headline. Lead with the facts a sharp reader
   can independently check (a price move + volume, OI building/draining), and use small positioning
   as corroboration. **Never upgrade "leans short (1.23%)" into "smart money is short."** Overstating
   a small signal is how the account loses credibility. See `references/worked-examples.md` (WLFI).

11. **The bar: a signal is a *change* that's *fresh*, *sized*, and *legible* — else cut it, don't pad.**
    "Overall not that interesting" is the failure mode to design against. Four filters, applied hard:
    - **Change, not state.** A big *holding* is not news — a whale sitting on $78M HYPE from a
      months-old $38.68 entry is holdings, not a move. Only a **recent open/add/flip or a sudden P&L
      swing** qualifies (rule → `whale_move`); the gain from *good timing* is the story, not the size.
    - **Fresh.** Prefer what moved *this run / this 4h*; a condition that's been true for weeks is
      wallpaper. State *when*.
    - **Legible.** Never a bare ticker — one clause on **what the asset is** ("ACE, a low-cap gaming
      alt") and, on positioning signals, **price context** (recent move + where it sits). A reader who
      doesn't know the asset can't judge it, so it isn't a signal to them.
    - **Don't pad to a count.** Better six that clear the bar than a padded ten. If the market's quiet,
      say "quiet right now — here are the 2 that stand out." Consolidate repeats (three majors all
      "smart-lean short" → one grouped line, not three thin ones).

12. **Smart-money *direction* comes from the proven cohort — not the leaderboard.** For "what is
    smart money doing" and any divergence, use **senpi-smart-money**: the **≥$1M-lifetime-realized**
    cohort's **net positioning** (bias, members, net $, crowd side — it already computes all of it).
    `leaderboard_get_markets` `pct_of_top_traders_gain` is a *different* thing — the share of the
    **live-4h** leaderboard's open profit in a name, i.e. *what's winning right now*, which is
    **momentum / survivorship-biased** (for a rising asset the winners are simply the ones long it).
    **Never call the leaderboard number "smart money is long/short"** — label it "what's hot in the
    last 4h." When the two disagree (proven cohort net-short HYPE while the 4h winners ride HYPE
    longs), **that gap is itself the story** — report both, correctly labeled. And **don't whistle in
    the wind on every 4h move**: treat 4h / momentum reads as supplementary *color (fwiw)*, never a
    standalone headline. Require **persistence** (the signal held across reads) or a **durable
    corroborator** (proven-cohort positioning, funding, real OI build, a sized whale) before you
    feature it. Durable + structural leads; transient 4h momentum is color.

## Output conventions
- **Title the sweep `Live Signals — <YYYY-MM-DD HH:MM> UTC`** (not "Signals of the Day").
- **Present `score.py`'s rendered block** (the badged, titled, deduped list) as the canonical signal
  list — narrate *around* it, don't replace it with free prose. The badges, header, ranking and
  diff-state are the engine's job; keep them.
- **Severity-flag every finding** by score: 🔥 ≥ 80 · 🟠 65–79 · 🟡 45–64; ⭐ the single top; ⚑ a
  named-wallet signal. (`score.py` emits these — keep them.)
- **Always include a smart-money-vs-crowd divergence check and a whale-shifts check** in a sweep. If
  either finds nothing, **say so plainly** — never relabel smart-vs-price as a divergence.

## Follow-up: "how could I play it?" (opt-in, consent-gated)
After the signals, **offer** — don't push — to show how the user could position for one: *align with
the smart-money side · fade the crowd on a divergence · harvest the funding · follow the whale.* On a
yes, compose **senpi-trader-research / senpi-trade / senpi-strategy-author** to propose a concrete,
**simulated** setup (with a stop), acting only on the user's confirmation. **Keep this out of any
public/tweet copy** — public output stays observation-only; "play it" is a private interactive step.

## Two modes
- **Sweep** — *"what's noteworthy right now?"* Run every detector across the universe → snapshot +
  diff → score → dedupe → return the top ~6 developments, each with its numbers, sourcing, and a
  tweet draft. The regular content engine.
- **Focus** — *"anything notable on OIL / the AI basket / 0x1234?"* Same detectors scoped to one
  asset, a named sector/basket, or a wallet. Skip the cadence ranking; report what's there (or say
  plainly that nothing stands out — a quiet "nothing notable" is a correct answer).

## The pipeline
1. **Universe** — `market_list_instruments`; keep names above a volume floor (top-N by liquidity).
   (Focus mode: just the named asset / basket / wallet.)
2. **Gather signals** per the recipes in [`references/detectors.md`](references/detectors.md) — call
   the detector's MCP tool(s), read the exact fields listed. **Inspect before you trust a shape:**
   if a tool's response shape isn't already confirmed in `references/detectors.md`, call it once and
   read the real response before extracting (a KeyError is better than a silent zero). Normalize each
   finding to the signal schema in `references/detectors.md`.
   - Reuse the engines we already have rather than re-deriving: **senpi-smart-money** (cohort +
     divergence), **senpi-market-pulse** (funding / movers / cross-asset), **senpi-trader-research**
     (trader reads). Only the **OI-tracker** and the **whale-watch** deltas are new here.
3. **Rank + diff** — pass the current per-asset metrics (and any pre-formed events) to
   `scripts/score.py` with the state file. It computes the deltas vs the last snapshot, fires the
   threshold detectors, scores noteworthiness, drops noise (illiquid / stale / obvious), dedupes per
   asset, ranks, writes the new snapshot, and returns the shortlist. **The thresholds and the
   scoring rubric live in that script + `references/detectors.md` — do not re-invent them per run.**
4. **Frame** — turn each top pick into one insight in Senpi voice under the golden rules (lead with
   the non-obvious number; cite it). Hand the best to **senpi-social-cards** for a graphic, or to
   **senpi-trend-reactor** (a detected anomaly is a trigger).

## Running the ranker
```bash
# current.json = { "asset_metrics": {<asset>: {...}}, "events": [ {pre-formed signals} ] }
python3 scripts/score.py current.json --state <tmp>/senpi-signals-state.json --top 6 --out signals.md
```
- `asset_metrics` per asset: any of `oi`, `price`, `smart_share` (0–100), `smart_dir`, `crowd_dir`,
  `funding_pctile` (0–100), `funding_annualized_pct`, `notional_vol`, `trader_count`. Missing fields
  just skip the detectors that need them.
- `events`: pre-formed signals the diff engine can't derive (whale moves, momentum events,
  cross-asset laggards) — each with `asset`, `detector`, `notional_vol`, `numbers`, optional
  `direction` / `concrete_entity`. See `references/detectors.md` for the schema.
- The state file is the **diff memory** — same path across runs. First run has no baseline, so
  change-detectors stay quiet; that's expected.

## What "noteworthy" means (the moat)
`score.py` weights, per `references/detectors.md`: **non-obvious** (needs OI/funding/smart-money/
whale data — invisible on a chart; heaviest), **statistically unusual** (vs the asset's own
baseline), **conflicting** (divergences), **concrete** (a named proven wallet), **fresh** (last run),
**credible** (proven cohort, real liquidity). Anti-noise: drop illiquid micro-caps, wash/bots, stale
reads, and the obvious ("BTC up 2%").

## Where it lives
Internal to start (a content/intelligence tool). **Focus mode is the seed of a user-facing feature**
— "ask your Senpi agent what's moving under the surface on gold" — graduate it once the detectors are
tuned.

## Checklist before posting a signal
- [ ] Every number traces to a live call this run (name it).
- [ ] It's genuinely non-obvious (needs OI/funding/smart-money/whale — not a price move anyone sees).
- [ ] Observation only — no advice, no target, no returns language.
- [ ] Liquid enough to be real (above the volume floor); not a bot/wash artifact.
- [ ] Deduped against the rest of today's picks; wallet framed as a public 0x… only.
