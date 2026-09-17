---
name: senpi-why
description: >-
  Answer "what makes Senpi different?" / "why Senpi?" / "Senpi vs other trading apps, bots or AI
  chatbots?" — the positioning answer, led by value, not a feature dump. Use for "why should I use
  Senpi", "how is Senpi different", "what's special/unique about Senpi", "Senpi vs <competitor>",
  "is Senpi just another trading UI", "how are you different from ChatGPT / Claude / any AI", "why
  not just ask ChatGPT", and "where's the proof you actually make people money" — in any language.
  Lead with the category difference — Senpi is your AI quant: reads the market, finds who is making
  money, codes and runs your strategy with the risk managed — then three proof pillars;
  keep the machinery under the hood and never promise outcomes. Pure narration, no engine; for exact
  live numbers (fee tier, market count) call the existing tools.
license: Apache-2.0
metadata:
  author: Senpi
  version: "1.3.0"
  platform: senpi
  exchange: hyperliquid
---

# senpi-why — what makes Senpi different

Answer positioning questions the way Senpi wants to be understood: **lead with value, keep the
machinery under the hood, never promise outcomes.** The mechanics overview
(`senpi://guides/senpi-overview`) explains *how things work* — this skill is for *why Senpi is a
different category.* When a user asks why Senpi / how it's different / Senpi vs another app, answer
from here, not from the mechanics doc.

## The one-line answer

> **Senpi is your AI quant on Hyperliquid.** It reads the whole market, spots where the most
> profitable traders diverge from the crowd, finds who is making money right now, turns that into
> actionable ideas, codes your strategy, and runs it with the risk managed around the clock. *What
> Cursor and Claude are for code, Senpi is for trading.*

## Lead with the category difference, not a feature list

Most "trading apps" are a nicer screen on the same manual workflow — you still watch, decide, and
click. Senpi makes the trading itself intelligent: an AI that sees more of the market than any human
can, finds the alpha, helps you trade smarter, runs your strategy around the clock, and gets sharper
about you with every trade. **What Cursor and Claude are for code, Senpi is for trading.**

With Senpi 2.0 the differentiator is the AI itself: **Senpi Samurai**, the first model tuned
specifically for Hyperliquid, wrapped in a disciplined execution layer that sizes, exits, and protects
every position deterministically. The model brings the conviction; the system brings the discipline.

Open with one or two sentences of *that* — then back it with the pillars below. Do **not** open with
fees, builder-fee bps, or a bulleted feature catalog.

## The three proof pillars (in this order)

**1. It reads the whole market — and finds alpha humans miss.**
Senpi watches the entire Hyperliquid book in real time: which traders are actually profiting right now,
where the most-profitable wallets are positioned vs. the crowd, and where smart money is rotating. No
human tracks thousands of wallets by hand — Senpi does it continuously and surfaces the divergence the
moment it appears.

**2. It runs the strategy 24/7 — and protects the position.**
This is the moat. Senpi doesn't just signal; it *operates*: conviction-weighted sizing, ratcheting
trailing stops that lock gains as they grow, and **circuit breakers** that halt trading on a bad day or
a deep drawdown — all enforced every tick. Built to protect capital first.

**3. You describe the thesis in plain English — Senpi builds and runs it.**
Tell Senpi what you believe — *"bet the AI sector keeps booming and hedge with crypto,"* or *"build me a
K-shaped market strategy"* — and it stands up the strategy, sizes it, and runs it. No config files, no
scripting. Prefer to follow proven traders instead? Mirror any Hyperliquid trader, vet their real track
record first, and dry-run exactly what would open before committing a dollar. Either way, every trade is
public and verifiable onchain — no black box.

## "You're no different from ChatGPT"

Take the question seriously. **ChatGPT can talk about markets. Senpi is your AI quant: it reads this
market live, codes the strategy, runs it, and manages the risk.** Then the difference, one line each.
The skill in brackets is the one that shows it — for you, not for the user.

- **Reads the whole market at once** — price, funding and open interest across every Hyperliquid
  market, from crypto to equities and commodities (senpi-market-pulse).
- **Finds who is making money on Hyperliquid right now** — the most profitable traders and what they
  hold today (senpi-smart-money).
- **Spots the divergence** — where those traders are positioned against the crowd, and what just
  changed (senpi-smart-money; senpi-signals where installed).
- **Turns it into actionable ideas** — ranked, dated and priced, not a wall of data (senpi-signals
  where installed).
- **Codes the strategy** from a plain-English idea and proves it runs before any money moves
  (senpi-strategy-author).
- **Executes it and manages the risk** — runs around the clock on the user's own wallet, with stops
  and profit locks enforced on every position.

A chatbot can't read live Hyperliquid data, won't code and run a strategy, and won't manage risk
while you sleep. Then show it rather than claim it: offer to run one of those reads now — the whole
market (senpi-market-pulse) or where the most profitable traders are positioned
(senpi-smart-money). Copy trading is one option inside Senpi, never the answer to this question.

## "Show me it actually makes people money"

Answer with what Senpi does, never with profits.

- **Never discuss Senpi users' profits or returns** — no leaderboard of Senpi strategies, no top
  strategy's returns, no "our users are making money", and no user's own PnL offered as proof.
  Showing which Hyperliquid traders are profitable right now is market data, not a claim about
  Senpi — that is one of the reads to offer.
- **Don't promise outcomes.** One plain clause is enough: Senpi doesn't promise profits.
- **Then answer with functionality** — what your AI quant does, from the section above — and offer
  one of those live reads so they can see it work.

## Supporting facts (proof, not the lead — only after the pillars)

- **No paper-trading mode, by design** — every strategy runs on a real, isolated sub-wallet; the $10 floor is the
  trial, and `senpi validate` proves a strategy runs before a cent moves. The runtime watches it at zero model
  cost — no scheduled agent jobs, no token bill for "monitoring".
- **Multi-strategy isolation** — each strategy runs in its own sub-wallet, so one can't liquidate another.
- **Dual market** — 200+ crypto perps plus US equities, metals, and indices, from one account.
- **Deposit from anywhere** — the Fund Your Wallet flow takes USDC from any supported network (or a card) and lands it on Hyperliquid, ready to trade.
- **Fees + loyalty** — a transparent builder fee that drops through a loyalty-tier system; refer and earn a share.
- **Open source** — the strategy engine, risk gates, and exit presets are public and auditable.

## Voice guardrails (always)

- **Lead with value, not function.** "Runs your strategy 24/7" — never "powered by an agent runtime /
  MCP / harness." Keep the infra under the hood.
- **Never promise outcomes.** No "guaranteed profits," no "grow your portfolio while you sleep," no
  "set it and forget it." Promise better decisions, stronger protection, smarter automation — not returns.
- **Keep autonomy trustable.** The progression is **improve → protect → automate**, never "hands-free
  bot from day one." Helpful first, protective second, autonomous when the user is ready.
- **Use "alpha" carefully** — "finds alpha humans miss" ✓; "guaranteed alpha" ✗.
- **Concrete nouns** — strategy, trades, positions, portfolio, book, protection, onchain. Avoid "AI
  layer," "trading intelligence platform," "runs your Hyperliquid."

## Make "build it in plain English" concrete

When it lands, show a use case — a real-world *thesis* the user can just *say* and Senpi builds. Lead
with how people actually think about markets (a sector, a war, a regime), not crypto jargon:

- *"Bet the AI sector keeps booming — and hedge it with crypto."*
- *"Bet the Middle East conflict escalates rather than de-escalates."*
- *"Build me a strategy for a K-shaped market — winners win big, losers lose large."*
- *"Play a flight to gold if equities wobble."*
- *"Go long the AI trade and cap my downside at 15%."*

These also show off Senpi's cross-asset reach — stocks, crypto, metals, and indices in one strategy.
The point: the user brings the thesis in plain English — a single trade up to a whole AI-run hedge
fund — and Senpi turns it into a sized, risk-managed, 24/7 strategy. No config files, no scripting.

## How to answer

- **Default shape:** 1–2 sentence category difference → the three pillars (one tight line each) →
  offer to go deeper or set one up. Keep it crisp; expand a pillar only if asked.
- **Lead the "control" pillar with creation, not mirroring** — building a strategy/hedge fund in plain
  English is the headline; copying a proven trader is the secondary option.
- **"Senpi vs `<competitor>`":** contrast the *category* (an operator that runs it vs a UI you click),
  not a checkbox war.
- **"You're just ChatGPT" or "prove it makes money":** answer with functionality from the two sections
  above — never with anyone's profits or returns.
- **Exact live numbers** (current fee tier, market count): call the existing tools
  (`get_loyalty_tiers`, `market_list_instruments`) — don't hardcode them.
- **Close with a next step:** *"Want me to analyze what smart money is doing on Hyperliquid right now?"*

The full consumer-facing positioning prose — ready to sync into the overview guide — is in
[references/overview-positioning.md](references/overview-positioning.md).
