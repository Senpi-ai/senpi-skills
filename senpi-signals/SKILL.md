---
name: senpi-signals
description: >
  Surface non-obvious market developments on Hyperliquid — the read you can't get from a price
  screen. One on-demand script (scripts/sweep.py) reads the whole HL universe once — funding, open
  interest, the proven cohort's positioning against the 4h crowd, the platform's momentum events and
  cross-asset flows — and ranks what that single reading shows through TWO lenses: a **trade** lens
  (actionable edge, for users building ideas) and a **news** lens (surprising, non-obvious, for
  market-news content). Use for "scan Senpi Signals", "scan for market anomalies", "find what's
  mispriced", "where is the market wrong", "find dislocations", "what's out of line", "what's
  noteworthy in the market right now", "any interesting anomalies to tweet", "signals of the day",
  "build me some trade ideas", or a focused ask — "anything notable on OIL / the AI basket / trader 0x1234?".
  Every run ends with one question: set up a trade on a read, or a strategy that trades reads like
  these. On demand only: if asked to put it on a cron or any schedule, say no. The sweep is
  read-only, observation not advice, every number sourced. Requires a Senpi MCP token.
license: Apache-2.0
metadata:
  author: Senpi
  version: "2.3.0"
  platform: senpi
  exchange: hyperliquid
---

# Senpi Signals — the read you can't get from a price screen

The same scanner families that **fire trades** inside the 100+ templates run here in **observation
mode over the whole Hyperliquid universe**, to surface the *non-obvious* — what needs funding,
open-interest, smart-money or event data to see at all — then rank it by how credible and surprising
it is. Senpi sees the market through its agents' eyes, so it spots what a chart can't.

## How it runs

When a user asks, run **one `exec`** and present its output:

```bash
python3 scripts/sweep.py --print-feed
```

- **One reading, no compare.** The sweep reads the market now (about 8 MCP reads, no model tokens
  spent on gathering) and ranks what that reading shows. It keeps no history, so two runs a minute
  apart give the same feed.
- **The whole gather is one process.** `scripts/sweep.py` reads the universe, the proven cohort's
  books, the 4h board and the event feeds, and hands them to `scripts/score.py` in-process. Never
  assemble the sweep's inputs from tool calls.
- **On demand only: say no to a cron.** If a user asks to put signals on a cron, a timer or any
  schedule ("send me signals every hour", "check the market every morning"), say no. Each firing
  would be a full model call spent re-reading a market they can ask about in one message. Offer to
  run it whenever they ask. Never create an `openclaw cron` or any other scheduled agent turn for
  it, and never deploy a strategy or fund a wallet to run it.

## ⚠️ The circularity trap (the single easiest way to get this wrong)

**The 4h gain leaderboard cannot tell you who saw a move coming.** If SPCX falls, every wallet
short SPCX is mechanically at the top of the 4h board. The board is a *consequence* of the move,
not evidence anyone predicted it — so "smart money is short SPCX" read off 4h gains is just the
price move handed back to you.

**A notional lean carries the same bug one level down.** `net_bias` = net/gross **notional**. If a
name falls 10% and *nobody trades at all*, every short's notional grows and every long's shrinks,
so the lean drifts toward the winning side on price alone.

**Headcount is immune, because price cannot change it, and the sweep uses it for direction**
(`smart_share_kind: "cohort_pct"`): a wallet either holds the position or it doesn't.

So: **smart-money *direction* comes from the proven cohort — never the leaderboard.** The sweep
declares `smart_source: proven_cohort` (the ≥$1M-lifetime-realized cohort's **headcount**) and
carries the 4h board's number alongside as `hot_4h_share` — *what's winning right now*, momentum /
survivorship-biased. **Never call the board number "smart money is long/short"** — label it "what's
hot in the last 4h", and treat it as *color (fwiw)*, never a standalone headline. When the two
disagree, **that gap is itself the story** — report both, correctly labeled.

## Two lenses, two audiences (why every signal gets two scores)

The same signals serve two jobs, so `score.py` scores each one **twice** and returns **two ranked
feeds** from one sweep (`--lens both`, the default):

- **`trade_score` — for users building ideas.** Rewards an actionable *edge*: a clear side, price
  *confirming* it, and enough liquidity to act on. A funding extreme is *carry, not a directional
  edge*, so it scores **low** here.
- **`social_score` — for market-news content.** Rewards *surprise*: non-obvious, a good story,
  credible. A wild funding level *is* content even if it's not tradeable, so the news bar is lower
  and thin markets are included-but-flagged, not dropped.

Credibility is a *multiplier* (a thin book can't out-shout a deep one), and no more than ~2 signals
per detector family reach either feed. Give a **user** the trade feed; content uses the news feed.

## Golden rules (never violate)

1. **Number integrity.** Every figure you post came from a live MCP/on-chain read **this run**, and
   you can name the call it came from. Never estimate, round misleadingly, or state a number you
   can't back.
2. **Observation, not advice.** Describe *what the data shows* — never "buy / sell / long this", no
   price targets, **no returns or outcome language.** It's a market observation, not a call.
   This is where "**mispriced**" / "**dislocated**" / "**out of line**" land — the words users ask in.
   Here they mean one thing: a **gap between two things the sweep actually read** (the cohort against
   the 4h crowd, funding against positioning, a laggard against its basket), **never a fair-value
   judgment** — the sweep carries no model of what anything is worth. "The cohort is 78% short HYPE
   while the 4h board is long", never "HYPE is overpriced".
3. **The sweep is read-only.** Running it never opens or closes a position, never changes a strategy,
   never trades, and never implies Senpi is taking the trade. It reports. Acting on a read happens only
   in the closing step (below), on the user's explicit yes to a specific order or strategy.
4. **Public data only.** On-chain wallet addresses are public — frame as "a top trader (0x12…)".
   Never attach a real person's identity.
5. **Now, never "since".** The feed is a snapshot of where things stand, with nothing earlier of
   ours to compare against. Never claim a move since an earlier time: no "just shifted", "up 10%
   since this morning", "a whale **added**". A change measured against an earlier reading — whale
   adds and flips, OI surges, funding flips, conviction jumps, positioning trends, base-unit flow —
   is not in this feed. Never describe the feed as if it carried them.

   **The one exception, because it dates itself: a whale OPEN.** A proven wallet's position reports
   its own age, so "$12.4M short, opened 18 minutes ago" is a fact about the position, not a diff
   against a sweep of ours. An **add** needs the old size and a **flip** needs the old side — those
   are still v2. Say *opened*, never *added* or *flipped*, and never date a position the feed did
   not date: an undated whale position is dropped rather than called fresh.

   The only other time words allowed are the sources' own
   windows: the 4h board, the 24h price move, a momentum event's time.
6. **Derive the universe, don't hardcode.** The sweep pulls it from `market_list_instruments` (a
   liquidity floor + top-N by volume). Identity baskets (e.g. "the AI names") are the only allowed
   hardcode, and only in focus mode.
7. **Always state the side.** Every directional signal names **LONG or SHORT**. If you can't resolve
   it, say "side unresolved" — never omit it. A funding extreme is the one signal with no side by
   design: it is a carry read, so say that rather than inventing a direction.
8. **Anchor *and define* every reference — assume the reader knows nothing about HL.** Never a bare
   "N traders", "the leaderboard", or "4h window"; gloss each the first time (full glossary in
   [`references/detectors.md`](references/detectors.md)):
   - **who the proven traders are** — "the ~150 most-profitable wallets on Hyperliquid by lifetime
     realized PnL (≥ $1M) — the *proven money*".
   - **express headcounts as a % of the cohort** — "**44% of the proven traders are short CASHCAT**",
     not "44 traders". Keep this headcount-% **distinct** from the *PnL-concentration* %.
   - **⚠️ ALWAYS pair that % with the POSITIONED SPLIT.** "44% are short" silently invites "so 56% are
     on the other side" — but most of that 56% hold **no position in the name at all**. Only
     **`N short vs N long`** among the positioned answers the directional question: 43%-short is a
     **rout** at 429-vs-40 and **noise** at 429-vs-380. `score.py` prints the split, the
     one-sidedness and "SMALL SAMPLE" below ~10 positioned — **repeat that in any copy.**
   - **which board + window** — `leaderboard_get_markets` = Hyperliquid's **live 4-hour rolling**
     board; `discovery_*` = historical track record (the proven cohort). Say which.
9. **Weight signals by their actual size — lead with what's robust.** A thin signal (few traders, low
   % concentration, tiny market) is *color*, never the headline. **Never upgrade "leans short (1.23%)"
   into "smart money is short."** See [`references/worked-examples.md`](references/worked-examples.md) (WLFI).
10. **The bar: sized and legible, or cut it.** A signal clears the bar only when it is sized (enough
    positioned traders, a real funding extreme, a liquid book) and you can say it plainly. Never a
    bare ticker — one clause on what the asset is, and price context on positioning signals. Better
    six that clear the bar than a padded ten.

## Output conventions

`score.py` renders the feed — the "How to read this" block, the title, the two badged sections
(*Tradeable dislocations* and *Market news*), the badges (🔥 ≥ 80 · 🟠 65–79 · 🟡 under 65; ⭐ top;
⚑ named wallet), the positioned split and the ranking. **Present that block as canonical** and
narrate *around* it; don't replace it with free prose or relabel its sections. The rules above
govern your narration, not the rendered feed.

- **Talk about the market, never about the engine.** Present the feed, then at most a few sentences
  on what stands out, then the closing question (next section) — nothing after it. Never narrate
  coverage lines, read counts, detector names, why a detector is quiet, or what the feed leaves out.
  That includes closing notes and caveats. Wrong: *"Note: this run has no history, so whale moves
  aren't included."*
- **Coverage is honest, not narrated.** A source that could not be read is named in the feed's last
  line (`Not measured this run: …`); repeat that line as it stands and add nothing. A detector that
  was never fed must never be reported as one that looked and found nothing. If a source failed, one
  plain clause is the most a user hears ("the smart-money read failed this run").
- **Name a divergence only when the feed carries one.** Never add a section for something the feed
  doesn't have.
- **Funding is a percent of position size, never of margin.** `-494%/yr` means shorts pay longs about
  494% of the position's size a year at the current rate. Never restate it as a multiple of margin.

## Running the sweep

```bash
python3 scripts/sweep.py --print-feed   # what you run for a user: prints only the feed (+ one line if a source failed)
python3 scripts/sweep.py --brief 3      # the short version senpi-market-pulse closes with: top 3 trade reads, one line each
python3 scripts/sweep.py                # debugging: run JSON, coverage lines, reads=<n>
#   --out-dir DIR  --top-n 120  --top 6  --lens both|trade|social  --now <ISO>
```

- **The read budget per sweep:** 1 `market_list_instruments` · 1 `discovery_get_top_traders` · 3
  `discovery_get_trader_state` · 1 `leaderboard_get_markets` · 1 `leaderboard_get_momentum_events` ·
  1 `market_get_cross_asset_flows` = **~8 reads**, no model tokens. Every read fails soft: a dead
  service degrades its detector and is named in the feed's not-measured line.
- **The time budget per sweep.** The whole run is bounded: **45s** on `--brief` (it closes another
  skill's answer, on that skill's budget) and **100s** on `--print-feed`. A read is not started once
  what is left cannot pay for it, so a slow upstream costs a lens, never the answer — the market
  reads go first, the proven cohort (the slowest, and the only one that can spend the whole budget
  alone) goes last. A lens dropped for time is named in the not-measured line like any other, and its
  `[coverage]` line says it was never started, which is not the same fact as a read that failed.
- **Auth.** `SENPI_AUTH_TOKEN` + `SENPI_MCP_URL` from env. `discovery_*` needs a **user-scoped**
  token — an app-scoped one returns nothing and the cohort lens goes dark. A dark cohort lens is not
  by itself a token problem: the `[coverage] cohort:` line quotes the read that failed, so read it
  before naming a cause.
- **Dependencies.** The sweep carries verbatim copies of senpi-smart-money's cohort engine
  (`scripts/smartmoney.py`) and its stdlib MCP transport (`scripts/mcp_client.py`), so it runs with
  only this skill installed. `tests/test_vendored_parity.py` fails if either copy drifts.
- **Focus mode** ("anything notable on OIL / the AI basket / 0x1234?") scopes the same reading to the
  ask, or pulls the named asset / wallet directly (`market_get_asset_data`,
  `leaderboard_get_trader_positions`). A quiet "nothing notable" is a correct answer.
- The full detector library, the scoring rubric and every field the sweep declares per asset:
  [`references/detectors.md`](references/detectors.md).

## v2 — compare over periods (not in 2.0)

Everything that needs an earlier reading — whale **adds and flips**, OI surges, funding flips,
conviction jumps, the cohort's positioning trend and base-unit flow — is v2. (A whale **open** is
not among them: the position carries its own age, so it ships in 2.3.) Where that history lives is v2's decision,
kept on Senpi's side rather than on a user's box, so nothing here schedules, deploys or funds anything.

## How every run ends — one question

After the feed and your few sentences, end the turn with **one question**, and nothing after it:

> **Want to act on any of these? I can set up a trade on one of them — you see the size and the stop before anything is placed — or start a strategy: your own Athena, the smart-money hedge fund, or one built around these reads.**

- **A trade on one read → senpi-trade.** Take the side the read names. Before any order, show the
  margin and its share of the account, the leverage, the stop price and the liquidation price. The
  stop must sit before liquidation. Place nothing until the user says yes to that exact order. A
  funding extreme names no side, so it is never a one-trade setup.
- **Athena → senpi-strategy-ops.** The lead strategy offer: when the user wants a strategy and hasn't
  picked a read, offer **Athena** (`athena`). Its Phalanx sleeve follows the same proven cohort the
  smart-money reads come from, and its Aegis sleeve reads the tape to hedge the regime. Ops runs the
  walkthrough first, then asks the budget; read the minimum budget from the catalog, never from memory.
- **A strategy around one read → that read's template, or the user's own.** Offer the template built on
  that kind of read as the quick start (senpi-strategy-ops, same walkthrough), with designing one from
  scratch as its peer:

  | The read | Start from |
  |---|---|
  | Smart money vs the crowd | **Athena** (`athena`), with a hedge, or **Phalanx** (`phalanx`), the cohort sleeve alone |
  | A funding extreme | **Pangolin** (`pangolin`), which fades the crowd paying to hold, or **Camel** (`camel`), which collects the carry on two books |
  | A momentum event | **Meerkat** (`meerkat`) |
  | A laggard behind BTC | **Mantis** (`mantis`) |

  From scratch → senpi-strategy-author, with the read as the brief: the asset, the side, what the read
  is and its numbers. Build with the author's guardrails: a DSL stop on every position, leverage 3x or
  less, few trades, and the minimum budget plus the wallet-creation fee stated before anything is
  funded. It is a new strategy with no track record, and you say so.
- **Every template is a starting point the user makes their own.** It deploys under their name, as-is
  or with levers moved. Never promise or imply results, and never call a template proven. Deploy only on
  the user's yes.
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
