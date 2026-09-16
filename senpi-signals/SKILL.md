---
name: senpi-signals
description: >
  Surface non-obvious market developments on Hyperliquid — the read you can't get from a price
  screen. One script (scripts/sweep.py) runs the same scanner families used across the 100+ Senpi
  strategy templates (funding, open-interest, smart-money, whale/position, cross-asset, momentum) in
  READ-ONLY OBSERVE mode over the whole HL universe, detects what just *moved* against a rolling
  baseline, and ranks it through TWO lenses at once — a **trade** lens (actionable edge + price
  confirmation, for users building ideas) and a **social** lens (surprising, non-obvious, FRESH, for a
  team automation that continuously generates HL market-news content). One sweep → two ranked feeds.
  Use for "what's noteworthy in the market right now", "any interesting anomalies to tweet", "signals
  of the day", "build me some trade ideas", OR a focused ask — "anything notable on OIL / the AI
  basket / trader 0x1234?". Its flagship detector tracks the PROVEN COHORT'S OWN POSITIONING SHIFTING
  OVER ~12h — "43% of the top 1,000 traders now hold HYPE shorts, up from 38% 12h ago" — which needs
  kept history nobody else has: deploy the `strategies/signals` package and the runtime keeps it
  warm at zero model cost. Read-only, observation not advice, every number sourced. Reuses
  senpi-smart-money's cohort engine; ranks/dedupes via scripts/score.py; feeds senpi-social-cards +
  senpi-trend-reactor. Requires a Senpi MCP token.
license: Apache-2.0
metadata:
  author: Senpi
  version: "2.0.0"
  platform: senpi
  exchange: hyperliquid
---

# Senpi Signals — the read you can't get from a price screen

The same scanners that **fire trades** inside the 100+ templates run here in **observation mode over
the whole Hyperliquid universe**, to surface the *non-obvious* developments — the ones that need
open-interest, funding, smart-money, or whale data to see at all — then rank them by how surprising
and credible they are, and frame the best into a post. Senpi sees the market through its agents'
eyes, so it spots what a chart can't, and says it first.

## Operating model (2.0 — the sweep is a script and a scanner, not an agent cron)

**The whole gather is one process.** `scripts/sweep.py` reads the universe, the proven cohort's books,
the 4h board and the event feeds, writes `current.json`, and hands it to `scripts/score.py` in-process.
The agent never assembles `current.json` from tool calls, and never runs the gather as a cron.

| Mode | How | Cost |
|---|---|---|
| **Ad hoc** ("what's moving under the surface?") | one `exec`: `python3 scripts/sweep.py --print-feed` — its output is the feed to present, and nothing else | ~8 MCP reads, no model tokens spent on gathering |
| **Continuous** (Senpi's content automation: one team box, never a user's account) | deploy **`strategies/signals`** once on the team account (`senpi-strategy-ops` `deploy.py create signals --budget 10`). The runtime runs the same sweep every 45 min on its own clock. The automation **reads** `signals.md`; it never gathers | ~8 MCP reads per sweep, **zero model cost** — the runtime ticks without a model call |

The 1.x model — an agent cron firing every ~45 min that did the gather as tool calls — cost a full
model call per firing and, on a real box, timed out on every firing. **senpi-smart-money's own rule
applies here verbatim: "Asked to run this on a schedule? Say the cost first."** An `openclaw cron` is
an agent turn; the sweep on a cron is 32 model calls a day that produce nothing the runtime scanner
does not produce for free. If a user asks for the sweep on a schedule, say a cron's cost first and
give them the on-demand run. **The package below is never deployed or offered on a user's account:**
it is Senpi's content-automation host, and on a user's account it would park $10 plus a wallet fee to
keep a feed they already get on demand.

`strategies/signals` **trades nothing**: its scanner runs `sweep.run(ctx.senpi_mcp.call_tool)` and
returns `[]` every tick; `slots: 0`; the $10 wallet is the runtime's formality. It exists to run the
sweep on the runtime's clock and keep the state ring warm on the box it runs on (the ring is that box's
state file, so it does not warm anyone else's ad-hoc runs).

## ⚠️ The circularity trap (the single easiest way to get this wrong)

**The 4h gain leaderboard cannot tell you who saw a move coming.** If SPCX falls, every wallet
short SPCX is mechanically at the top of the 4h board. The board is a *consequence* of the move,
not evidence anyone predicted it — so "smart money is short SPCX" read off 4h gains is just the
price move handed back to you. That is why `leaderboard_4h` is discounted and must never be
called smart money.

**A notional lean carries the same bug one level down.** `net_bias` = net/gross **notional**. If a
name falls 10% and *nobody trades at all*, every short's notional grows and every long's shrinks,
so the lean drifts toward the winning side on price alone.

**Two measures are immune, because price cannot change them — and the sweep uses both:**
- **headcount** (`smart_share_kind: "cohort_pct"`) — a wallet either holds the position or it doesn't
- **base units** (`smart_positions`) — coins/contracts opened or added, which is a *decision*

`sm_flow` is built on the second and is the strongest read the skill has.

## The core idea (read this or the rest won't make sense)
**Almost every noteworthy development is a *change*** — "OI **up** 10%", smart money "**just
shifted** short", a whale "**increased by** $10M". You cannot detect a change without remembering the
prior state. So Signals is a **stateful diff engine**: each sweep snapshots the universe's metrics
to a state ring and diffs against the last snapshots. "Here's the current market" is boring and anyone
has it; "here's what just *moved* in a way nobody's watching" is the product.

## Two lenses, two audiences (why every signal gets two scores)
The same detected signals serve two jobs, so `score.py` scores each one **twice** and returns **two
ranked feeds** from one sweep (`--lens both`, the default):
- **`trade_score` — for users building ideas.** Rewards an actionable *edge*: a clear side, price
  *confirming* it (smart-money short **while price is falling** > the same with price rising), a fresh
  *change* over a static level, and enough liquidity to act on. A static funding extreme is *carry,
  not a directional edge* — it scores **low** here. Not freshness-gated: a standing edge is still an edge.
- **`social_score` — for the team's market-news automation.** Rewards *surprise*: non-obvious, a good
  story (a divergence, a named whale), credible — and **fresh**, so a continuous sweep never re-posts
  the same six. A wild funding level *is* content even if it's not tradeable, so the social bar is
  lower and thin markets are included-but-flagged, not dropped.

Change beats state, credibility is a *multiplier* (a thin book can't out-shout a deep one), and no more
than ~2 signals per detector family reach either feed. Give a **user** the trade feed; give the
**content automation** the social feed.

## Golden rules (never violate)
1. **Number integrity.** Every figure you post came from a live MCP/on-chain read **this sweep**, and
   you can name the call it came from (`current.json` carries the source per field, `coverage` per
   detector). Never estimate, round misleadingly, or state a number you can't back.
2. **Observation, not advice.** Describe *what the data shows* — never "buy / sell / long this", no
   price targets, **no returns or outcome language.** It's a market observation, not a call.
3. **Read-only.** This skill never opens/closes a position, never changes a strategy, never trades,
   and never implies Senpi is taking the trade. It reports. (`strategies/signals` never emits a signal.)
4. **Public data only.** On-chain wallet addresses are public — frame as "a top trader (0x12…)".
   Never attach a real person's identity.
5. **Cadence discipline.** The ranker enforces this on the social lens: it diffs against a snapshot
   ~1h old and de-weights any asset+detector surfaced in the last ~45 min, so the continuous sweep
   rotates instead of repeating. Trust it — if the social feed comes back short or empty, nothing new
   happened; **don't lower the bar to fill a quota.** (The trade lens is deliberately *not* rotated.)
6. **Derive the universe, don't hardcode.** The sweep pulls it from `market_list_instruments` (a
   liquidity floor + top-N by volume). Identity baskets (e.g. "the AI names") are the only allowed
   hardcode, and only in focus mode.
7. **Always state direction.** Every signal names the side — **LONG or SHORT** — and for OI /
   conviction, the *flow* (building / unwinding, piling in / exiting). If you can't resolve the side,
   say "side unresolved" — never omit it.
8. **Anchor *and define* every reference — assume the reader knows nothing about HL.** Never a bare
   "N traders", "the leaderboard", "4h window", or "% of top-trader PnL"; gloss each the first time
   (full glossary in `references/detectors.md`):
   - **who the proven traders are** — "the ~150 most-profitable wallets on Hyperliquid by lifetime
     realized PnL (≥ $1M) — the *proven money*"; the sample size is `cohort_n` in `current.json`.
   - **express headcounts as a % of the cohort** — "**44% of the proven traders are short CASHCAT**",
     not "44 traders". Keep this headcount-% **distinct** from the *PnL-concentration* % (`hot_4h_share`).
   - **⚠️ ALWAYS pair that % with the POSITIONED SPLIT.** "44% are short" silently invites "so 56% are
     on the other side" — but most of that 56% hold **no position in the name at all**. Only
     **`N short vs N long`** among the positioned answers the directional question: 43%-short is a
     **rout** at 429-vs-40 and **noise** at 429-vs-380. The sweep supplies both counts; `score.py`
     prints the one-sidedness and "SMALL SAMPLE" below ~10 positioned — **repeat that in any copy.**
   - **quantify in dollars when you can** — the cohort's real summed net notional is
     `smart_net_usd` in `current.json`; **never estimate a $ number; omit it if you can't cite one.**
   - **which board + window** — `leaderboard_get_markets` = Hyperliquid's **live 4-hour rolling**
     board (`hot_4h_share`); `discovery_*` = historical track record (the proven cohort). Say which.
9. **Weight signals by their actual size — lead with what's robust.** A thin signal (few traders, low
   % concentration, tiny market) is *color*, never the headline. **Never upgrade "leans short (1.23%)"
   into "smart money is short."** See `references/worked-examples.md` (WLFI).
10. **The bar: a signal is a *change* that's *fresh*, *sized*, and *legible* — else cut it, don't pad.**
    A big *holding* is not news; only a recent open/add/flip qualifies (`score.py` drops a bare
    holding). Prefer what moved this sweep; state *when*. Never a bare ticker — one clause on what the
    asset is, and price context on positioning signals. Better six that clear the bar than a padded ten.
11. **Smart-money *direction* comes from the proven cohort — not the leaderboard.** The sweep declares
    `smart_source: proven_cohort` (the ≥$1M-lifetime-realized cohort's **headcount**) and carries the
    4h board's number alongside as `hot_4h_share` — *what's winning right now*, momentum /
    survivorship-biased. **Never call the board number "smart money is long/short"** — label it "what's
    hot in the last 4h." When the two disagree, **that gap is itself the story** — report both,
    correctly labeled. Treat 4h / momentum reads as *color (fwiw)*, never a standalone headline.

## Output conventions
- **A "How to read this" block prints at the TOP of every run — including a quiet one.** `score.py`
  emits it — keep it. A reader should not have to ask what 88.7 means.
- **The engine titles the sweep `🔭 Senpi Signals — <YYYY-MM-DD HH:MM> UTC`** and renders **two badged
  sections** — *Tradeable dislocations* (trade feed) and *Market news* (social feed). Present the feed
  that fits the audience; don't relabel it.
- **Present `score.py`'s rendered block** (`signals.md`) as canonical — narrate *around* it, don't
  replace it with free prose. Badges (🔥 ≥ 80 · 🟠 65–79 · 🟡 45–64; ⭐ top; ⚑ named wallet), header,
  ranking and diff-state are the engine's job.
- **For a user, `--print-feed` has already checked coverage.** A source that could not be read is
  named in the feed's last line (`Not measured this run: …`); repeat that line as it stands and add
  nothing. Don't open `current.json` or `state.json` to answer a user. A detector that was never fed
  must never be reported as one that looked and found nothing. The content automation and debugging
  read `current.json` → `coverage` (per source `ok` / `failed: …` / `NO DATA`) and the run JSON's
  `smart_money_lens` / `flow_lens` / `whale_lens` (whether those detectors were fed at all). Whale
  moves are one proven wallet opening, adding to or flipping a side by ≥ $1M since the previous sweep
  (`score.py` diffs the cohort's books), so a first run has none.
- **Talk about the market, never about the engine.** Present `signals.md`, then at most a few
  sentences on what stands out. Never narrate the state ring, baselines or their age, warm-up,
  `trend_ready`, coverage lines, read counts, detector names, or why a detector is quiet — that is
  debugging output, not an answer. If a lens could not be read, one plain clause is the most a user
  hears ("the smart-money read failed this run"). That includes closing notes and caveats. Wrong:
  *"Note: this is a first run on a cold state ring, so the flagship detector isn't active yet; whale
  moves are baseline-only this time."* Every part of that is engine state, and the feed it closed
  already carried two whale moves.
- **Name a divergence or a whale move only when the feed carries one.** No whale move in the feed is
  not a section and not an explanation — leave it out, or one plain clause if the user asked about whales.
  When the feed carries one, keep who it is: the line names the shortened wallet and its lifetime gains.
- **Funding is a percent of position size, never of margin.** `-494%/yr` means shorts pay longs about
  494% of the position's size a year at the current rate. Never restate it as a multiple of margin.

## Running the sweep
```bash
python3 scripts/sweep.py --print-feed        # what you run for a user: prints only the feed (+ one line if a source failed)
python3 scripts/sweep.py                     # debugging: run JSON, coverage lines, reads=<n>; SENPI_AUTH_TOKEN + SENPI_MCP_URL from env (like senpi-smart-money)
python3 scripts/sweep.py --consumer social   # the content feed's anti-repeat namespace (the package does this)
python3 scripts/sweep.py --snapshot-only     # warm the ring, rank nothing; prints trend_ready
#   --out-dir DIR  --state PATH  --top-n 120  --top 6  --lens both|trade|social  --now <ISO>
```
- **Outputs** land beside the state file. On a claw that is **`/data/.openclaw/senpi-state/signals/`**, the
  runtime's state dir on the persistent volume: the exec shell carries no `SENPI_STATE_DIR` and its
  `~/.openclaw` is not the volume, so the sweep looks for that directory itself (`$SENPI_STATE_DIR/signals/`
  wins when set; `~/.openclaw/senpi-state/signals/` only off a claw):
  `current.json` (the gathered metrics + events + `coverage`), `signals.md` (the two badged feeds),
  `state.json` (the snapshot ring + per-consumer freshness). Inside `strategies/signals` the scanner child
  has no env, so it derives the same root from its launch config and writes to `<runtime state root>/signals/`
  — the same directory. stdout is the run JSON
  (`trade`, `social`, `coverage`, `trend_ready`), and the last line is **`reads=<n>`** — the read budget.
- **The read budget per sweep:** 1 `market_list_instruments` (universe, OI, price, 24h move, funding,
  volume — both dexes) · 1 `discovery_get_top_traders` (one page of 1000 → the ≥$1M cohort, 150 sampled)
  · 3 `discovery_get_trader_state` (50 wallets each) · 1 `leaderboard_get_markets` · 1
  `leaderboard_get_momentum_events` · 1 `market_get_cross_asset_flows` = **~8 reads**, no model
  tokens. Every read fails soft: a dead service degrades its detector and is named in `coverage`.
- **Auth.** `discovery_*` needs a **user-scoped** token (an app-scoped one returns nothing → the cohort
  lens reports `NO DATA`, and the sweep degrades honestly into OI/funding/price). Inside the runtime
  the scanner uses the strategy's own token through `ctx.senpi_mcp`.
- **Dependencies.** The sweep imports senpi-smart-money's cohort engine (`build_cohorts` +
  `cohort_bias`, unchanged) and its vendored `mcp_client.py`, resolved the way the runtime resolves
  skills (`SENPI_SKILLS_DIR`, default `/data/.openclaw/skills`, or up the tree from a checkout).
- **What the sweep declares per asset** (so you can read the output honestly): `smart_source:
  proven_cohort`, `smart_share_kind: cohort_pct` (headcount share of the sampled cohort on
  `smart_dir`), `smart_long_n`/`smart_short_n`/`cohort_n`, `smart_positions` (`{wallet: signed BASE
  size}` → `sm_flow`), `smart_net_bias`/`smart_net_usd` (the engine's notional lean, as colour),
  `crowd_dir` + `crowd_source` (`board_4h` when the name is on the 4h board, else `funding_sign`),
  `hot_4h_share`/`hot_4h_dir`, `price_change_pct` (4h, from the board — absent when the name is off
  it), `price_change_pct_24h`, `funding_annualized_pct` + `funding_pctile` (cross-sectional rank
  inside this universe), `oi` (base units), `notional_vol`, `dex`. The full detector library and the
  scoring rubric: [`references/detectors.md`](references/detectors.md).
- **Focus mode** ("anything notable on OIL / the AI basket / 0x1234?") reads the same `current.json`
  scoped to the ask, or pulls the named asset / wallet directly (`market_get_asset_data`,
  `leaderboard_get_trader_positions` for a wallet's 4h deltas). Skip the freshness rotation; a quiet
  "nothing notable" is a correct answer.

## Continuous operation (the social automation)
**Deploy `strategies/signals`.** → [`references/scheduling.md`](references/scheduling.md) for the
cold-start timeline and the one rule that bites (every snapshot carries the full metric set — the
sweep guarantees it). The package runs `sweep.run(...)` every 45 minutes (matched to the ~45-min
freshness window so the social feed rotates), writes `signals.md`, and emits no trade signal. The
content automation then **reads** `signals.md` from that same directory on its own cadence — it never
gathers, and it never needs a model call to have fresh signals waiting.

*Operator detail — for whoever runs the package, never part of an answer to a user.*
**The strongest reason to keep it running is `sm_positioning_build`** — the flagship detector, the
*proven cohort's own positioning shifting over ~12h*. It needs a **warm ring** (a 12h-old snapshot to
diff against), so on a cold state file it stays silent. Until then, expect standing-state signals only
(`funding_extreme`, `sm_divergence`); `oi_surge`/`funding_flip`/`sm_conviction` wake after ~1h,
`sm_positioning_build`/`sm_flow` after ~12h. **An empty feed means nothing new happened — never
backfill to hit a quota.**

## Follow-up: "how could I play it?" (opt-in, consent-gated)
The **trade feed** already gives the actionable *read* per pick (side + whether price confirms it).
After the signals, **offer** — don't push — to show how the user could position for one: *align with
the smart-money side · fade the crowd on a divergence · harvest the funding · follow the whale.* On a
yes, compose **senpi-trader-research / senpi-trade / senpi-strategy-author** to propose a concrete,
**simulated** setup (with a stop), acting only on the user's confirmation. **Keep this out of any
public/tweet copy** — public output stays observation-only; "play it" is a private interactive step.

## Where it lives
Internal to start (a content/intelligence tool). **Focus mode is the seed of a user-facing feature**
— "ask your Senpi agent what's moving under the surface on gold" — graduate it once the detectors are
tuned.

## Checklist before shipping a signal
- [ ] Every number traces to this sweep's `current.json` (name the source field / call) — incl. `price_change_pct` when you claim a side.
- [ ] `coverage` checked: a dark lens is reported as *not measured*, never as *nothing happening*.
- [ ] Right feed for the audience: **trade** for a user building ideas, **social** for content.
- [ ] It's genuinely non-obvious (needs OI/funding/smart-money/whale — not a price move anyone sees).
- [ ] A whale item is a **move** (open/add/flip), never a bare holding.
- [ ] Observation only — no advice, no target, no returns language (holds for the trade read too).
- [ ] Liquid enough for the use (trade feed excludes thin books; social flags them, never hides them).
- [ ] Trusted the freshness rotation — didn't lower the bar to fill a quota; wallet framed as a public 0x… only.
