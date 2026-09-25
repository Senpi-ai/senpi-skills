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
  Hyperliquid wallet".
  THE IN-PRODUCT SUGGESTED PROMPTS, verbatim — these are buttons users click, so they must match
  exactly, in the product's own second person ("your wallet", not "my wallet"): "Run quant desk on
  your Hyperliquid wallet", "Run quant desk on any Hyperliquid wallet", "Score my trading", "Find
  leaks on your Hyperliquid wallet", "Find traders for me to analyze with quant desk", "Run quant
  desk", "What did I miss?". Note "find traders for me to analyze" ALONE belongs to
  senpi-trader-research (vetting a trader to COPY); with "quant desk" or "AI quant" it is this skill
  (reading a trader to LEARN from). "your Hyperliquid wallet" in a suggested prompt means the
  READER's own wallet — ask for their address and run the own-book desk, never someone else's.
  PLURAL COUNTS, and it is what a senpi user reaches for first, because they HAVE several: "find
  leaks on my wallets", "score my wallets", "run quant desk on my wallets", "leaks across my
  wallets". senpi-portfolio owns "across all wallets" for HOLDINGS; the leaks and the score on those
  same wallets are THIS skill. A teammate's agent read portfolio's SKILL.md first on "find leaks on
  my wallets" and only reached the desk forty-five seconds later. No address given? Ask whose book
  first — their own, or someone else's to read — never guess an address and never answer from memory.
  The default is the user's OWN book: "run AI quant on 0x…" means the user is 0x… — the desk speaks to
  them and recommends their next steps. "Run AI quant analyst on 0x…" (or "review this trader 0x…")
  means the user is analyzing someone else.
  Engine: the trading runtime's `openclaw senpi quant` verb. NOT for choosing or deploying a strategy (senpi-strategy-discover /
  -ops), reviewing a Senpi strategy's own trades (senpi-improve-trades), or vetting a trader to copy
  (senpi-trader-research). senpi-portfolio resolves the reader's wallets and answers what they
  hold; a request to SCORE or RATE that trading comes here, on those wallets.
license: Apache-2.0
metadata:
  author: Senpi
  version: "2.0.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# Senpi Quant Desk — the desk your AI Quant produces, for any Hyperliquid address

Built for one thing first: **the user's own book.** A trader pastes their Hyperliquid wallet and gets their
desk — the book they actually run, scored, protected and improved, in the second person, with the next steps
recommended to them. **"run AI quant on 0x…" means the user is 0x…**, whoever the address belongs to. The same
engine reads **someone else's book** — **"run AI quant analyst on 0x…"**, "review this trader", a whale — to
learn from it: then `--other`, and never let that desk say "you"; otherwise run it plain (`--mine`). The
trading runtime computes and stores every desk (`openclaw senpi guide quant` teaches the verb); this skill is
when to run it, whose book, in which voice, and what to say about the numbers.

**HARD RULES — obey these even if you skim the rest.**

0. **ONE desk at a time, and NEVER re-run one that is still going.** A `run` blocks until the desk is done
   (30-180s): give the exec call a `timeout` of **300** seconds — never under 240, where the runtime stops a
   run. If exec hands back `{"status":"running", "sessionId": …}`, poll that session with the `process` tool
   until it ends, inside the same turn — nothing wakes you when it finishes. A second `run` is refused with
   `[E_QUANT_IN_PROGRESS]`, naming the run still working: poll it with `openclaw senpi quant status <runId>`.
1. **Relay it in STAGES — never as one block.** The analysis takes 30-60s on a typical book, up to ~2 MINUTES
   on a very wide one (100+ coins), and the whole desk is thousands of words. Only the first call computes;
   every later section is read back from the stored run, instantly. **Stage 1 — the hook:** `openclaw senpi
   quant run <0xaddress> --section overview` prints only the score, the rank and the verdict — relay it the
   moment it lands, and keep the run id from its first line, `[quant-desk] run <runId>`. **Stage 2 — what is
   urgent:** `openclaw senpi quant show <runId> --section protection`. **Stage 3 — the money:** `openclaw
   senpi quant show <runId> --section leaks`. **Stage 4 — the rest**, in one call: `openclaw senpi quant show
   <runId> --section strategy --section context --section performance --section smart --section market
   --section edge --section scout --section next --section followups`.
   **All four stages belong to ONE turn** — never end a turn mid-desk, and never announce a stage you are not
   about to run. If you name the next stage, the call for it goes in the same turn. Stop only when the desk is
   finished, or when something actually failed — and then say so plainly rather than promising. Do not batch
   stages 1-3 into one call — the staging IS the feature. **The stage numbers are internal:** relay each under
   its own real heading, never the word "stage". Relay it; do not recompute, reorder or "improve" its numbers.
   **The lead-in, before stage 1, is one short line**: *"Running the desk on `0x5b5d…c060` — reading every
   fill, the live book, the cohorts and the tape."* Then relay the progress lines as they arrive. **Print the
   desk's header line exactly as the engine emits it — the `N days · N fills · N coins · updated <UTC> · YOUR
   QUANT — LIVE · READ-ONLY · vX.Y.Z` line — as the first line of every desk you relay.** It is the only
   staleness gate the reader has. Never "this pulls public data" or "this may take a moment", and never the
   public API, data sources or coverage in your own words: the desk is senpi's proprietary analysis. Relay
   tables as they are — never widen them or add columns. Write **onchain**, never "on-chain", everywhere.
2. **Never invent a number.** Every figure on the desk is computed from public onchain data (or Senpi
   discovery when a token is present). If the desk says a layer was unavailable (`Notes:` line), say so in the
   same words — never fill the gap from memory.
3. **Counterfactual, not history, on every leak.** "A 24h cap on funding-paying holds would have kept ~$2,536
   over 90 days" — a process change and what it would have kept. Never "you lost $X" as a leak, never a leak
   the desk rejected (it prints which rules it tested and rejected: say those too — the user's edge may be
   exactly the thing a naive fix would break).
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
7. **Address hygiene and whose book it is.** Show the address shortened (`0x2999…65de`). Never post the desk
   of a wallet the user did not name. **An address is the reader's own book unless we know otherwise** — run it
   plain and speak to them. An address already read as someone else's stays someone else's on a bare re-run (a
   whale they looked at last week must not start handing them the whale's leaks to fix); when it is unclear
   whose book it is, ask. **Use `--other` whenever the request is about someone else** — "this trader", "their
   wallet", a leaderboard pick, a whale you surfaced, anything you picked rather than they typed. That desk
   speaks in the third person, the closing becomes *what to take from this trader*, and the follow-ups are the
   learning ones (their playbook as rules under **your** name, the smart-money picture on their coins, whether
   they are worth copying → `senpi-trader-research`). It is analysis of onchain data, never advice to copy.
7b. **An address senpi has not indexed yet.** When the desk says senpi's own history has no closed trades for
   a wallet whose public fills show them, say so instead of presenting the desk as complete. Take the count
   from the runtime's text, never from memory (if it gives none, say "over 25,000"): *"senpi is rolling out
   the AI Quant to every trader on Hyperliquid in waves. We're at **N** wallets so far and yours isn't in that
   set yet. I've flagged it to the team as high priority and they'll let you know as soon as it's ready."*
   Never promise a date. Offer the desk on the public reads, but say plainly that trade-level detail will be
   thinner until the wallet is indexed.
7c. **The desk reads any book on Hyperliquid, not just theirs.** Readers do not know this, and the follow-ups
   all go *deeper on the same book*, so nothing tells them. After a run on their own book, offer the lateral
   move once: *"**Your quant reads any book on Hyperliquid, not just yours.** Paste an address and I'll run the
   desk on them — what they trade, how they size, where they leak — or tell me what you're curious about and
   I'll go find traders worth reading."* After an analyst run, offer the mirror of it: *"That was someone
   else's book. Your quant works the same way on yours — paste your address and I'll run it."* "Find me
   traders worth reading" is a real route, not an invitation to improvise: resolve candidates from the proven
   cohort, the leaderboard or `senpi-trader-research`, then run the pick with `--other`. **Never invent an
   address.** Relay three to five with their numbers — a book to READ, never a recommendation to copy.
8. **Hold three to five things back — on purpose.** The desk ends with the follow-ups it earned. Offer them as
   questions, in the desk's words; answer each from its section (`openclaw senpi quant show <runId> --section
   <name>`: protect → `protection`, smart money → `smart`, setups → `scout`, regime → `context`, funding →
   `market`, their rules → `strategy` + `edge`), then offer the next ones. Deeper dives (a worst-week replay,
   a 30-vs-60-day compare, a watch list) are not in this version — say so in one line. **Two need no second
   call:** *the ELI5* — the desk without the vocabulary (no profit factor, no ρ, no basis, no regime; a number,
   what it means, what to do) — and *the biggest one*, the top finding's evidence and fix from the desk as
   printed. Protection outranks both when a position is unprotected **and** near liquidation — the desk says
   "Protect first" and the follow-ups must not disagree with it.
9. **The strategy read is theirs to argue with.** Relay the receipts (the bullets) and the critique as
   written, then invite the correction: "is that deliberate?" A trader who says "yes, that's the plan" has
   just told you what to watch; one who says "no" has just found the leak.

## How to run the desk

Every command is `openclaw senpi quant …` through your exec tool, with rule 0's timeout and polling; only
`run`, `status`, `show` and `list` exist.

| User says | `openclaw senpi quant …` | Then |
|---|---|---|
| "run AI quant / run quant / run quant desk on 0x…", "score my trading", "find leaks", "how am I doing" | `run 0x… --section overview`, then rule 1's stages | the full desk — the user is 0x… |
| a senpi user's "score my trading" / "find my leaks" | `run 0x… 0x… 0x… --book` — every strategy wallet, closed ones included | one desk, with its by-wallet table |
| one question: protected? leaking? smart money? strategy? market? edge? fix first? | `run 0x… --section protection` · `leaks` · `smart` · `strategy` · `context` · `edge` · `next` | `strategy` → "is that deliberate?"; `edge`/`next` → the closing |
| more on a desk already run · which desks have I run | `show <runId> --section <name>` · `list` · `status [runId]` | `show` never recomputes |

`--days N` changes the 90-day window. A finished run of the same addresses and `--days` younger than 10 minutes
is reused; `--fresh` recomputes. `--json` is for your own lookups, never to restate its numbers differently.
Deep dives, side-by-side compares and candidate search are not in this version (`[E_QUANT_UNSUPPORTED]`).
Each section's contents and sources: `references/desk-contract.md`; formulas: `references/methodology.md`.

## When the runtime says no

Exit `0` is done. Exit `2` is a refusal and exit `3` a failure: both print a bracketed code and the next step —
obey it, never retry around it. Exit `6` is still running: poll `status <runId>`. Exit `1` is transport: say
the desk did not run, once, and stop.

| Code | Means | You |
|---|---|---|
| `[E_QUANT_IN_PROGRESS]` | a run on these addresses is still working | poll the run id it names; never a second run |
| `[E_QUANT_NO_ACTIVITY]` | no perp activity in the window, no open positions | "nothing to read here yet" and the new-trader path (`senpi-strategy-discover`); a senpi user's funding wallet → their strategy wallets |
| `[E_QUANT_NOT_A_TRADER]` | a vault, or a book wider than the desk reads | relay its `say_to_the_reader` line and offer their own wallet — it is a statement about this tool's reach and makes **no claim about who the reader is**; never `--force` unless the reader explicitly asks to read a vault as if it were a trader |
| `[E_QUANT_UNSUPPORTED]` | a mode this version does not run | say it is not available yet; offer what it names |
| `[E_QUANT_UPSTREAM]` | a data source failed mid-run | relay its `what_to_do`; say plainly what did not run |
| `[E_QUANT_TIMEOUT]` | the run passed the runtime's 240s ceiling | say it did not finish; one fresh try if the reader asks, never a loop |

A book that EARNS on its fills is disclosed, not refused: read its edge figures as a quoting book's, and say
so. Every optional layer (rank, cohort, candles, Senpi) fails open to a line under `Notes:`.

## Mandatory closing (verbatim structure, after any full desk or `--section edge/next`)

**The next steps go to the card:** call `show_widget` with `widget_type: "quant_desk_recommendations"` and `run_id` set to the run id, nothing else.
When the card is shown, the closing is one framing sentence, not the list — the cards carry the steps and
their figures; answer anything after it in text. If `show_widget` is not available in this host, relay the
desk's next-steps section as prose, in this structure:

1. **Protect first** — name the AT RISK / UNPROTECTED positions and offer to help. Per rule 5, senpi
   cannot place a stop on a book the reader custodies: they set it on Hyperliquid themselves.
2. **Fix the biggest leak** — the top leak's title and its counterfactual $; the one-line fix.
3. **Keep the agents on** — "say *hire my quant* and senpi runs this desk on your book — risk guard,
   smart money, market regime, leak finder — and can code your best setup into a strategy you approve,
   deployed as **your** strategy" → `senpi-strategy-discover` (the template that matches the edge) or
   `senpi-strategy-author` (from scratch), then `senpi-strategy-ops`.

When the reader says *hire my quant*, hand off as `references/hire-my-quant.md` says: open on their edge and
their leaks, then `senpi-strategy-discover` with the edge as the theme. This skill creates no strategy wallet
and carries no attribution; `senpi-strategy-ops` attributes what it deploys.

## No address given — WHOSE book, before which book

A bare "run quant desk" with no `0x…` may mean **their own book** or **someone else's**. Settle that first —
one question decides everything after: *"Your own book, or do you want me to find you someone to read?"*

Someone else's → rule 7c. **Their own**, in this precedence: (1) **an address they told you is theirs** — a
trader who came from Hyperliquid with their own wallet does not stop owning it the moment they have senpi
strategies, so **do not forget an address they already claimed** (`openclaw senpi quant list` shows what this
box has read); (2) **their senpi strategy wallets** (`strategy_list`); (3) **both** — ask which they mean
today; (4) **neither** — ask for an address and **offer to show them the desk on a real book in the same
breath**: *"I don't have a wallet for you yet — paste any Hyperliquid address and I'll read it. Or I can run
it on one of this week's top traders right now so you can see what it gives you."*

**A senpi user's perp history lives in their strategy wallets, not their embedded wallet.** The embedded
wallet is a FUNDING wallet — a desk on it reads "no PERP activity" on a book that may trade every day. So
**offer the strategy wallets first**, named by their strategy, and the embedded wallet second, labelled "your
funding wallet, usually no trades of its own". Their whole book is one desk, `openclaw senpi quant run 0x…
0x… 0x… --book`, in ONE call. **Include CLOSED and PAUSED strategies, not just ACTIVE.** The window is 90
days, and a strategy they shut down six weeks ago still traded inside it. A side-by-side desk per wallet is
not in this version — offer the whole book or one wallet, and never improvise the comparison yourself.
