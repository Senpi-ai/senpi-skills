---
name: senpi-signals
description: >
  Surface non-obvious market developments on Hyperliquid — the read you can't get from a price
  screen. Runs the same scanner families used across the 100+ Senpi strategy templates (funding,
  open-interest, smart-money, whale/position, cross-asset, regime, momentum) in READ-ONLY OBSERVE
  mode over the whole HL universe, detects what just *moved* against a rolling baseline, and ranks it
  through TWO lenses at once — a **trade** lens (actionable edge + price confirmation, for users
  building ideas) and a **social** lens (surprising, non-obvious, FRESH, for a team automation that
  continuously generates HL market-news content). One sweep → two ranked feeds. Use for
  "what's noteworthy in the market right now", "any interesting anomalies to tweet", "signals of
  the day", "build me some trade ideas", OR a focused ask — "anything notable on OIL / the AI basket / trader 0x1234?".
  Its flagship detector tracks the PROVEN COHORT'S OWN POSITIONING SHIFTING OVER ~12h — "43% of the
  top 1,000 traders now hold HYPE shorts, up from 38% 12h ago" — change applied to the best data
  available, which needs kept history nobody else has (run it on a schedule to keep that history warm).
  Examples it produces: "smart money just flipped SHORT on SPCX while the crowd is LONG", "OI +10%
  on OIL longs", "0x1234 grew their INTC short by $10M to $50M". Read-only, observation not advice,
  every number sourced. Composes senpi-smart-money, senpi-market-pulse, senpi-trader-research;
  ranks/dedupes via scripts/score.py; feeds senpi-social-cards + senpi-trend-reactor. Requires a
  Senpi MCP token.
license: Apache-2.0
metadata:
  author: Senpi
  version: "1.5.0"
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

## Two lenses, two audiences (why every signal gets two scores)
The same detected signals serve two jobs, so `score.py` scores each one **twice** and returns **two
ranked feeds** from one sweep (`--lens both`, the default):
- **`trade_score` — for users building ideas.** Rewards an actionable *edge*: a clear side, price
  *confirming* it (smart-money short **while price is falling** > the same with price rising), a fresh
  *change* over a static level, and enough liquidity to act on. A static funding extreme is *carry,
  not a directional edge* — it scores **low** here. Not freshness-gated: a standing edge is still an edge.
- **`social_score` — for the team's market-news automation.** Rewards *surprise*: non-obvious, a good
  story (a divergence, a named whale), credible — and **fresh**, so a cron running continuously never
  re-posts the same six (see Continuous operation). A wild funding level *is* content even if it's not
  tradeable, so the social bar is lower and thin markets are included-but-flagged, not dropped.

Change beats state, credibility is a *multiplier* (a thin book can't out-shout a deep one), and no more
than ~2 signals per detector family reach either feed — so you never get four funding items and a $6M
ghost town in a top six. Give a **user** the trade feed; give the **content automation** the social feed.

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
5. **Cadence discipline.** The ranker enforces this for you on the social lens: it diffs against a
   snapshot ~1h old (not just the last run, so a 3-min re-run isn't noise) and de-weights any
   asset+detector surfaced in the last ~45 min, so a continuous cron rotates instead of repeating.
   Trust it — if the social feed comes back short or empty, nothing new happened; **don't lower the
   bar to fill a quota.** (The trade lens is deliberately *not* rotated — a standing setup is still one.)
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
   - **⚠️ ALWAYS pair that % with the POSITIONED SPLIT — the cohort % alone is misleading in BOTH
     directions.** "44% are short" silently invites "so 56% are on the other side" — but most of that
     56% usually hold **no position in the name at all**. The directional question is only answered by
     the split *among traders who are actually positioned*: **`N short vs N long`**. The same 43%-short
     reading is a **rout** at 429-short vs 40-long (~91% one-sided) and **noise** at 429 vs 380. Never
     characterise a lean without both numbers — and if you can't get the long count, **say the split is
     unknown** rather than implying one. Two truths to carry at once: a large cohort-% on a *single*
     name out of ~200 instruments is a genuinely rare concentration (don't dismiss it as "just 43%"),
     *and* one-sidedness is what makes it directional (don't assert a side without it).
   - **quantify in dollars when you can** — the aggregate notional the cohort is long/short, e.g.
     "**~$204M in notional GOOGL shorts**", summed from their *actual* positions
     (`leaderboard_get_trader_positions`). Dollars make it tangible. Use it for the signals you
     feature. **Only a real summed figure — never estimate a $ number; omit it if you can't sum one.**
   So a full positioning line reads: *"44% of the proven traders are short CASHCAT — 44 short vs 6
   long among the 50 who hold it (88% one-sided) — ~$Xm notional, and 2.4% of their open PnL."*
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
- **The engine titles the sweep `🔭 Senpi Signals — <YYYY-MM-DD HH:MM> UTC`** and renders **two badged
  sections** — *Tradeable dislocations* (trade feed) and *Market news* (social feed). Present the feed
  that fits the audience; don't relabel it "Signals of the Day."
- **Present `score.py`'s rendered block** (the badged, titled, ranked feeds) as canonical — narrate
  *around* it, don't replace it with free prose. The badges, header, ranking and diff-state are the
  engine's job; keep them.
- **Severity-flag every finding** by score: 🔥 ≥ 80 · 🟠 65–79 · 🟡 45–64; ⭐ the single top; ⚑ a
  named-wallet signal. (`score.py` emits these — keep them.)
- **Always include a smart-money-vs-crowd divergence check and a whale-shifts check** in a sweep. If
  either finds nothing, **say so plainly** — never relabel smart-vs-price as a divergence.

## Follow-up: "how could I play it?" (opt-in, consent-gated)
The **trade feed** already gives the actionable *read* per pick (side + whether price confirms it); this
is the next step — turning a read into a concrete setup. After the signals, **offer** — don't push — to
show how the user could position for one: *align with
the smart-money side · fade the crowd on a divergence · harvest the funding · follow the whale.* On a
yes, compose **senpi-trader-research / senpi-trade / senpi-strategy-author** to propose a concrete,
**simulated** setup (with a stop), acting only on the user's confirmation. **Keep this out of any
public/tweet copy** — public output stays observation-only; "play it" is a private interactive step.

## Two modes
- **Sweep** — *"what's noteworthy / build me ideas / what can we post?"* Run every detector across
  the universe → snapshot + diff → score both lenses → return the two ranked feeds (~6 each). Give a
  user the **trade** feed with a one-line read per pick; give the content automation the **social**
  feed with a tweet draft per pick. The regular engine for both use cases.
- **Focus** — *"anything notable on OIL / the AI basket / 0x1234?"* Same detectors scoped to one
  asset, a named sector/basket, or a wallet. Skip the cadence/freshness rotation; report what's there
  through whichever lens the ask implies (a user gets the trade read), or say plainly that nothing
  stands out — a quiet "nothing notable" is a correct answer.

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
   `scripts/score.py` with the state file. It diffs against the **~1h-old** snapshot in the state
   ring, fires the threshold detectors, scores each signal on **both lenses**, drops noise (illiquid
   below the floor / near-duplicate family angles / repeats), applies the per-family cap, writes the
   new snapshot + freshness memory, and returns **`trade` and `social`** shortlists (badged). **The
   thresholds and the scoring rubric live in that script + `references/detectors.md` — do not re-invent them.**
4. **Frame** — for a **user** (trade feed): lead with the side + whether price is confirming it, using
   the `trade_read` line as the seed — a read, never advice (golden rule 2 still holds; no targets, no
   "buy"). For the **content automation** (social feed): lead with the non-obvious number, cite it, one
   insight per pick under the golden rules. Hand the best social pick to **senpi-social-cards** for a
   graphic, or to **senpi-trend-reactor** (a detected anomaly is a trigger).

## Running the ranker
```bash
# current.json = { "asset_metrics": {<asset>: {...}}, "events": [ {pre-formed signals} ] }
python3 scripts/score.py current.json --top 6 --out signals.md
#   state defaults under $SENPI_STATE_DIR (the claw sets it to /data/.openclaw/senpi-state — the
#     Railway persistent volume, where runtime state lives) as .../signals/state.json; survives chats
#     + redeploys. Fallback ~/.openclaw/senpi-state/signals/state.json. Override with --state <path>.
#   --lens both|trade|social  (default both)
#   --consumer social         → the content cron's OWN anti-repeat memory (still shares the baseline)
#   --now <ISO>               → deterministic runs / backfills
```
- **stdout** is JSON `{ "generated", "diff_baseline_ts", "trade": [...], "social": [...] }`; `--out`
  writes the same two badged feeds as markdown. Each signal carries `trade_score`, `social_score`,
  `credibility`, `freshness`, `detector`, `direction`, `numbers`.
- `asset_metrics` per asset: any of `oi`, `price`, **`price_change_pct`** (4h % — powers the trade
  lens's price-*confirmation* term; supply it whenever you have a direction), `smart_share` (0–100),
  **`smart_long_n` / `smart_short_n`** (the positioned split — see below), `smart_dir`, `crowd_dir`,
  `oi_side`, `funding_pctile` (0–100), `funding_annualized_pct`, `notional_vol`, `dex` (`""` main /
  `"xyz"`), `trader_count`. Missing fields just skip their detectors.
  - **`smart_source` — DECLARE WHERE THE READ CAME FROM. This is not optional.**
    `"proven_cohort"` (senpi-smart-money, ≥$1M lifetime realized — track record) or `"leaderboard_4h"`
    (`leaderboard_get_markets` — momentum, survivorship-biased). The engine **labels the output from
    this field and discounts the score by it** (proven 1.0 · leaderboard 0.7 · **unstated 0.8, and it
    prints "SOURCE UNSTATED — do not call this smart money"**). Omit it and every line the skill emits
    carries that warning — by design: *the engine will not assert a provenance you did not give it.*
  - **Be consistent: the trend detector only diffs like against like.** The ~12h baseline is matched
    per asset *and per source*, so **switching `smart_source` restarts that asset's trend history**
    (silently, by design — the alternative is a fake trend manufactured by the switch). If you intend
    to move to `proven_cohort`, do it **now**, before a schedule lays down hours of history you can't
    diff against. Carry the leaderboard number alongside in `hot_4h_share` if you still want it.
  - **`smart_share` is the share on `smart_dir` from whichever source you declared** — the cohort %
    for `proven_cohort`, the leaderboard share for `leaderboard_4h`. If you carry the leaderboard
    number alongside a proven-cohort read, put it in `hot_4h_share` as labelled colour. Mixing them is
    how you get "smart money is short X" from a number that only says "shorts are winning right now."
  - **Always supply `smart_long_n` / `smart_short_n` when you can.** The cohort % alone can't tell a
    rout from noise — 43%-short is 429-vs-40 (~91% one-sided, real) or 429-vs-380 (~53%, noise). The
    un-positioned rest of the cohort is **not** the other side. score.py uses the split for magnitude
    and prints the one-sidedness; without it, it prints "split unknown" — which you must repeat, not
    paper over.
- `events`: pre-formed signals the diff engine can't derive. A **`whale_move` must be a MOVE, not a
  holding** — pass `change_usd` (or `pnl_swing_usd` / `opened` / `flipped`); a bare holding with no
  change is dropped. Each event: `asset`, `detector`, `notional_vol`, `numbers`, optional `direction` /
  `price_change_pct` / `concrete_entity`. See `references/detectors.md` for the schema.
- **State is the whole engine, and it must be DURABLE.** It defaults under **`$SENPI_STATE_DIR`** — the
  same base dir the Senpi runtime uses — as `…/signals/state.json`. In the hyperclaw container the claw
  exports `SENPI_STATE_DIR=/data/.openclaw/senpi-state`, the **Railway persistent volume at `/data`**, so
  state survives **across chats and redeploys**, beside `installed_runtimes.json` — no config needed.
  (Fallback: `~/.openclaw/senpi-state/signals/state.json`.) On a per-chat scratchpad or `/tmp` the memory
  resets every chat and *every run silently looks like a first run*. First run has no baseline — expected.
- **One baseline, separate anti-repeat.** The snapshot ring is *shared* across consumers; `--consumer
  <name>` namespaces only the freshness memory. Run the content cron as `--consumer social`, users as
  the default `adhoc`: a user's ad-hoc browse diffs against the cron's warm ~1h baseline, but the cron's
  "already-posted" never blanks the user's feed, and vice-versa. Writes are file-locked.

## Continuous operation (the social automation)
The content use case is **runtime-shaped**: don't wait for someone to open a chat. Run the sweep on a
**schedule** (an agent cron / scheduled job) with `--consumer social` against the durable state — that
job keeps a warm ~1h baseline populated for *everyone*, including users' on-demand runs.
**→ Full setup, cadence, and the cold-start timeline: [`references/scheduling.md`](references/scheduling.md).**
`--snapshot-only` records a reading into the ring without ranking, emitting, or spending freshness —
the cheap history-warmer, and it prints `trend_ready` so you can check whether the 12h detector is
armed. ⚠️ **Any job that writes to the ring must gather the FULL metric set** — a partial snapshot can
become the ~1h baseline and silently starve `oi_surge` / `funding_flip`. Match the cron
cadence to the freshness window (~45 min): a name you just posted stays suppressed ~that long, so the
feed rotates. **An empty feed means nothing new happened — never backfill to hit a quota.**

**The strongest reason to schedule it is `sm_positioning_build`** — the flagship detector, which reads
the *proven cohort's own positioning shifting over ~12h* ("43% of the top 1,000 now hold HYPE shorts,
up from 38% 12h ago"). That is change applied to the best data we have, and **nobody else keeps the
history to see it** — a price chart can't, the 4h leaderboard can't, and a single run can't. It needs a
**warm ring** (a 12h-old snapshot to diff against), so on a cold state file it stays silent. A running
schedule is what turns it on. Until then, expect standing-state signals only.

## What the two scores weigh (the moat)
Per `references/detectors.md` — **social** = non-obvious (needs OI/funding/smart-money data; heaviest)
· magnitude · conflict (a divergence) · change · a named wallet, all **× credibility × freshness**.
**trade** = directional edge (per detector; a static funding extreme is low) · price confirmation ·
change · conflict · magnitude, **× credibility** (no freshness gate). Credibility is a *multiplier*
(full at ~$25M vol, discounted toward a $1M drop floor), so a thin book can't out-shout a deep one.
Anti-noise: drop below the $1M floor, collapse near-duplicate family angles, cap ~2 per family, and
skip the obvious ("BTC up 2%").

## Where it lives
Internal to start (a content/intelligence tool). **Focus mode is the seed of a user-facing feature**
— "ask your Senpi agent what's moving under the surface on gold" — graduate it once the detectors are
tuned.

## Checklist before shipping a signal
- [ ] Every number traces to a live call this run (name it) — incl. `price_change_pct` when you claim a side.
- [ ] Right feed for the audience: **trade** for a user building ideas, **social** for content.
- [ ] It's genuinely non-obvious (needs OI/funding/smart-money/whale — not a price move anyone sees).
- [ ] A whale item is a **move** (change/open/flip), never a bare holding.
- [ ] Observation only — no advice, no target, no returns language (holds for the trade read too).
- [ ] Liquid enough for the use (trade feed excludes thin books; social flags them, never hides them).
- [ ] Trusted the freshness rotation — didn't lower the bar to fill a quota; wallet framed as a public 0x… only.
