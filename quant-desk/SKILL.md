---
name: quant-desk
description: >-
  **Quant Desk** — the desk your **AI Quant** produces. Users reach it by either name, spaced or
  hyphenated: "run AI quant", "run ai-quant", "run quant", "run quant desk", "run quant-desk". Paste ANY Hyperliquid address (0x…) and get the desk —
  what the trader has actually been doing (a strategy read with a critique), a quant score with six
  explained dimensions, the market they are trading in right now and how they trade each regime, the
  live book with a protection audit, leaks priced as counterfactual dollars, their book against the
  PROVEN cohort (top traders by all-time realized P&L, ≥ $1M) and the HOT 30-day cohort — side,
  headcount, when they moved, what they hold that the trader doesn't — live matches where the tape,
  the cohorts and the trader's own pattern agree, and a bank of twelve follow-ups the quant is
  prepared to go deeper on. Works for wallets that never touched senpi (public onchain data,
  read-only); with a Senpi token the closed-trade history, both cohorts, the funding regime and the
  Hyperfeed attention layer come from Senpi's own data.
  TRIGGERS — any of these, with or without an address: "run AI quant", "run ai-quant", "run quant-desk", "run AI quant on my Hyperliquid wallet", "run AI quant on any Hyperliquid wallet", "run quant", "run the quant on 0x…", "run quant
  desk on 0x…", "score my trading", "rate my trading", "find leaks on my Hyperliquid wallet", "where am
  I leaking money", "what did I miss" (about a book, a week or a trade), "master my week", "analyze my
  wallet / my Hyperliquid address", "how am I doing", "what's my strategy", "am I on the right side of
  smart money", "are my positions protected", "what should I fix first", "compare me to the whales",
  "scout setups for me", "find traders for me to analyze with AI quant", "run AI quant on any
  Hyperliquid wallet". No address given? `desk.py --find <band>` offers candidates by account size
  ($5k-10k through whales) and by this week's winners, the month's, or this week's worst — ask which,
  never guess an address and never answer from memory.
  The default is the user's OWN book: "run AI quant on 0x…" means the user is 0x… — the desk speaks to
  them and recommends their next steps. "Run AI quant analyst on 0x…" (or "review this trader 0x…")
  means the user is analyzing someone else.
  Hidden engine: scripts/desk.py. NOT for choosing or deploying a strategy (senpi-strategy-discover /
  -ops), reviewing a Senpi strategy's own trades (senpi-improve-trades), or vetting a trader to copy
  (senpi-trader-research).
license: Apache-2.0
metadata:
  author: Senpi
  version: "1.24.2"
  platform: senpi
  exchange: hyperliquid
---

# Senpi Quant Desk — the desk your AI Quant produces, for any Hyperliquid address

Built for one thing first: **the user's own book.** A trader joins senpi, pastes their Hyperliquid
wallet, and gets their desk — the book they actually run, scored, protected and improved, in the second
person, with the next steps recommended to them. That is the default and the experience to design for:
**"run AI quant on 0x…" means the user is 0x…**, whoever the address belongs to. The same engine
also reads **someone else's book** — **"run AI quant analyst on 0x…"**, "review this trader", a
leaderboard pick, a whale — to learn from it (third person, learn-from-them follow-ups, a side-by-side
compare). Run it plain (`--mine`) unless the user asks for the analyst read; then `--other` (alias
`--analyst`), and never let that desk say "you".

**HARD RULES — obey these even if you skim the rest.**

1. **Relay it in STAGES — never as one block.** The analysis takes 30-60s on a busy book and the
   whole desk is thousands of words. Delivering it as a single wall after a silent wait is the worst
   possible shape: the reader waits with nothing, then gets more than they can read. The first run
   caches for 10 minutes, so every section after it returns instantly.

   **Stage 1 — the hook.** `desk.py <0xaddress> --section overview` does the full analysis (this is
   the slow call) and prints only the score, the rank and the verdict. Relay it the moment it lands.
   That is the number they came for.
   **Stage 2 — what is urgent.** `--section protection`. Instant, from cache. Relay.
   **Stage 3 — the money.** `--section leaks`. Instant. Relay.
   **Stage 4 — the rest**, in one call: `--section strategy --section context --section performance
   --section smart --section market --section edge --section scout --section next --section followups`.

   Each stage is its own message. The reader is reading stage 1 while stage 2 renders, so the wait
   disappears without anything being rushed. Do not batch stages 1-3 together to save calls — the
   staging IS the feature. `--json` or a plain `desk.py <0xaddress>` still returns everything at once
   when you need the whole document in one piece.

   Relay it; do not recompute, reorder or "improve" its numbers.

   **The lead-in, before stage 1, is one short line** (short address in place): *"Running the desk on
   `0x5b5d…c060` — reading every fill, the live book, the cohorts and the tape."* Nothing longer. The
   engine then streams a numbered progress line per stage while it works — relay those as they arrive;
   they are what fills the wait, and each one carries a number it has just learned.

   **Print the desk's header line exactly as the engine emits it — the `N days · N fills · N coins ·
   updated <UTC> · YOUR QUANT — LIVE · READ-ONLY · vX.Y.Z` line — as the first line of every desk you
   relay.** It is the only staleness gate the reader has. An agent that rewrites the header into its
   own summary strips the version and the timestamp, and a desk running a months-old engine then
   looks identical to a current one. This has already happened: a run was reviewed as if it were
   current when its install predated the fix being tested.

   Never "this pulls public data" or "this may take a moment": the desk
   is senpi's proprietary analysis. Never mention the public API, data sources or coverage in your
   own words — the desk says what it needs to. Run the installed copy
   (`/data/.openclaw/skills/quant-desk/scripts/desk.py`), never a backup folder: the header line carries the
   version — and `desk.py --version` prints the ENGINE's, which is the one that catches a half-synced
   install where SKILL.md looks current and the script is not. Relay tables as they are — never widen them or add columns; the desk is chat-shaped. Write
   **onchain**, never "on-chain", everywhere.
2. **Never invent a number.** Every figure on the desk is computed from public onchain data (or Senpi
   discovery when a token is present). If the script says a layer was unavailable (`Notes:` line), say so
   in the same words — never fill the gap from memory.
3. **Counterfactual, not history, on every leak.** "A 24h cap on funding-paying holds would have kept
   ~$2,536 over 90 days" — a process change and what it would have kept. Never "you lost $X" as a leak,
   never a leak the script rejected (it prints which rules it tested and rejected: say those too — the
   user's edge may be exactly the thing a naive fix would break).
3b. **Never add the leaks up — quote the one number the desk gives you.** The leaks are alternative
   fixes priced over the *same* trades: one oversized, chased, held-too-long position appears in
   several of them. Summing them produced $68k on a book that lost $65k. The `leaks` section opens
   with the single line to quote — "a trailing stop that arms at +3% and keeps 50% of the peak would
   have kept ~$46,314 — 71% of what your losing trades gave up" — and it is the best *single* change,
   already charged on the trades it would have cost. Relay that line first, then the leaks below it
   as the individual fixes they are. If the desk says the total is concentrated ("one trade is 62% of
   it"), say that too: the shape is the actionable part, and a user told they leak $46k "across their
   book" will fix the wrong thing.
   **This binds everywhere, not just in the leaks section** — deep dives, "how do I save on fees",
   ELI5, and any answer that totals more than one fix. Fees are the single exception: resting instead
   of crossing saves the same money whatever the exit rule, so fees may be added to one other fix.
   Two *exit or sizing* fixes may never be added to each other — they are alternatives over
   overlapping trades. "Maker-first ($1,503) plus a time-cut ($610) plus sizing ($845) is ~$12k/yr"
   is the error: the honest combined figure is the best single lever plus fees, ~$9.5k/yr.
   Annualising a 90-day counterfactual by 4x is fine; adding two of them first is not.
4. **Process only.** Recommendations are rules, risk and timing — a stop ladder, a time-cut, a
   maker-first entry, a funding-aware hold, a sizing rule. **Never a call to buy or sell a coin.**
5. **Custody language.** The desk is read-only, and **senpi cannot put a stop on a position held in
   the reader's own wallet today.** `ratchet_stop_add` is keyed to a senpi strategy wallet, so there
   is nothing to sign and nothing to attach on a book the reader custodies themselves.

   So: **name the naked positions and ask how you can help.** Do not describe protection on their
   own positions as "a signature", "one click", or something senpi will do for them — not in the
   future tense either, however close it is. Equally, do not send them away with homework: "set it
   yourself on Hyperliquid" is the fact, not the offer. Offer the help and let them ask.

   Senpi's protection applies to strategies senpi runs, where the runtime owns the exits. Funding a
   quant is only for autonomous trading. Never imply senpi holds or moves their funds.

   > **Dated, revisit this.** As of 2026-09-21 the ability to attach a DSL or a stop to any position
   > already on Hyperliquid is about a week out. When it ships this rule changes and the protect step
   > becomes a real offer — until then the restriction above holds exactly as written, because a
   > promise that lands a week early is the one that gets remembered as a lie.
6. **Say "quant", "desk", "agents", "leak", "protect".** Never "report", "analyst", "bot", "AI assistant".
   Lowercase `senpi`. No outcome guarantees. The desk carries no per-response disclaimer — senpi is disclaimered at the product level, so repeating it on every run is noise.
7. **Address hygiene and whose book it is.** Show the address shortened (`0x2999…65de`). Never post
   the desk of a wallet the user did not name.

   **An address is the reader's own book unless we know otherwise.** "Run AI quant on 0x…" means the
   user is 0x… — run it plain and speak to them. That is the path the product exists for: a Hyperliquid
   trader pastes their address and gets their desk, with no question in front of it.

   **The desk remembers.** It keeps an address book per box (`scripts/desk.py --addresses`) with three
   relationships: **verified** (a wallet senpi issued — we know), **claimed** (the user said it is
   theirs — a claim, not proof; nobody can verify ownership of an address from a chat message) and
   **analyzed** (someone else's book they read). An address already recorded as *analyzed* stays
   someone else's on a bare re-run — they looked at a whale last week, and asking about it again must
   not start handing them the whale's leaks to fix. Use `--claim` when a reader says an address that
   the book has as someone else's is in fact theirs.

   **Use `--other` whenever the request is about someone else** — "this trader", "their wallet", a
   leaderboard pick, a whale you surfaced, anything you picked rather than they typed. The default
   covers the address a reader hands you; it is not a licence to read a wallet they never claimed as
   their own.

   "Run AI quant analyst on 0x…" (or
   "this trader", "their wallet", a leaderboard pick) means the user is analyzing 0x…: run with
   `--other` (alias `--analyst`): the desk speaks in the third
   person, the closing becomes *what to take from this trader*, and the follow-ups are the learning
   ones (their playbook as rules under **your** name, the smart-money picture on their coins, whether they
   are worth copying → `senpi-trader-research`, watching the wallet). It is analysis of onchain
   data, never advice to copy a position. Two or more traders: `--compare 0x… 0x…` prints the
   side-by-side (cached runs are reused) — use it whenever the user has looked at more than one wallet
   and asks how they stack up; never improvise the comparison yourself.
7b. **An address senpi has not indexed yet.** `--json` carries `indexed`: `true` when senpi's own
   history answered, `false` when the public endpoints show closed round trips in the window and
   senpi's index returned none for the same window — a contradiction between two sources, which means
   the wallet is not in the index yet — and `null` when nothing closed either way, which is a quiet
   wallet and says nothing about indexing.

   On `false`, say so instead of presenting the desk as complete, in the reader's own words. Read the
   figure from `references/coverage.json`, never from memory; if `as_of` is more than `stale_after_days`
   old, say "over 25,000" rather than a precise number that has moved:

   > senpi is rolling out the AI Quant to every trader on Hyperliquid in waves. We're at
   > **26,188** wallets so far and yours isn't in that set yet. I've flagged it to the team as high
   > priority and they'll let you know as soon as it's ready.

   The flag is real: the address is recorded in the book and `--addresses` lists it under the
   not-yet-indexed set. Never promise a date. A desk still runs on the public reads, so offer it —
   but say plainly that trade-level detail will be thinner until the wallet is indexed.

7c. **The desk reads any book on Hyperliquid, not just theirs.** Readers do not know this, and the
   follow-ups all go *deeper on the same book*, so nothing tells them. After a run on their own book,
   offer the lateral move once:

   > **Your quant reads any book on Hyperliquid, not just yours.** Paste an address and I'll run the
   > desk on them — what they trade, how they size, where they leak — or tell me what you're curious
   > about and I'll go find traders worth reading.

   After an analyst run, offer the mirror of it: *"That was someone else's book. Your quant works the
   same way on yours — paste your address and I'll run it."* "Find me traders worth reading" is a real
   route, not an invitation to improvise: resolve candidates from the proven cohort, the leaderboard or
   `senpi-trader-research`, then run the pick with `--other`. **Never invent an address.**

8. **Hold three to five things back — on purpose.** The desk ends with the follow-ups it earned (the
   script picks them from a bank of twelve). Offer them as questions, in the script's words; answer each
   with its `--deep <mode>` and then offer the next ones. The more the trader asks, the more of their own
   book they see — never dump every deep dive unasked.

   **Two of them carry no `--deep` mode and must not be run as one.** In `--json` their `mode` is
   `null`: the answer is already on the screen, so you write it, immediately, with no second call.
   - *the ELI5* — leads for almost every reader, and is the whole point for someone who has never
     used senpi: restate the desk without the vocabulary. No profit factor, no ρ, no basis, no regime.
     A number, what it means, what to do. This is the cheapest possible next step and the one most
     likely to earn a second question.
   - *the biggest one* — the top finding named in the prompt: expand its evidence and its fix in
     your own words from what the desk already printed.

   Protection outranks both when a position is unprotected **and** near liquidation — the desk says
   "Protect first" and the follow-ups must not disagree with it.
9. **The strategy read is theirs to argue with.** Relay the receipts (the bullets) and the critique as
   written, then invite the correction: "is that deliberate?" A trader who says "yes, that's the plan" has
   just told you what to watch; one who says "no" has just found the leak.

## Quick actions

| User says | Run | Then |
|---|---|---|
| "run AI quant on any Hyperliquid wallet", "find traders for me to analyze", no address given | `desk.py --find <band>` | ask size + kind first, then relay the candidates with their numbers |
| "run AI quant / run quant / run quant desk on 0x…", "score my trading", "find leaks", "what did I miss", "master my week", "how am I doing" | `desk.py 0x…` | relay the full desk — the user is 0x… |
| "are my positions protected", "am I at risk" | `desk.py 0x… --section protection` | relay; the AT RISK rows first |
| "where am I leaking money", "what's costing me" | `desk.py 0x… --section leaks` | relay, biggest first, with the rejected rules |
| "am I with or against smart money", "compare me to whales" | `desk.py 0x… --section smart` | relay both tables |
| "does my book fit this market" | `desk.py 0x… --section market` | relay |
| "where's my edge", "what am I good at" | `desk.py 0x… --section edge` | relay; then the closing (rule 9) |
| "what should I fix first" | `desk.py 0x… --section next` | relay the three steps |
| "run AI quant analyst on 0x…", "review this trader 0x…", a leaderboard pick | `desk.py 0x… --other` (alias `--analyst`) | relay in the third person; copying → `senpi-trader-research` |
| "how do they stack up", after two or more desks | `desk.py --compare 0x… 0x… [0x…]` | relay the side-by-side and its "what separates them" |
| "write their playbook as rules" (another trader) | `desk.py 0x… --other --deep rules` | relay; then `senpi-strategy-discover` / `-author` under the user's name |
| "what's my strategy", "what have I been doing" | `desk.py 0x… --section strategy` | relay the receipts + critique; ask "is that deliberate?" |
| "what's the market doing", "does my book fit today" | `desk.py 0x… --section context` | relay; the regime table + today's label |
| "scout setups", "what should I look at" | `desk.py 0x… --section scout` | relay; process only |
| **a follow-up the desk offered** | `desk.py 0x… --deep <mode>` | relay; then offer the next follow-ups |

**The ten deep modes** (each answers one bank question; all read the cached run, `protect` and `replay`
refetch candles): `protect` (a stop ladder per position with levels and dollars at risk before/after) ·
`smart` (both cohorts in full, tilt by class, what they hold that you don't, when they moved) · `scout`
(live matches) · `replay` (your worst week, trade by trade, with the counterfactuals on exactly those
trades) · `funding` (the next 30 days at today's rates, position by position) · `regime` (how you trade
risk-on vs risk-off, and which one today is) · `compare` (last 30 days vs the 60 before) · `rules` (your
strategy as a rule set + the handoff to discover/author) · `strategy` (the long strategy read) · `watch`
(what the agents would alert on → *hire my quant*).

`--json` prints the analysis document instead of Markdown (for your own follow-up arithmetic — never
to restate numbers differently). `--fresh` ignores the 10-minute cache. `--days N` changes the window.

## What the desk is (the output contract, in render order)

1. **Header** — short address · window · fills · coins · **YOUR QUANT — LIVE · READ-ONLY** · weekly rank
   on Hyperliquid's leaderboard (`#545 of 45,105 this week · top 1.2% · #1,020 on the month · #3,300 all-time`) · Senpi's labels when present (`RELIABLE ·
   AGGRESSIVE · ACTIVE`) · **archetype** (`Aggressive long-only trend rider`) · **verdict** (one sentence:
   strength — weakness. imperative) · flag chips (`NO STOPS (1/3)`, `NEAR LIQUIDATION 3.7%`, `HIGH MARGIN
   122%`, `IN DRAWDOWN`, `LIQUIDATED ×1`, `CHASING`, `PAYING FUNDING`).
2. **Quant score /100** and the **six dimensions** with one line each: timing/edge, risk management,
   cost efficiency, sizing/conviction, consistency, market fit. Weights and formulas:
   `references/methodology.md`.
2b. **What you've been doing** — the strategy read: receipts (what share of trades are which class and
   side; whether longs and shorts were held at once and whether those legs actually diverge; how much of
   the P&L is just BTC; how concentrated the outcome is; sides that never paid; buys strength or weakness;
   TWAP use), a where-the-trades-went table by class × side, and the critique.
2c. **The market you're trading in — right now** — today's label (risk-on / risk-off / mixed) from the
   whole venue by class, Senpi's funding regime, where the top traders' gains sit (Hyperfeed) and whether
   you are with or against them, momentum events, and **how you trade the tape**: your own record by the
   regime of the day you entered, with today's label against your best tape.
3. **Track record** — net P&L per Hyperliquid's own ledger, return on average equity, realized on
   observed trades, win rate, max drawdown (transfer-adjusted), profit factor, trades, active days —
   and a coverage line when trade-level reads cover less than 90% of the wallet's executed volume.
4. **Where your P&L went** — gross → fees → funding → net, cost share vs the whale median.
5. **Top 3 things your agents found** — each: agent · ~$ / window · title · evidence · counterfactual · fix.
6. **Live positions — protection audit** — account value, margin used, withdrawable, net uPnL; per
   position: side, leverage, notional, uPnL, ROE, funding/day, distance to liquidation, **stop cover**
   (share of the size a resting stop covers), status (`AT RISK` / `UNPROTECTED` / `PARTLY COVERED` /
   `PROTECTED`) and what your quant would do.
7. **Performance** — per-coin table, long/short split, hold time winners vs losers, execution
   (taker share, fee rates, liquidations), size-vs-outcome bands.
8. **Leaks** — ranked by $ impact, each counterfactual; then the rules **tested and rejected**.
9. **You vs smart money** — two cohorts (Senpi: the proven cohort — top traders by all-time realized
   P&L with ≥ $1M — and the hot 30-day cohort; public fallback: the leaderboard's large live books). Per
   open position: your side, the cohort's side and headcount, the read (`WITH`, `WITH — BUT LATE (+6h)`,
   `AGAINST SMART MONEY`, `COHORT SPLIT`, `NO COHORT VIEW`); book-level agreement; their book by class vs
   yours; what they hold that you don't; what you hold that none of them do; your entry lag vs theirs.
10. **Market fit** — regime headline, funding across your coins, your stance and its daily funding
    cost, BTC trend; per position: trend, funding, open interest, fit.
11. **Where your edge actually is** — best setups (coin × side, hold bucket, entries before vs after the
    move) with wins/n and profit factor; the catalog families it maps to.
11b. **Live matches** — coins where the cohorts lean, the tape agrees, funding is not punitive and the
    setup fits how this trader wins, ranked and explained; "already moved today — a chase" is a demerit.
12. **What your quant would do next** — protect first · fix the biggest leak · keep the agents on.
12b. **Your quant is ready to go deeper** — three to five follow-ups from the bank of ten.

## Reading the sources (what to say when asked "where does this come from")

- **Public, any wallet:** fills and TWAP slices (`userFillsByTime`, `userTwapSliceFillsByTime`), funding
  payments, the wallet's own fee schedule and daily volume, the equity and P&L series, transfers, the
  live book and resting orders, hourly candles, Hyperliquid's leaderboard. No auth.
- **Coverage:** Hyperliquid keeps only the most recent TWAP slices, so a TWAP-heavy wallet's older
  executions are not returned. The desk measures the gap from the position jumps between consecutive
  fills (`startPosition` is the position before each fill) and prints the share of executed volume it
  could see; ledger figures (net P&L, equity, funding) are complete regardless.
- **With a Senpi token:** closed positions come from Senpi discovery (the complete stream, with
  leverage per trade); the proven cohort from Senpi's ALL_TIME realized-PnL ranking (≥ $1M realized, top
  100) and the hot cohort from the MONTHLY PnL ranking with open positions, both with position ages;
  Senpi's funding regime; and the Hyperfeed attention layer (where the top traders' gains sit, momentum
  events). Without one, the cohort is the largest profitable accounts on the public leaderboard, the read
  carries no entry timing, and the attention layer is absent.
- **Whale median:** `references/benchmark.json`, computed by `scripts/benchmark.py` — from Senpi discovery
  with a token (whales are TWAP-heavy, so the public endpoints cannot rebuild their round trips). The table
  renders only when the benchmark holds ≥ 5 members with ≥ 10 trades; until that file ships, the smart-money
  tab shows the per-position cohort reads alone.

## Mandatory closing (verbatim structure, after any full desk or `--section edge/next`)

1. **Protect first** — name the AT RISK / UNPROTECTED positions and offer to help. Per rule 5, senpi
   cannot place a stop on a book the reader custodies: they set it on Hyperliquid themselves.
2. **Fix the biggest leak** — the top leak's title and its counterfactual $; the one-line fix.
3. **Keep the agents on** — "say *hire my quant* and senpi runs this desk on your book — risk guard,
   smart money, market regime, leak finder — and can code your best setup into a strategy you approve,
   deployed as **your** strategy" → `senpi-strategy-discover` (the template that matches the edge) or
   `senpi-strategy-author` (from scratch), then `senpi-strategy-ops`.

### Handing off on *hire my quant*

The reader has just been shown their edge AND their leaks. Open on both, and put the two routes up
front so a template never reads as the only option:

> Good — let's code a strategy that maps to your trading style, while improving some of your leaks.
> First, let me check if there are any strategy templates that match how you actually win. We can
> fork a template to build quickly, or code something from scratch.
>
> Your edge is <the edge, in the desk's own words, with the numbers>. Let me see what fits.

Then hand to `senpi-strategy-discover` with the edge as `--theme`. Two things carry across and are
the reason this handoff is worth more than opening discover cold: **the edge** (what to search for)
and **the leaks** (what the strategy has to fix — the exits, the sizing rule, the maker-first entry).
Name the leak the template closes; a reader who was just told they hold losers 29.7x longer than
winners should hear which candidate takes that decision away from them.

## Resilience

The engine fails open: every optional layer (rank, cohort, candles, Senpi) degrades to a line under
`Notes:`; the trade-level analysis needs only the public fills. An address with no perp activity in the
window and no open positions returns an error document — say "nothing to read here yet" and offer the
new-trader path (`senpi-strategy-discover`). A malformed address returns exit 2 with the reason.
Public-API rate limits (HTTP 429) are retried with backoff; a second run inside 10 minutes is served from
the cache (`--fresh` to refetch).

## No address given — find them some

"Run AI quant on any Hyperliquid wallet", "find traders for me to analyze with AI quant", or a bare
"run AI quant" with no `0x…`. **Do not guess an address and do not answer from memory.** Ask the one
question that narrows it, then hand them a short list.

The question is size first — a $9k book and a $9M book teach different lessons — then style:

> Happy to. Two things and I'll pull a list: **how big a book** do you want to read, and **what
> kind of trader**?
>
> Size: **$5k–10k · $10k–25k · $25k–100k · $100k–1M · whales ($1M+)**
> Kind: **this week's winners** · **the ones who've held up over a month** · or **this week's worst**
> — a losing book is often the more instructive read, and the desk prices it the same way.
>
> Or paste any address and I'll just run it.

Then:

- `desk.py --find 25k-100k --find-window week` — best in the band right now
- `desk.py --find whales --find-window month` — the ones who held up over a month
- `desk.py --find 10k-25k --find-losers` — this week's worst, often the instructive read
- `desk.py --find 100k-1m --find-window allTime` — durable mid-size books
- `desk.py --find 5k-10k` — retail-sized, this week

Bands: `5k-10k` · `10k-25k` · `25k-100k` · `100k-1m` · `whales`.

It returns JSON candidates — address, account value, P&L, ROI, volume, turnover. **Relay them as a
short numbered list with the numbers**, so the reader picks on evidence rather than on your summary,
then run the desk on whichever they choose. Three to five is a list; eight is a wall.

Never present a candidate as a recommendation to copy or follow — it is a book to READ. Vetting a
trader to mirror is `senpi-trader-research`.

## Running a batch — sequentially

One desk makes 100-200 reads against a per-IP weight bucket. **Run wallets one at a time.** Seven in
parallel loses one or two runs to HTTP 429 on an essential read however long the backoff is: the
budget is now a full refill window with jitter, and it still only gets 6 of 7 through. A lost run
fails loudly with the read that died, so nothing silently ships on partial data — but it is a rerun
you did not need.

## Install — the whole `scripts/` directory is required

`desk.py` imports `addresses.py`, `deep.py`, `followups.py`, `hl_api.py`, `market.py`, `metrics.py`,
`opportunities.py`, `render.py`, `roundtrips.py`, `score.py`, `senpi_history.py`, `smart_money.py`,
`strategy_read.py`, `taxonomy.py`, `timing.py` and `voice.py`, plus the vendored `mcp_client.py` (used
only when `SENPI_AUTH_TOKEN` is set). Copy the whole directory — a partial copy fails at import, not
at runtime. Stdlib only, Python ≥ 3.9. Fixture-driven tests in `tests/`.

## Skill attribution

This skill creates no strategy wallet and carries no attribution; strategies it hands off are attributed
by the skill that deploys them (`senpi-strategy-ops`).
