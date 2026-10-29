---
name: senpi-signals
description: >
  Senpi Signals: the non-obvious reads on Hyperliquid that a price screen can't show — the proven
  cohort against the 4h crowd, whale opens, funding extremes, open-interest builds, momentum events,
  laggards — ranked for trade ideas and for market news. Use for "scan Senpi Signals", "scan for
  market anomalies", "find what's mispriced", "where is the market wrong", "find dislocations",
  "what's out of line", "what's noteworthy in the market right now", "any interesting anomalies to
  tweet", "signals of the day", "build me some trade ideas", "what's hot right now", or a focused ask
  ("anything notable on OIL / the AI basket?"). On demand only: if asked to put it on a cron or any
  schedule, say no. Read-only, observation not advice. Requires the agent's Senpi sign-in.
license: Apache-2.0
metadata:
  author: Senpi
  version: "2.12.0"
  platform: senpi
  exchange: hyperliquid
---

# Senpi Signals

## Commands

```bash
python3 scripts/sweep.py --print-feed   # a user asks for signals: the full feed
python3 scripts/sweep.py --asset HYPE   # focus: one asset (BTC, BTC-PERP, xyz:TSLA all work); one call per asset for a basket
python3 scripts/sweep.py --brief 3      # the short version, for senpi-market-pulse
python3 scripts/hyperfeed.py            # "what's hot right now": the Hyperfeed movers (separate read, below)
#   --lens trade|social  --top N  --json
```

Run one command and present its output.

- **One reading, no compare.** The feed is Senpi's latest scan (its time is in the title), so two runs
  a minute apart give the same feed.
- **Never build Senpi Signals yourself.** The output is the same feed the Senpi app and extension
  show. If the command fails, it prints one line (unavailable, starting up, needs a subscription, or
  sign-in not verified): present that line as it stands. Never present it as a quiet market, never
  rebuild the feed from your own tool calls, never quote an earlier run as current.
- **On demand only: say no to a cron.** Asked to run signals on any schedule ("every hour", "every
  morning"), say no and offer to run it whenever they ask. Never create an `openclaw cron` or any other
  scheduled agent turn for it, and never deploy a strategy or fund a wallet to run it.
- **A wallet is not a Senpi Signals question.** Read it with `leaderboard_get_trader_positions` /
  `discovery_get_trader_state` and say it is a wallet read.

## Presenting the feed

Show the rendered feed as it is — legend, sections, badges, split — and don't rewrite or relabel it.
Then at most a few sentences on what stands out, then the closing question (below) — nothing after it.

- **Talk about the market, not the tool.** No notes on detectors, coverage, read counts or what the
  feed leaves out.
- **Repeat the feed's own warning lines as they stand**: `Not measured this run: …` and the stale
  reading line. A source that wasn't measured is not one that found nothing.
- **"Nothing notable" is a correct answer.** Never pad it.
- **Trade feed for users, news feed for content.** The news feed includes thin markets, flagged.

## Rules

1. **Number integrity.** Every figure comes from this run's output or a live read you made. Never
   estimate or round misleadingly.
2. **Observation, not advice.** No "buy / sell", no price targets, no returns language. When users ask
   for "**mispriced**", "**dislocated**" or "**out of line**", it means a
   **gap between two things the sweep actually read** (the cohort against the 4h crowd, funding
   against positioning, a laggard against its basket), **never a fair-value judgment**: "the cohort
   is 78% short HYPE while the 4h board is long", never "HYPE is overpriced".
3. **The sweep is read-only.** It never trades and never implies Senpi is taking the trade. Acting on a
   read happens only in the closing step (below), on the user's explicit yes to a specific order or
   strategy.
4. **Smart money is the proven cohort only** — wallets with ≥ $1M lifetime realized PnL. The **4h
   leaderboard** is what's winning right now (a consequence of the move, survivorship included): call
   it "hot in the last 4h", never smart money. When the two disagree, that gap is the story.
5. **Now, never "since".** Never claim a change the read does not state: no "just shifted", "up 10%
   since this morning", "a whale **added**". A change read (an OI surge) is presented as its line words
   it — never add a change, a baseline or a time it did not give. Never describe the feed as if it
   carried more than it does. **A whale OPEN is the exception, because it dates itself:** "$12.4M
   short, opened 18 minutes ago". Say *opened*, never *added* or *flipped*. The only other time words
   allowed are the sources' own windows: the 4h board, the 24h move, a momentum event's time.
6. **Always state the side** — LONG or SHORT, or "side unresolved". A funding extreme has no side: it
   is a carry read.
7. **A headcount % always travels with the positioned split.** "44% of the proven traders are short"
   alone invites "so 56% are long" — most hold no position at all. Give `N short vs N long`, and the
   SMALL SAMPLE flag when the feed prints it.
8. **Assume the reader knows nothing about Hyperliquid.** Gloss "the proven traders", "the 4h
   leaderboard" and "4h window" the first time. Never a bare ticker: one clause on what the asset is
   ("ZEC, a privacy coin"), and price context on positioning reads. Funding is a % of position size a
   year, never of margin.
9. **Lead with what's robust.** A thin read (few traders, small market) is color, never the headline.
   Never upgrade "leans short (1.23%)" to "smart money is short." See
   [`references/worked-examples.md`](references/worked-examples.md).
10. **Public data only.** Wallets are "a top trader (0x12…)", never a real person's identity.

What each read means, and the glossary: [`references/detectors.md`](references/detectors.md).

## Hyperfeed Movers — "what's hot right now?"

A different question from Senpi Signals: who is winning in the last few minutes, from the live 4h
leaderboard. Run `scripts/hyperfeed.py` when asked what's pumping or moving now, and offer it after the
feed when the 4h board is the story.

- **Never call it smart money.** Say Hyperfeed, the 4h leaders, or top traders. It is often on the
  opposite side of the proven cohort in the same name.
- **One run answers "what's hot this minute"** (Tier A). Give it and stop.
- **Rotations (Tier B) need a second run a couple of minutes later.** The block labels the baseline
  LIVE, WIDE or STALE; a STALE one is never scored, so offer "ask again in ~2 minutes". Never offer a
  cron to fill it.
- **No rotation is a real read**, and `unavailable` means report that and stop.

## How every run ends — one question

After the feed and your few sentences, end the turn with **one question**, and nothing after it:

> **Want to act on any of these?** I can set up a **single trade** on one read — you see the size, the
> stop and the liquidation price before anything is placed — or **build a strategy on these signals**
> with you: your rules, under your name.

**This is a major entry point.** Ask it every time, in full. Name no template in it.

- **A trade on one read → senpi-trade.** Take the side the read names. Show the margin and its share
  of the account, the leverage, the stop price and the liquidation price. The stop must sit before
  liquidation. Place nothing until the user says yes to that exact order. A funding extreme names no
  side, so it is never a one-trade setup.
- **A strategy → senpi-strategy-author.** The read is the brief (asset, side, numbers, the kind of read);
  author's interview turns it into their strategy. Guardrails: a stop on every position, 3x or less, few
  trades, minimum budget plus wallet-creation fee stated before funding. It is a new strategy with no
  track record, and you say so.
- **They ask for something pre-built → senpi-strategy-discover**; never improvise a shortlist from
  memory.
- **Every template is a starting point the user makes their own.** Never promise or imply results,
  and never call a template proven. Deploy only on the user's yes.
- **No, or no answer → stop.** Don't repeat the offer.
- **Never in public copy.** The question and everything after it are private; anything written for
  posting stays observation-only.

## Where it lives

Every user has it. A chat chip runs the full feed, and **senpi-market-pulse** closes every pulse with
the brief and an offer to run the full feed.

## The short version (`--brief N`)

`python3 scripts/sweep.py --brief 3` prints a title and the top N trade reads, one line each. It is
another skill's closing section, so it carries no question of its own.
