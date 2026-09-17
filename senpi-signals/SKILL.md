---
name: senpi-signals
description: >
  Surface non-obvious market developments on Hyperliquid — the read you can't get from a price
  screen. One on-demand script (scripts/sweep.py) reads the whole HL universe once — funding, open
  interest, the proven cohort's positioning against the 4h crowd, the platform's momentum events and
  cross-asset flows — and ranks what that single reading shows through TWO lenses: a **trade** lens
  (actionable edge, for users building ideas) and a **news** lens (surprising, non-obvious, for
  market-news content). 2.0 is one reading with no compare: it keeps no history, so moves measured
  against an earlier sweep (whale moves, OI and funding changes, positioning trends) come in v2. Use
  for "scan Senpi Signals", "scan for market anomalies", "what's noteworthy in the market right now",
  "any interesting anomalies to tweet", "signals of the day", "build me some trade ideas", or a
  focused ask — "anything notable on OIL / the AI basket / trader 0x1234?". Every run ends with one
  question: set up a trade on a read, or a strategy that trades reads like these. On demand only: if
  asked to put it on a cron or any schedule, say no. The sweep is read-only, observation not advice,
  every number sourced. Carries a verbatim copy of senpi-smart-money's cohort engine; ranks via
  scripts/score.py. Requires a Senpi MCP token.
license: Apache-2.0
metadata:
  author: Senpi
  version: "2.1.0"
  platform: senpi
  exchange: hyperliquid
---

# Senpi Signals — the read you can't get from a price screen

The same scanner families that **fire trades** inside the 100+ templates run here in **observation
mode over the whole Hyperliquid universe**, to surface the *non-obvious* — what needs funding,
open-interest, smart-money or event data to see at all — then rank it by how credible and surprising
it is, and frame the best of it. Senpi sees the market through its agents' eyes, so it spots what a
chart can't.

## How it runs (2.0 — one reading, on demand)

When a user asks, run **one `exec`** and present its output:

```bash
python3 scripts/sweep.py --print-feed
```

- **One reading, no compare.** The sweep reads the market now (about 8 MCP reads, no model tokens
  spent on gathering) and ranks what that reading shows. It keeps no history: nothing earlier is read
  and nothing is saved for next time, so two runs a minute apart give the same feed.
- **The whole gather is one process.** `scripts/sweep.py` reads the universe, the proven cohort's
  books, the 4h board and the event feeds, writes `current.json`, and hands it to `scripts/score.py`
  in-process. Never assemble `current.json` from tool calls.
- **On demand only: say no to a cron.** If a user asks to put signals on a cron, a timer or any
  schedule ("send me signals every hour", "check the market every morning"), say no. Each firing
  would be a full model call spent re-reading a market they can ask about in one message, and the 1.x
  cron timed out on every firing. Offer to run it whenever they ask. Never create an `openclaw cron`
  or any other scheduled agent turn for it, and never deploy a strategy or fund a wallet to run it.

## ⚠️ The circularity trap (the single easiest way to get this wrong)

**The 4h gain leaderboard cannot tell you who saw a move coming.** If SPCX falls, every wallet
short SPCX is mechanically at the top of the 4h board. The board is a *consequence* of the move,
not evidence anyone predicted it — so "smart money is short SPCX" read off 4h gains is just the
price move handed back to you. That is why `leaderboard_4h` is discounted and must never be
called smart money.

**A notional lean carries the same bug one level down.** `net_bias` = net/gross **notional**. If a
name falls 10% and *nobody trades at all*, every short's notional grows and every long's shrinks,
so the lean drifts toward the winning side on price alone.

**Headcount is immune, because price cannot change it, and the sweep uses it for direction**
(`smart_share_kind: "cohort_pct"`): a wallet either holds the position or it doesn't. Base-unit flow
is the other immune measure; it needs an earlier reading, so it arrives with v2.

## What one reading can show (and what it can't)

A single reading shows **where things stand**, not what just moved:
- **Smart money vs the crowd** (`sm_divergence`): the proven cohort's side against the 4h board's side.
- **Funding at an extreme** (`funding_extreme`): carry, ranked across this universe.
- **The platform's momentum events** (`momentum_event`) and **cross-asset laggards**
  (`cross_asset_laggard`): events the MCP itself reports, each with its own time.

It **cannot** show a change measured against an earlier sweep: whale moves (a proven wallet opening,
adding or flipping $1M+), OI surges, funding flips, sharp conviction jumps, the cohort's 12h
positioning trend or base-unit flow. Those are v2 (below). Never describe the feed as if it carried them.

## Two lenses, two audiences (why every signal gets two scores)
The same signals serve two jobs, so `score.py` scores each one **twice** and returns **two ranked
feeds** from one sweep (`--lens both`, the default):
- **`trade_score` — for users building ideas.** Rewards an actionable *edge*: a clear side, price
  *confirming* it (smart-money short **while price is falling** > the same with price rising), and
  enough liquidity to act on. A funding extreme is *carry, not a directional edge*, so it scores **low** here.
- **`social_score` — for market-news content.** Rewards *surprise*: non-obvious, a good story (a
  divergence, a named wallet in a momentum event), credible. A wild funding level *is* content even
  if it's not tradeable, so the news bar is lower and thin markets are included-but-flagged, not dropped.

Credibility is a *multiplier* (a thin book can't out-shout a deep one), and no more than ~2 signals per
detector family reach either feed. Give a **user** the trade feed; content uses the news feed.

## Golden rules (never violate)
1. **Number integrity.** Every figure you post came from a live MCP/on-chain read **this run**, and
   you can name the call it came from (`current.json` carries the source per field, `coverage` per
   source). Never estimate, round misleadingly, or state a number you can't back.
2. **Observation, not advice.** Describe *what the data shows* — never "buy / sell / long this", no
   price targets, **no returns or outcome language.** It's a market observation, not a call.
3. **The sweep is read-only.** Running it never opens or closes a position, never changes a strategy,
   never trades, and never implies Senpi is taking the trade. It reports. Acting on a read happens only
   in the closing step (below), on the user's explicit yes to a specific order or strategy.
4. **Public data only.** On-chain wallet addresses are public — frame as "a top trader (0x12…)".
   Never attach a real person's identity.
5. **Now, never "since".** One reading has nothing earlier to compare against, so never claim a move
   since an earlier time from this feed: no "just shifted", "up 10% since this morning", "a whale
   added". Say where things stand. The only time words allowed are the sources' own windows: the 4h
   board, the 24h price move, a momentum event's time.
6. **Derive the universe, don't hardcode.** The sweep pulls it from `market_list_instruments` (a
   liquidity floor + top-N by volume). Identity baskets (e.g. "the AI names") are the only allowed
   hardcode, and only in focus mode.
7. **Always state direction.** Every signal names the side — **LONG or SHORT**. If you can't resolve
   the side, say "side unresolved" — never omit it.
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
10. **The bar: sized and legible, or cut it.** A standing state clears the bar only when it is sized
    (enough positioned traders, a real funding extreme, a liquid book) and you can say it plainly.
    Never a bare ticker — one clause on what the asset is, and price context on positioning signals.
    Better six that clear the bar than a padded ten.
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
  sections** — *Tradeable dislocations* (trade feed) and *Market news* (news feed). Present the feed
  that fits the audience; don't relabel it.
- **Present `score.py`'s rendered block** (`signals.md`) as canonical — narrate *around* it, don't
  replace it with free prose. Badges (🔥 ≥ 80 · 🟠 65–79 · 🟡 under 65; ⭐ top; ⚑ named wallet), header
  and ranking are the engine's job.
- **For a user, `--print-feed` has already checked coverage.** A source that could not be read is
  named in the feed's last line (`Not measured this run: …`); repeat that line as it stands and add
  nothing. Don't open `current.json` to answer a user. A detector that was never fed must never be
  reported as one that looked and found nothing. Debugging reads `current.json` → `coverage` (per
  source `ok` / `failed: …` / `NO DATA`) and the run JSON's `smart_money_lens`.
- **Talk about the market, never about the engine.** Present `signals.md`, then at most a few
  sentences on what stands out, then the closing question (next section) — nothing after it. Never
  narrate coverage lines, read counts, detector names, why a detector is quiet, or what 2.0 leaves
  out — that is debugging output, not an answer. If a source could not be read, one plain clause is
  the most a user hears ("the smart-money read failed this run"). That includes closing notes and
  caveats. Wrong: *"Note: this run has no history, so whale moves and 12-hour trends aren't
  included."* The feed is what the market shows now; don't explain what it leaves out.
- **Name a divergence only when the feed carries one.** Never add a section for something the feed
  doesn't have.
- **Funding is a percent of position size, never of margin.** `-494%/yr` means shorts pay longs about
  494% of the position's size a year at the current rate. Never restate it as a multiple of margin.

## Running the sweep
```bash
python3 scripts/sweep.py --print-feed   # what you run for a user: prints only the feed (+ one line if a source failed)
python3 scripts/sweep.py --brief 3      # the short version senpi-market-pulse closes with: top 3 trade reads, one line each
python3 scripts/sweep.py                # debugging: run JSON, coverage lines, reads=<n>; SENPI_AUTH_TOKEN + SENPI_MCP_URL from env (like senpi-smart-money)
#   --out-dir DIR  --top-n 120  --top 6  --lens both|trade|social  --now <ISO>
```
- **Outputs:** `current.json` (the gathered metrics + events + `coverage`) and `signals.md` (the two
  badged feeds) in `--out-dir` (default `$SENPI_STATE_DIR/signals/`, else
  `~/.openclaw/senpi-state/signals/`), overwritten each run. Nothing else is written. stdout is the
  run JSON (`trade`, `social`, `coverage`), and the last line is **`reads=<n>`** — the read budget.
- **The read budget per sweep:** 1 `market_list_instruments` (universe, OI, price, 24h move, funding,
  volume — both dexes) · 1 `discovery_get_top_traders` (one page of 1000 → the ≥$1M cohort, 150 sampled)
  · 3 `discovery_get_trader_state` (50 wallets each) · 1 `leaderboard_get_markets` · 1
  `leaderboard_get_momentum_events` · 1 `market_get_cross_asset_flows` = **~8 reads**, no model
  tokens. Every read fails soft: a dead service degrades its detector and is named in `coverage`.
- **Auth.** `discovery_*` needs a **user-scoped** token (an app-scoped one returns nothing → the cohort
  lens reports `NO DATA`, and the sweep degrades honestly into OI/funding/price).
- **Dependencies.** The sweep carries verbatim copies of senpi-smart-money's cohort engine
  (`scripts/smartmoney.py`: `build_cohorts` + `cohort_bias`, unchanged) and its stdlib MCP transport
  (`scripts/mcp_client.py`), so it runs with only this skill installed.
  `tests/test_vendored_parity.py` fails if either copy drifts from the original.
- **What the sweep declares per asset** (so you can read the output honestly): `smart_source:
  proven_cohort`, `smart_share_kind: cohort_pct` (headcount share of the sampled cohort on
  `smart_dir`), `smart_long_n`/`smart_short_n`/`cohort_n`, `smart_net_bias`/`smart_net_usd` (the
  engine's notional lean, as colour), `crowd_dir` + `crowd_source` (`board_4h` when the name is on
  the 4h board, else `funding_sign`), `hot_4h_share`/`hot_4h_dir`, `price_change_pct` (4h, from the
  board — absent when the name is off it), `price_change_pct_24h`, `funding_annualized_pct` +
  `funding_pctile` (cross-sectional rank inside this universe), `oi` (base units), `notional_vol`,
  `dex`. The full detector library and the scoring rubric: [`references/detectors.md`](references/detectors.md).
- **Focus mode** ("anything notable on OIL / the AI basket / 0x1234?") reads the same `current.json`
  scoped to the ask, or pulls the named asset / wallet directly (`market_get_asset_data`,
  `leaderboard_get_trader_positions` for a wallet's 4h deltas). A quiet "nothing notable" is a
  correct answer.

## v2 — compare over periods (not in 2.0)
v2 adds what needs an earlier reading: whale moves (naming the shortened wallet and its lifetime
gains), OI surges and funding flips, sharp conviction jumps, the proven cohort's 12h positioning trend
and base-unit flow, and anti-repeat rotation for a content feed. The engine for it exists in
`score.py` behind `--state` for development; the skill never passes it. Where that history lives is
v2's decision, kept on Senpi's side rather than on a user's box, so nothing in 2.0 schedules, deploys
or funds anything.

## How every run ends — one question
After the feed and your few sentences, end the turn with **one question**, and nothing after it:

> **Want to act on any of these? I can set up a trade on one of them — you see the size and the stop before anything is placed — or build a strategy around one of these reads.**

- **A trade on one read → senpi-trade.** Take the side the read names. Before any order, show the
  margin and its share of the account, the leverage, the stop price and the liquidation price. The
  stop must sit before liquidation. Place nothing until the user says yes to that exact order. A
  funding extreme names no side (it is a carry read), so it is never a one-trade setup.
- **A strategy around a read → senpi-strategy-author.** Hand over the read as the brief: the asset,
  the side, what the read is and its numbers. Build with the author's guardrails: a DSL stop on every
  position, leverage 3x or less, few trades (fees are the biggest cost of an active strategy), and
  the minimum budget plus the wallet-creation fee stated before anything is funded. Never promise or
  imply results: it is a new strategy with no track record, and you say so. Deploy only on the user's
  yes.
- **No, or no answer → stop.** Don't repeat the offer.
- **Never in public copy.** The question and everything after it are a private, interactive step;
  anything written for posting stays observation-only.
- **The brief carries no question.** `--brief` output is another skill's closing section; that skill
  asks its own question.

## Where it lives
Every user has it. A chat chip runs the full sweep ("Scan Senpi Signals for market anomalies"), and
**senpi-market-pulse** closes every pulse with the brief (`--brief 3`) and an offer to run the full sweep.

## The short version (`--brief N`)
`python3 scripts/sweep.py --brief 3` runs the same sweep and prints only a title and the top N trade
reads, one line each: badge, score, asset and the read. No legend, no news feed, nothing about the
engine, plus the one not-measured line when a source failed. A quiet market prints one plain line.

## Checklist before shipping a signal
- [ ] Every number traces to this run's `current.json` (name the source field / call) — incl. `price_change_pct` when you claim a side.
- [ ] Coverage respected: a dark source is reported as *not measured*, never as *nothing happening*.
- [ ] Right feed for the audience: **trade** for a user building ideas, **news** for content.
- [ ] It's genuinely non-obvious (needs OI/funding/smart-money/event data — not a price move anyone sees).
- [ ] Said as a standing state, never as a move since an earlier time.
- [ ] Observation only — no advice, no target, no returns language (holds for the trade read too).
- [ ] Liquid enough for the use (trade feed excludes thin books; news flags them, never hides them).
- [ ] Didn't lower the bar to fill a quota; wallet framed as a public 0x… only.
