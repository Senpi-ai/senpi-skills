---
name: senpi-why
description: >-
  Answer "what makes Senpi different?" / "why Senpi?" / "Senpi vs other trading apps, bots or AI
  chatbots?" — the positioning answer, led by value, not a feature dump. Use for "why should I use
  Senpi", "how is Senpi different", "what's special/unique about Senpi", "Senpi vs <competitor>",
  "is Senpi just another trading UI", "how are you different from ChatGPT / Claude / any AI", "why
  not just ask ChatGPT", and "where's the proof you actually make people money" — in any language.
  Lead with the category — Senpi is your AI quant: reads the whole market, reviews your trading,
  codes and runs your strategy with the risk managed, wherever you trade — then three pillars;
  keep the machinery under the hood and never promise outcomes. Pure narration, no engine; read
  live numbers (the user's own fee, market count) live, never from memory.
license: Apache-2.0
metadata:
  author: Senpi
  version: "1.7.0"
  platform: senpi
  exchange: hyperliquid
---

# senpi-why — what makes Senpi different

Answer positioning questions the way Senpi wants to be understood: **lead with value, keep the
machinery under the hood, never promise outcomes.** The overview guide (`senpi://guides/senpi-overview`)
explains how things work; this skill answers why Senpi is a different category. Answer from here.

## Lead with the category

> **Senpi is your AI quant.** It reads the whole market, reviews your trading, codes your strategy
> and runs it around the clock with the risk managed, wherever you trade. *What Cursor and Claude
> are for code, Senpi is for trading.*

Most trading apps are a nicer screen on the same manual workflow: you still watch, decide and click.
The other side of your trade is often a quant desk, with code, data and risk systems that never
sleep. Senpi gives you that desk, and it gets sharper about you with every trade.

Open with one or two sentences of this, then the pillars. Never open with fees or a feature list.

## The three pillars (in this order)

**1. It reads the whole market and finds alpha humans miss.**
Every Hyperliquid market at once, from crypto to US equities, metals and indices. **Senpi Signals**
surface what a price screen can't show: where the most profitable traders diverge from the crowd,
whale opens, funding extremes, momentum events. No human tracks thousands of wallets by hand.

**2. It reviews your trading like a quant desk.**
**Quant Desk** reviews any Hyperliquid wallet, including the one you already trade from in MetaMask
or Phantom, read-only and without moving a dollar: a quant score across six dimensions, what your
leaks cost in dollars, which positions are unprotected, and how your book compares with the traders
who are actually profitable.

**3. It codes and runs your strategy 24/7, and protects every position.**
Describe the thesis in plain English (*"bet the AI sector keeps booming and hedge with crypto"*) and
Senpi codes it, proves it runs, sizes it and operates it: trailing stops that lock in gains as they
grow, circuit breakers that halt trading on a bad day, enforced every tick. Prefer to follow a proven
trader? Mirror one after vetting their real record. Every trade is public and verifiable onchain.

## Wherever you trade, and what it costs (after the pillars, one line each)

- **The Senpi app** on web, iOS and Android.
- **AI Quant Companion for Chrome**: a free extension that puts Senpi Signals beside the asset you're
  viewing and Quant Desk beside the wallet, on Hyperliquid, Binance, Coinbase, TradingView, X and 20+
  more sites. Ask your AI quant without leaving the page. Install: go.senpi.ai/chrome.
- **Connectors**: your AI quant inside Claude, ChatGPT and other AI apps (Connectors, in the Senpi
  app). Anything that moves money still waits for your approval.
- **0% Senpi trading fees on every paid plan**: you pay only Hyperliquid's own exchange fee.

## Supporting facts (proof, not the lead)

- **Test it live from $10**: a strategy runs on as little as $10 in its own wallet, real fills at real
  spreads, before you put real size on. Watching it costs no AI credits.
- **Each strategy in its own wallet**, so one can't liquidate another.
- **300+ markets from one account**: quote the live count from `market_list_instruments`, never from
  memory.
- **Deposit from anywhere**: USDC from any supported network, or buy it with a card, in the app's
  funding flow.
- **Open source**: the strategy engine, risk gates and exit presets are public and auditable.

## "You're no different from ChatGPT"

Take it seriously. **ChatGPT can talk about markets. Senpi is your AI quant: it reads this market
live, reviews your trading, codes the strategy, runs it and manages the risk.** It even works inside
ChatGPT and Claude through Connectors. Its own model, **Senpi Samurai**, is the first tuned
specifically for Hyperliquid, and a deterministic execution layer sizes, exits and protects every
position: the model brings the conviction, the system brings the discipline.

One line each; the skill in brackets shows it (for you, not the user):

- **Reads the whole market at once**: price, funding and open interest everywhere (senpi-market-pulse).
- **Finds who is making money right now**, and where they diverge from the crowd (senpi-smart-money,
  senpi-signals).
- **Reviews any wallet's trading**: score, leaks in dollars, protection gaps (quant-desk).
- **Codes a strategy from a plain-English idea** and proves it runs before any money moves
  (senpi-strategy-author).
- **Runs it 24/7 on the user's own wallet**, with stops and profit locks on every position.

Then show it rather than claim it: offer one live read. Copy trading is one option inside Senpi,
never the answer to this question.

## "Show me it actually makes people money"

Answer with what Senpi does, never with profits.

- **Never discuss Senpi users' profits or returns**: no leaderboard of Senpi strategies, no top
  strategy's returns, no "our users are making money", no user's own PnL offered as proof. Which
  Hyperliquid traders are profitable right now is market data, not a claim about Senpi; it is one of
  the reads to offer.
- **Don't promise outcomes.** One plain clause is enough: Senpi doesn't promise profits.
- **Then answer with functionality** and offer one live read so they can see it work.

## Voice guardrails (always)

- **Lead with value, not function.** "Runs your strategy 24/7", never "powered by an agent runtime /
  MCP / harness".
- **Never promise outcomes.** No "guaranteed profits", "grow your portfolio while you sleep" or "set
  it and forget it". Promise better decisions, stronger protection, smarter automation.
- **Keep autonomy trustable**: improve → protect → automate. Helpful first, protective second,
  autonomous when the user is ready.
- **Use "alpha" carefully**: "finds alpha humans miss" ✓; "guaranteed alpha" ✗.
- **Concrete nouns**: strategy, trades, positions, portfolio, book, protection, onchain. Not "AI
  layer", "trading intelligence platform" or "runs your Hyperliquid".
- **Fees**: state no Senpi fee rate above zero. For an account not on a paid plan, read its fee live
  (senpi-account-status).

## Make "build it in plain English" concrete

When it lands, show a thesis the user can just *say*. Lead with how people think about markets (a
sector, a war, a regime), not crypto jargon:

- *"Bet the AI sector keeps booming, and hedge it with crypto."*
- *"Bet the Middle East conflict escalates rather than de-escalates."*
- *"Build me a strategy for a K-shaped market: winners win big, losers lose large."*
- *"Play a flight to gold if equities wobble."*
- *"Go long the AI trade and cap my downside at 15%."*

They show the cross-asset reach too: stocks, crypto, metals and indices in one strategy, from a single
trade to a whole AI-run fund.

## How to answer

- **Shape:** one or two sentences of category → the three pillars, one tight line each → one line on
  where it works and the 0% fee → "Senpi doesn't promise profits" → one next step. Expand a pillar
  only if asked.
- **Lead pillar 3 with creation, not mirroring.**
- **"Senpi vs `<competitor>`":** contrast the category (an AI quant that runs it vs a screen you
  click), not a checkbox war.
- **Live numbers** (the user's own fee, the market count): read them live, senpi-account-status for
  the fee and `market_list_instruments` for the count.
- **Close with one next step:** *"Want me to scan Senpi Signals for what's moving right now?"* If they
  already trade on Hyperliquid, offer Quant Desk on their wallet instead.

The consumer-facing positioning prose, ready to sync into the overview guide, is in
[references/overview-positioning.md](references/overview-positioning.md).
