---
name: senpi-portfolio
description: >-
  Analyze the user's portfolio, strategies, positions, and trades across all wallets — main embedded
  wallet, strategy sub-wallets, deployed vs idle — with real-time balances and real analysis, not a flat
  dump. Leads at the STRATEGY level: each strategy judged against its OWN mandate (is it doing its job?),
  with positions as evidence. Use this skill FIRST for ANY portfolio / strategies / positions / balances
  / PnL / trade-history question, BEFORE any raw strategy_get_clearinghouse_state / account_get_portfolio
  / strategy_list MCP call. Use for "analyze my strategies", "how are my strategies doing", "analyze my
  portfolio", "how am I doing", "show my positions", "balance across all wallets", "how much is idle", and
  "are my open positions protected? / do they have a stop-loss?", "my saved wallet", "the wallets I added", "my MetaMask
  wallet" (read-only balances and positions of a wallet the user added in Your wallets), and "tell me about my strategies and
  their DSL / what tier are my positions in?", and "what happened to my closed [asset] position / did my
  trade actually go through / do I still hold X" — the authority for position facts, OPEN and CLOSED, which
  come from a fresh engine read, never from memory or a raw order response. A hidden engine (scripts/portfolio.py)
  does the multi-wallet pull and taxonomy; you narrate. Requires a USER-scoped Senpi token.
  Asked to SCORE, RATE or GRADE their trading — "score my trading", "rate my trading", "find leaks
  on my wallet" — run **quant-desk** on the wallets this skill just resolved; it returns a quant
  score, the six dimensions behind it and leaks priced in dollars. Everything else about holdings,
  strategies, positions and closed-position facts stays here.
license: Apache-2.0
metadata:
  author: Senpi
  version: "1.36.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# Senpi Portfolio — real-time, all-wallet analysis

## Follow-ups first — the router

**Follow-ups: answer only what is new.** Re-run the one step the question needs (numbers always come
from a fresh read, never an earlier one), answer that question, and never re-send the wallet list, the
book total, or verdicts and figures already shown unless the user asks for them again.

| The ask | Step to run | Read first |
|---|---|---|
| *"how much idle / where's my money / balance across wallets / grand total / my wallets"*, a wallet they added | `money` | "One wallet list" below |
| *"are my strategies protected? / do they have a stop-loss? / how are my strategies doing / analyze my strategies / what's their DSL / mandate"* | `strategies` | `references/dsl.md` (protection), `references/mandate.md` (verdicts), `references/runtime-health.md` (liveness) |
| "is it running / health check / degraded / not running / could not verify / it looks stuck / restart it" | `strategies` | `references/runtime-health.md` |
| "why hasn't it traded / why didn't it open X / no signals" | `strategies` | `references/why-not-trading.md` |
| a copy-trade (`strategy_kind: "mirror"`) | `strategies` | `references/mirror-strategies.md` |
| *"analyze my positions / my positions vs the market / net exposure / concentration / idle drag"* | `positions` | `references/analysis-framework.md` |
| *"analyze my portfolio / how am I doing"* (the full read) | `money` → `strategies` → `positions`, narrating between | `references/output-contract.md`, `references/runtime-health.md` (liveness) |
| "what happened to my closed X / did my trade go through / do I still hold X" | `strategies` (`closed`), `positions` | "Source of truth" below |
| "go deeper on X" (the deep-dive answer) | a strategy: `strategies`; a wallet they added: its row; the Senpi main wallet: `embedded_wallet` | — |
| add / change / remove a stop or DSL | nothing here — this skill reads; offer it, then `senpi-trade` | "This skill reads" below |
| score / rate / grade my trading | `quant-desk` on these wallets | — |
| "where am I leaking / did a stop fail / exit quality", trade history | `senpi-improve-trades` | — |
| what a JSON field means | — | `references/output-fields.md` |
| a read failed: `meta.degraded`, `meta.warnings`, `reconciles: false`, a `null` figure | re-run that step | `references/output-contract.md` ("Resilience") |
| run this on a schedule | — | say the cost first: every firing is a full model call; at most twice a day, after a yes ("Run it in steps") |
| close / pause / keep / top up ONE wallet of a strategy | — | the no-no below: whole strategy only |
| a yes to CTA 1 / CTA 2, or a digit (after the CTAs: that CTA; after the idle menu: that option), or a question about a menu option or its numbers | the levers / the ready options | `references/output-contract.md` (the menu) |

**Still true on every follow-up** (in full further down):
- **A failed read is not $0 or flat.** App-scoped token → can't read the account; failed clearinghouse
  read → positions may be incomplete; realized PnL that didn't load → *unavailable*, never `$0`;
  `totals.reconciles == false` → stop, re-run, then trust per-wallet figures. A full read never skips a
  wallet or the CTAs. A `timeout` open may have filled: check positions, never suggest a retry.
  ("Resilience")
- **Acting on a CTA:** Confirm before any change; never trade unprompted. Propose; never deploy without
  confirmation. CTA 2: -15% SL is 15% ROE; up to 10x, never a flat 10x; say rotations, never "pumps".
  (The menu and its numbers: `references/output-contract.md`.)
- **There is no restart verb; close → redeploy is a last resort, asked first.** ("Liveness")

> **Source of truth for position facts — read before you answer, even mid-trade.** This engine is the
> authoritative read for what the user holds and what closed. **Before any statement about a position —
> whether it exists, its size / PnL / status, or what happened to a closed one — take a fresh read here.**
> Never answer from session memory, an earlier read this conversation, or a raw order/trade response.
> These rules hold even inside a trading flow:
> - **A successful open/close order is NOT proof of the resulting position.** After you place or close a
>   trade, confirm the resulting state here before telling the user what they hold.
> - **"What happened to my [asset] / my closed trades"** → read the authoritative CLOSED record
>   (`closed.recent[]` / `closed.realized_pnl` here, or hand to `senpi-improve-trades` for why-it-closed).
>   Never narrate a closed-position story from memory.
> - **Quote a close with its UTC date and time from `closed_at_utc`, never from a raw epoch** such as
>   `closed_time`.
> - **The closed record can arrive hours after a close.** A close the user saw may not be in `closed` yet
>   (`closed_record_newest_utc` is the newest close it holds; a re-read does not skip the wait). Say it
>   has not reached trade history yet and show the live state from this read instead — open `positions[]`
>   and `account_value`. Never present older closes as today's, and never invent the missing P&L.
> - **"What did the trader do?" on a copy-trade (`mirror`) means the COPIED trader, never the user's wallet.**
>   `mirror_of` / `traderAddress` are masked and discovery rejects them: ask for the full address, then
>   read `discovery_get_trader_state` / `discovery_get_trader_history` on it.
> - **"In profit" on an open position is unrealized.** Say it is unrealized, and do not forecast the close.

## One wallet list — every wallet first-class

**A Senpi strategy is one row with all its wallets.** One package is one strategy: its instances are
its wallets, grouped by the package they were deployed under (`skill_name`); a wallet with no package
stamp is its own row.

Every wallet the user has is ONE list, `book.wallets`, ordered by value: the Senpi main wallet, each
Senpi strategy (one row across ALL its wallets — `strategy_wallets[]` are its sleeves, never rows of
their own) and each wallet the user added in Your wallets. The engine sorts it (`book.order_rule`:
largest first; ties by label, then address; a wallet that couldn't load last). **Keep the engine's
order and never re-section by origin** — no Senpi block followed by a saved-wallets block, no saved
wallet appended after the money map. Origin is the kind column (`kind`: `managed` = Senpi runs it,
`read-only` = a wallet the user added), never a rank and never a heading.

- **One table:** label, value (`value_usd`), open positions, protection, PnL (`unrealized_pnl_usd`,
  open PnL), kind. Protection keeps its two words: a strategy row's `protected` + `runtime_health` (the
  runtime exit — read them by the DSL rules below); a read-only row's `protection` counts (live stops
  on the exchange, `FULL` / `PARTIAL` / `NONE`). The Senpi main wallet holds cash only
  (`not_applicable`): "—" in the positions, protection and PnL columns.
- **One book total — quote `book.totals.line`.** It reads: the book total, then the managed subtotal
  with the money map's three buckets inside it, then the read-only subtotal.
  `book.totals.read_only_usd` is the wallets the user added — never idle, never deployable, never in the
  managed subtotal. `book.totals.excludes_note` names every wallet that couldn't load and every coin
  without a price; it is part of the total — never quote the total without it.
  Its unknowns, and every row's: "One wallet list — row details" below.

The rest of this section is about the **read-only rows** — the user's **saved wallets**, Hyperliquid
wallets they added in Your wallets by pasting the address, and trade by hand. A saved wallet is their
claim: call them "your wallets" or "the wallets you added", and never imply Senpi checked who controls
them. Their raw reads: "row details" below.

- **Quote the access line verbatim** — every wallet carries it as `access`, and it is the whole answer to
  "can you trade it / close it / set a stop on it":
  > Read-only. Senpi can analyze this wallet. It cannot place, change or cancel orders on it.
- **Never Senpi money.** A saved wallet's value is never in `grand_total_usd`, never idle, never
  deployed, and never part of `reconciles` — it is a `read-only` row in the one list and part of
  `book.totals.read_only_usd` only. CTA 2 ("put the idle to work") never counts it.
- **No write suggestions on these wallets** — no `close.py`, redeploy, `edit_position`,
  `close_position`, `strategy_*` or `ratchet_stop_*`. CTA 1 applies to Senpi wallets only.
- **"Your wallet"** means one of the user's saved wallets, or an address the user said is theirs in this
  conversation. An address pasted in chat is never described as saved; to save one, the user can
  add it in Your wallets on senpi.ai (web). If a saved wallet isn't theirs (or they no longer want it
  read), they can remove it in Your wallets on senpi.ai (web).
- **`meta.no_strategy_path`** (saved wallets, no Senpi strategy): give the one-list read and skip the
  strategy verdict. Never pitch a strategy; the deep-dive question still comes first when offered, and
  this replaces CTA 1 and CTA 2:
  > **Want me to review the trades on it, or score it on the quant desk?**

**Closing — the deep-dive question comes first** (the two CTAs follow, in "Mandatory closing").

**First, when `book.deep_dive.offer` is true** (more than one wallet with something to go deeper on —
any user, with or without a Senpi strategy), ask the deep-dive question and name the wallets in
`book.deep_dive.order`, largest first. The order holds only wallets with a non-zero value, an open
position, or a value that couldn't load: an empty $0 Senpi main wallet is a row of the list, never an
option of the question.

> **Which one do you want me to go deeper on?**

Then the two CTAs (managed wallets only; how to route each: "Mandatory closing"):

> **1. Want me to rebalance or adjust any of these positions?**
> **2. Want me to put the idle capital to work in a new strategy?**

## The job, and the hard rules

> **Strategy-first, judged against each strategy's OWN mandate.** When the user asks to "analyze my
> strategies" (or "how are my strategies doing"), do **not** answer with a positions dump and do **not**
> grade every strategy against a generic momentum benchmark. Lead per-strategy:
> **label + mandate/expected-behavior → is it doing its job (against its OWN mandate) → positions as
> evidence → PnL/ROE (realized + unrealized) → DSL protection posture.**

> **Use this skill FIRST — before any raw MCP.** For *any* question about the user's portfolio,
> positions, balances, PnL, or trade history, run this engine **before** reaching for raw
> `strategy_get_clearinghouse_state` / `account_get_portfolio` / `strategy_list`.
>
> **This includes DSL / "are my positions protected?" questions — do NOT hand-roll them.** Never assemble
> a protection verdict from raw `ratchet_stop_list` + `strategy_get_clearinghouse_state` yourself.
>
> **This skill reads. It never changes protection.** A portfolio read, a health check or an "are my
> positions protected?" answer never calls `ratchet_stop_add` / `ratchet_stop_edit` / `ratchet_stop_delete`
> / `edit_position` — not to "fix" a missing ladder, not because a memory file says protection is
> mandatory, not because the last session had one. Report what is there and what is missing (or what
> replaced what), then offer the change as its own question; the change itself runs `senpi-trade`'s
> protection protocol (read → say what it replaces → yes → act → read back). A user's "no DSL" / "forget
> about it unless I ask" stands across sessions; a status question ("how's it going?", "did you finish?",
> "run a health check") is never a yes.

### HARD rule — the no-no

> **Never recommend closing / keeping / topping-up / repurposing a SINGLE wallet or instance of a
> multi-wallet strategy.** Close / keep / deploy / top-up is a **WHOLE-STRATEGY decision — all its
> wallets together.**

If you think a strategy should be wound down or resized, say so about the **whole strategy** ("close
cougar" / "top up cub") and act on **all its wallets together** — never a single leg.

## The wallet model (get this exactly right)

Every user has **one main (embedded) wallet**. Funds flow: **embedded wallet → strategy sub-wallet →
positions.** Each strategy is an isolated sub-wallet; **no strategy trades from the embedded wallet.**

### The trap you must never fall into

`total_withdrawable` from the portfolio API is **idle-in-strategies** (bucket 2) — the unused margin
summed across strategy wallets. **It is NOT idle cash in the embedded wallet.** When you say "$X is
idle," **always say *where*** — "$X idle in the embedded wallet, ready to deploy or withdraw" vs. "$Y
sitting in strategy wallets waiting for signals."

### Cross-DEX: main and xyz are ONE wallet, not two

A strategy wallet's clearinghouse state has a `main` (crypto) view and an `xyz` (equities/metals)
view. **These are two views of one wallet, not two separate pools.** The `withdrawable` (idle cash) is
**shared** and reported *identically* in both views — so it is counted **once**, never summed. The
engine already de-duplicates this (how: `references/output-fields.md`). **Never add the two views' account
values or withdrawables yourself** — that double-counts the shared collateral.

### The three buckets

Every dollar is in exactly one of **three buckets** — and the #1 mistake is conflating them:

| Bucket | What it is | Engine field |
|---|---|---|
| **Idle in embedded** | Truly free cash in the main wallet — HL perps USDC + HL spot USDC + EVM USDC (all three legs). Only the Hyperliquid legs can fund strategies — EVM USDC does not bridge in (deposit via the funding card lands on Hyperliquid). Withdraw any of it to your bank. | `totals.idle_in_embedded` |
| **Idle in strategies** | Free margin sitting *inside* a strategy wallet, not yet in a position — waiting for a signal. | `totals.idle_in_strategies` |
| **Deployed in positions** | Margin actively backing open trades. | `totals.deployed_in_positions` |

**`grand_total = idle_in_embedded + idle_in_strategies + deployed_in_positions`.**

These three buckets are the **managed** money — Senpi's. The wallets the user added sit outside them:
they are `read-only` rows in the one wallet list and the read-only subtotal beside `grand_total_usd`
(see "One wallet list — every wallet first-class").

## A strategy is ALL its wallets (present + reason at `strategy_groups[]`)

**This is the most important rule in this skill.** A single strategy can deploy as **MULTIPLE instances
on SEPARATE wallets** — **ox** = `core`+`ballast` (risk-parity), **cougar** = `long`+`short`
(market-neutral), **cub** = `long`+`short`+`preipo` (multi-sleeve dispersion). `strategy_list` returns
**each instance/wallet as its own row**, so the raw list looks like several separate strategies. **It is
not.** A multi-wallet strategy (long+short, core+ballast, multi-sleeve) is **ONE strategy across N
wallets/instances** — the wallets are the *legs of one design*, not independent bets.

**Lead and reason at the `strategy_groups[]` level, not `strategies[]`.** The engine re-unites the
per-wallet rows into **`strategy_groups[]`** — one entry per real strategy, with `is_multi_wallet`,
`instances[]` (the per-wallet detail), and `totals` summed across every wallet. **Present each group as
one strategy**; never present its wallets/instances as separate strategies. (`strategies[]` is still
there for per-wallet detail and the bucket math — but the *unit of analysis and recommendation* is the
group.) When `meta.has_multi_wallet_strategy` is true, at least one strategy spans multiple wallets —
be especially careful.

### A flat/empty instance of a multi-wallet strategy is its OTHER sleeve, waiting for a signal

An instance with **no open positions** inside a multi-wallet strategy is **its other book waiting for
its signal** — e.g. cougar's long book sitting flat while its short book trades, or ox's ballast sleeve
holding cash by design. The engine names these in `strategy_groups[].flat_instances`. **It is NOT idle
capital to redeploy elsewhere, and never "dead money."** Only truly-free
`idle_in_embedded` (and, with care, a *whole* strategy's idle) is redeployable — a flat sleeve of a
live multi-wallet strategy is not.

## Liveness and "why hasn't it traded?" — the rules that always apply

The detail is in `references/why-not-trading.md` and `references/runtime-health.md`; read the one the
question needs before you answer. These hold always:

- Every evaluated signal carries a `senpi.outcome.reason_code`, so **say which one, how many, and whether
  the user needs to do anything** — never "I'm not sure why." **Two rules before you answer.** Count the
  signals rather than describing one, and name the assets that were skipped, because "3 eligible entries
  were skipped" is the part the user actually feels. And lead with whether they need to act: for most of
  these the honest answer is *nothing*, and saying so is the useful reply.
- **Never call it healthy on the strength of `health=healthy`.**
- `status: ACTIVE` only means the strategy *record* exists and is funded — it does **not** mean a runtime
  is actually running it. For `not_running`: Report it as **⛔ NOT RUNNING / UNPROTECTED — funded but no
  runtime; no scanner, no DSL, no guardrails**, and tell the user to redeploy it via `senpi-strategy-ops`.
  **Never** call a `not_running` strategy "alive and waiting," "scanner is live," or "DSL-protected" —
  that is a false all-clear.
- `running_blind: true` is a third, previously-invisible state: the runtime IS registered and ticking, but its
  entry scanners never wired (`running — NO ENTRY SCANNERS`), so it **cannot produce entry signals** — report
  **⚠ RUNNING — NO ENTRY SCANNERS**, not a clean "running."
- When `runtime_registered` (or `not_running` / `running_blind`) is `null`, the registry read did not
  answer — say **"could not verify on this host,"** never "running" and never "not running." This is the
  honest bar: **only `live` means "confirmed working."** `unverified` (the registry read itself failed):
  Say **"could not verify on this host"** — never "protected," "not protected," "running," or "not
  running." **`null` is not `false`.**
- Every runtime field `null` on every strategy, with a `meta.warnings` line naming `openclaw senpi runtime
  list --json`: **That whole-fleet pattern means the runtime on this box is too old for this skill's
  registry read, not that the strategies are broken.** Say so in those words, quote the warning, and do
  not diagnose the strategies from it: no strategy may be called running, not-running, protected or
  unprotected off that run. The fix is a runtime upgrade on the box, not a redeploy of the strategies.
- `recovering` (one errored scan): **Do not warn on it.** Do not put a ⚠ on the strategy, and do not send
  the user to a diagnostic.
- `degraded` (two or more errored scans, or `running_blind`): This is the one that always deserved the
  warning. Say **"⚠ runtime degraded — running but not healthy,"** not a clean all-clear.
- `unknown`: Say **"runtime liveness unverified — not confirmed running"** — never upgrade to
  "healthy/protected" or downgrade to "broken." A runtime *process* that exists (`status: running`) is not
  a health verdict and never reaches `live` on its own.
- Corroborate before you assert (the grid: `references/runtime-health.md`). `live`, trading: **They
  agree.** Clean. Say so. `degraded`, book live:
  **They disagree.** The last scans errored; the book is live and managed. Say *"running — some scans
  errored"*, never *"your strategy is broken."* Confirm with `status.py <id>` before going further.
  `live`, not trading: **They disagree.** Do **not** give a clean all-clear — a scanner can read `healthy`
  and still be blind. Say it is running but has not found a trade, and use "No signals at all" in
  `why-not-trading.md`. **When the two disagree, say which one you are trusting and why.** If the money
  is moving, that is the headline and the field is the footnote — do not report the field and bury the
  evidence.
- `hours_since_last_close` / `meta.quiet_strategies`: **Say "has not CLOSED a trade in Xh", never "has not
  traded"**: a position carries no open time, so an entry taken since that close is invisible here. Quiet
  while **holding** means fully allocated, not stuck — check slots and entry gates first, and note every
  guard rail halts ENTRIES only, so a gate counter is moot while every slot is full.
- **`risk_pause`** (beside `runtime_health`, never instead of it) — the runtime's OWN entry gate says
  `CLOSED` or `COOLDOWN`: the strategy is **healthy and paused by its own rule** (daily entry cap, daily
  loss halt, drawdown halt, a cooldown). Name the gate and quote its `reason` verbatim ("Max Entries/Day —
  Max entries: 4/4 entries today"), say when it lets go (`reset`: daily gates at 00:00 UTC, cooldowns on
  their own; a latched drawdown halt with `drawdown_reset_on_day_rollover: false` never lets go on its own —
  it waits for the user's decision, and you never clear it or resume entries without their explicit
  approval), and that nothing is broken. **Never send the user to redeploy over it** — a fresh wallet
  market-exits the book and starts the same gate again from zero.
- `status.py`: A thorough health check does not stop at the verdict: for a strategy that is `not_running`,
  `degraded` or `unknown` — **never for `recovering`** —
  **`python3 senpi-strategy-ops/scripts/status.py <id>`** re-asks the runtime directly and returns its
  verdict **beside a position count**, which is the corroboration above. Run it yourself and give the
  answer. **It is your tool, not a step you hand the user** — never write "worth running …" into a portfolio answer; either run it, or say what you know
  without it. **Mind the vocabulary:** `status.py` reports the engine's raw words, and they do not line up
  with this skill's. Its **`degraded`** is this skill's **`recovering`** (one errored tick); its
  **`unhealthy`** is this skill's **`degraded`** (the real fault). Reference the right tool to *confirm* —
  never re-derive its analysis here.
- **Before the ladder: if the row carries `runtime_reason`, say it.** Quote it in those words: it is why
  the scanner has not traded. Call it a crash loop only when it says crashes. No `runtime_reason` means the
  runtime gave none — never invent one; the ladder finds it.
- **When it IS genuinely broken, act — don't hand the user a to-do.** For `degraded` corroborated by no
  activity, or `not_running`, work the ladder yourself and report what you found (the ladder:
  `references/runtime-health.md`). Its step 3: **Re-run the deploy** — a deploy interrupted by a gateway
  restart does not resume on its own, and re-running reconciles rather than duplicating. This is the fix
  for `not_running` after a broken deploy. Only the last step touches their money, and only that one needs
  their consent: **`close.py` → redeploy** — **last resort, and ask first.** It is a market exit of every open
  position and a fresh wallet. Never reach for it to clear an error you have not diagnosed. **There is no
  restart verb.**
- No signals, scanner healthy: **Nothing qualified.** The entry bar has not been met — name it, never
  "being selective": the binding gate, its threshold and the universe count from its `runtime.yaml`
  (`senpi-strategy-ops` `references/liveness-verification.md`, "Healthy, live — and it has never traded").
- **Do not offer "close and relaunch" for `withdrawable_unavailable`.** Wait, then support. Do not send
  anyone to redeploy over a row marked "no". ("do they act?" in `references/why-not-trading.md`.)
- `position_open_failed` (the exchange rejected the order): Give the exchange's own message, which rides
  the outcome.
- `timeout`: **Not a rejection — the order may have filled.** Check live positions before saying anything,
  and never suggest retrying until you have.
- A copy-trade (`strategy_kind: "mirror"`) has no runtime by design — none of the liveness verdicts apply
  to it. Its `name` is **"copy of `mirror_of`"** — never call it "unnamed." High leverage or a lopsided
  book is worth *surfacing* ("this copies `mirror_of` at 20x — a sharp adverse move liquidates fast; it has
  [no] strategy-level stop"), but the fix is **never** a DSL or a per-position stop. To add/tighten a
  downside cap, take profit, or size down → set `stopLossPercentage` / `takeProfitPercentage` or lower
  `mirrorMultiplier` **via `senpi-trade`** (it wraps `strategy_update`). To stop copying → unsubscribe /
  close the mirror **via `senpi-trade`**.

> **NEVER, for a mirror:** add a DSL / ratchet; set a stop via `edit_position` on its positions; "redeploy it
> via `senpi-strategy-ops`"; call it "running unprotected / not running"; or call it "redundant with strategy X,
> close it." A mirror is an intentional copy of a trader, judged on the trader + the multiplier + its
> strategy-level SL/TP.

**Fees are read, never estimated.** A P&L claim about a strategy — "it is up", "it is not losing" —
quotes the wallet's **signed** fees from its fills (`totalFees` / the closed-trade history), net of
them, with the fee figure beside the PnL. If the fee read fails, say the number is gross and why.

**Paper is not live.** A `senpi validate` tick, a scanner signal, a simulation or an estimate is not a
trade; only a fill on the strategy wallet (`positions`, `closed`) is. Never narrate a shadow run as
"live", "running" or "a trade".

## Judge each strategy against its OWN mandate — not a momentum benchmark

This is the core of the analysis. Every strategy was deployed to do a *specific* job. "Is it working?"
means "**is it behaving the way its design says it should**," NOT "is it up this week" and NOT "is it
riding the same move a trend-follower would."

**Get the mandate first, then judge.** Before you call any strategy good or bad, know what it was *for* —
and get that from the **source of truth, not memory.** The engine already does the lookup
(`strategies[].profile`, its `profile.description` read from the deployed `runtime.yaml`; its fields:
`references/mandate.md`). Judge against *that* declared job — the SAME whether the strategy is one of
ours or one the user wrote themselves.

**Lead the per-strategy read with `profile.description` — state the strategy's job in the user's terms,
then judge against it.**

- **Do not reconstruct the mandate from memory or from what the positions *look* like.** The deployed
  `runtime.yaml` is authoritative; a strategy's open book is *evidence about* whether it's on-mandate,
  never the definition of the mandate.

If `profile` is `null` (no registry entry AND not in the catalog — e.g. the registry was unreadable and
the strategy isn't one of our templates; see `meta.profile_source`), say the mandate is unknown and
judge conservatively on behavior — do **not** default to a momentum yardstick.

**Anti-patterns — these exact misreads happened live; never repeat them:** (why each is on-mandate:
`references/mandate.md`)

- **A risk-parity / all-weather core is NOT "misaligned" or "dead weight."** Judge it on drawdown control
  and steadiness, not on whether it caught this week's move.
- **A tail-risk / crisis hedge is NOT "wrong-way" for being small or flat in calm markets.** Only a hedge
  that fails to pay off in an actual crisis is broken.
- **A selective strategy with NO open position is NOT a "ghost" or "dead."** Say "flat, waiting for its
  setup," never "idle dead weight."

**Then judge honestly.** Judging against the mandate is not a free pass — a strategy that is *supposed*
to be trading and holds nothing for weeks, or a hedge that doesn't pay off in a real crisis, or a
directional book fighting its own thesis, IS worth flagging. The point is to grade against the right
yardstick, not to excuse everything.

### "Counter to smart money / the crowd" is NOT a defect for a hedge / neutral / all-weather / contrarian mandate

For a **hedge, market-neutral, all-weather, or contrarian** strategy, **being counter is the DESIGN.** **Judge it against
its own `mandate` / `profile.description`, NOT against alignment with the 4h leaderboard / Predators
view.** Do **not** recommend closing a hedge/neutral/all-weather strategy because it's "fighting the
whales" or "on the wrong side of smart money" — that IS its job. (For a *directional momentum* strategy,
fighting the tape is a real red flag — but only for a strategy whose mandate is to ride the move.)

### Don't tear down a deliberate book to chase a short-window signal

The **leaderboard / Predators view is a ~4h momentum window, not a portfolio mandate.** **Never recommend a
wholesale close+redeploy of a deliberate book to chase what's hot on a 4h screen.** Before proposing any
close+redeploy, weigh **turnover cost** (fees compound on churn) and **regime durability** (is this a
lasting shift or a 4h blip?). A deliberate, on-mandate strategy is not "underperforming" because it
didn't catch this afternoon's move.

### Recommend at the STRATEGY level, not cherry-picked positions

For an **autonomous strategy the scanner owns entries and exits**. **Hand-closing an individual position it will simply re-open on the
next tick is futile** (and pays fees twice). The levers that actually change anything are at the
**STRATEGY** level: **close it, pause it, adjust its config, or top up the whole strategy** — not its
individual positions. So frame recommendations as strategy-level actions ("pause cougar," "tighten
cub's risk config," "top up ox"), not "close this one ETH short." (Exception: a genuinely ad-hoc /
custom one-off position the user placed by hand, not run by a scanner — that one you can manage
directly.)

## Golden rules

- **Run the engine; never hand-pull balances.** Read its JSON.
- **Numbers come from the engine; other strategies come from discover.** Never state a win rate, a
  winners/losers count or a long/short split the `closed` block did not print — `recent[]` is the
  last five trades, not the record, and a rate read off it is a guess. If the user asks what to run
  instead, hand off to **senpi-strategy-discover** and quote the returned record's `risk_level`;
  never name a template, or call one aggressive or conservative, from memory.
- **Real-time, always.** Never report balances from earlier in the conversation — re-run.
- **Always say which wallet / which bucket.** Every dollar figure gets a location. "Idle" is
  meaningless without "idle *where*."
- **Lead at the strategy level, judged against the mandate.** For each strategy: state its
  **mandate** (the engine attaches it as `strategies[].profile` — its **`profile.description`, read from
  the deployed `runtime.yaml`**; use catalog facets like `belief_plain`/`archetype` when present), then
  whether it's **doing its job against that mandate**, *then* positions as evidence. See "Judge each
  strategy against its OWN mandate" above.
- **Analyze, don't dump.** Positions are *evidence*, not the headline. For every position, compare it to
  the market (`market_24h_pct`, `vs_market`): is this short *working* because the asset is falling, or
  *fighting* a rally? Read net exposure, concentration, idle drag. See `references/analysis-framework.md`.
- **Use leveraged return, not raw price %.** Cite `return_on_equity_pct` (uPnL / margin), the number
  that actually reflects the position.
- **Report realized PnL + closed trades, not only open ones.** Each strategy carries a `closed` block
  (its fields: `references/output-fields.md`). Quote the record; never work a win rate
  or a long/short split out of `recent[]`. A strategy flat right now may have *already booked* real
  gains; report both realized and unrealized. If `closed.realized_pnl` is `null`, the history read
  failed (see `meta.warnings`) — say realized PnL is unavailable, don't imply zero. For dating a close,
  and for a close that has not reached the record yet, see **Source of truth for position facts** above.
- **Surface the protection posture per strategy — then the live tiers.** Each strategy carries
  `protected` (`true` / `false` / `null`): `true` only when the deployed `runtime.yaml`'s `exit:` block
  is one the **ENGINE actually read** (`dsl_preset` or `engine: dsl`) — a `skill_name` attribution stamp
  alone no longer suffices. `protected` / `not_running` / `running_blind` are **tri-state**: `null` means
  the runtime gateway did not answer — say **"could not verify on this host"**, never "protected" and
  never "not protected." `runtime_health: "unverified"` reads the same way; `meta.warnings` carries the
  command that failed — quote it rather than inventing a cause. State a `true` posture as ("deployed with
  a DSL exit"), then give the **ladder** (`profile.dsl`: hard stop + arm-at + tiers) and each **open
  position's live tier** (`positions[].dsl`). This config-level field is NOT the per-position tier — see
  "DSL — how it works per strategy" below. **Never call a live position "unprotected" for lacking an
  armed ratchet tier.** For a raw position with no runtime, `false` means **no ratchet** —
  see the HARD rule below.
- **Don't infer "wiped out" from a low balance.** Check `total_funded` / `total_withdrawn` — a
  strategy can show a small balance because profits were withdrawn (`netFunded` can be negative). That
  is not a loss.
- **"Current / my strategies" = ACTIVE only — never CLOSED.** If you ever reach for `strategy_list` directly, pass `status: ["ACTIVE"]` — a bare call returns CLOSED/PAUSED
  too and they must not be presented as current. Mention PAUSED strategies only if relevant, clearly
  labeled "paused," never as active.
- **If the engine's own signals disagree, STOP and re-run — do NOT narrate through it.** A `reconciles:
  false` in `totals`, or the `money` and `strategies` steps reporting a different strategy count/set, means
  the numbers didn't tie out. Re-run the step fresh and reconcile BEFORE you say a word — above all before
  any close / rebalance recommendation.
- **The live clearinghouse is the source of truth for whether a strategy holds capital — over the `status`
  field AND over what anyone asserts about the wallet.** The engine reconciles this: a strategy whose live
  wallet holds **$0 account value, no positions, no idle** is flagged **`empty: true`** (`empty_reason`:
  `closed_or_drained` when `total_withdrawn ≈ total_funded`, else `unfunded`; listed in
  `meta.dormant_active`) — report those as closed. A strategy with **`account_value > 0`** is **live**,
  even if `status` is stale or someone believes it's closed.
- **Don't cave to a claim the wallet contradicts, and NEVER fabricate account history to agree.** If the
  user says a strategy is "closed / has no funds" but its `account_value > 0`, it is **live** — say so with
  the number ("wolf is live — $X in the wallet, flat right now, waiting for its signal"). Do not abandon a
  correct reading, and do not invent a story to justify agreeing. Verify first, then correct the record.
- **Live capital = clearinghouse `account_value`, NEVER `total_funded` / `budget` / `status`.**
  `total_funded` / `total_withdrawn` are **lifetime history**, not a current balance. Never present `total_funded` (or a configured budget) as current idle /
  reserved money — read `idle_withdrawable` / `account_value` from the live clearinghouse.
- **A flat strategy that still holds idle margin (`account_value > 0`, no positions) is NOT empty** — it's
  funded and waiting for a signal (or the flat sleeve of a multi-wallet pair); report it as **live**. Only
  `empty: true` (a genuinely $0 wallet) means closed/drained. Don't conflate "flat but funded" with "closed."
- **Present active strategies as known state, not a fresh discovery.** Pull the data quietly and state
  what's running as established fact ("Your two active strategies are…"). Don't narrate the lookup
  ("let me check… oh, I see you have…") — that reads like you didn't already know your own book.
- **Deployed strategies are already risk-managed — don't prescribe a stop-loss they already have.** Never tell a user to "add a 10–15% SL via `strategy_update`" on a deployed
  strategy — it already has one. To *verify* protection, read `profile.dsl` (the ladder) + each
  `positions[].dsl` (the live tier) — see "DSL — how it works per strategy, and which position is in
  which tier"; never infer "no stop" from the absence of a resting stop order (DSL exits are
  runtime-managed, not resting orders) or from a missing or tier -1 ratchet record.
- **Always end with the closing** (below), verbatim — the deep-dive question when offered, then the two
  CTAs.

## DSL — how it works per strategy, and which position is in which tier

When the user asks about **their strategies' DSL** ("tell me about my strategies and their DSL," "are my
open positions protected? / do they have a stop-loss?"), answer in **two parts, per strategy**:

**(1) How its DSL works — the tier ladder** (`profile.dsl`), and **(2) which OPEN position is in which
tier — live** (`positions[].dsl`). Read `references/dsl.md` for both before answering. Two readings
hold always:

- `has_phase2` — `false`/empty `tiers` ⟹ **phase1-only** (a hard stop, no profit ratchet). Say
  "hard-stop protected, no profit-lock ratchet," not "unprotected."
- **Named-string preset** (`dsl_preset: conviction`): `profile.dsl` is `{preset_name, note}`. The
  runtime cannot install a string preset, so `positions[].dsl` has `downside_stop: false`: quote its
  `note` (`references/dsl.md`), never "protected."

### HARD rule — NEVER say a live position has "no active DSL / no monitoring"

**No ratchet record, or a tier -1 one, is sub-Tier-1, NOT "unprotected"**: a runtime strategy's
max-loss stop is on from entry (`references/dsl.md`). **Never read "no ratchet record" as "no
DSL."** Say **"protected; profit-ratchet arms at Tier 1 (+X%)"** — never "unprotected / unmonitored /
no stop." (The engine frames every `armed: false` position in `dsl.note`; never override it with an
"unprotected" reading.)

- **An ERRORED or empty DSL query is "unknown," never "unprotected."** That
  is a **data gap**, not evidence of missing protection — treat it exactly like `dsl:null`. Never turn a failed read into
  a risk finding.
- **A strategy with a null name is still a real strategy** (why so many are: `references/output-fields.md`)
  — identify and analyze them by `strategyId` + wallet, never skip, mislabel ("Unnamed"),
  or double-count them for lacking a display name. (If you *created* it this session, you already know its
  name — don't re-derive it as "unknown.")
- **Only `name_source: "strategyName"` means `name` is really its name.** (The other values:
  `references/output-fields.md`.) When `name_source != "strategyName"`, call it by `strategy_id` + wallet and say which package it came from —
  never "the cub strategy", and never tell one sleeve from another by that string.
- **A raw position (no runtime, `protected: false`) is "no ratchet," not "no stop."** A tier -1
  record (`downside_stop: false`): no downside stop until Tier 1. A one-off position the user placed
  by hand carries its protection as resting orders, which this engine does not read. Before saying it
  has no stop, read them — `strategy_get_open_orders` on that wallet; a
  reduce-only trigger order (`isTrigger`, `triggerPx`) whose `orderType` is a Stop (`Stop Market` /
  `Stop Limit`) is its stop, a Take Profit order is not — and say what you found: "no ratchet;
  a static stop rests at $X" or "no ratchet and no stop order." The direct question ("do I have a
  stop?") is `senpi-trade`'s protection protocol, which reads the same orders; this is the portfolio view.
- **`SL_TRIGGERED` is history, not current exposure.** A `SL_TRIGGERED` (or `MANUALLY_CLOSED` /
  `LIQUIDATED`) record on a **closed** position means the DSL **did its job** — it locked profit / cut the
  loss. Present it as history ("DSL locked profit on the ETH short last week"), never as current risk.

## One wallet list — row details

**Saved wallets** (their raw reads, `external_wallets`: `references/output-fields.md`).

- **Trade history** on a saved wallet ("how did my trades on it go", "where am I leaking on it") →
  `senpi-improve-trades` (review) or `quant-desk` (score and leaks). This skill reads balances and open
  positions only.

Going deeper on a Senpi strategy is its per-strategy verdict (the `strategies` step); on a wallet the
user added, its row here, then its trades via `senpi-improve-trades` or `quant-desk`; on the Senpi main
wallet, its cash legs (`embedded_wallet`).

- **`not_read_this_step`** on a strategy row (the `money` step reads no positions) → those columns come
  with the `strategies` step; never "none" or $0.
- **`loaded: false`** → "couldn't load" in the value column, never $0, and it stays last.
  `wallets_couldnt_load` > 0 on a strategy row → "N of its wallets couldn't load".
- **The line's unknowns.** `read_only_usd: null` means the saved-wallets read failed or none of the saved
  wallets loaded ("read-only unknown"), never $0; when only some loaded, `read_only_wallets` (`loaded` of
  `total`) and the line say how many ("1 of 2 wallets you added"). No saved wallets → the line has no
  read-only clause. `book.totals.managed_complete: false` → strategy_list couldn't be read: the managed
  figure is the Senpi main wallet only, the line says "your Senpi strategies couldn't be read", and it is
  never "no strategies" (no `meta.no_strategy_path`, no strategy pitch) — say you couldn't read them.

- **Quote, never recompute.** Each wallet's `state` is `account_get_external_wallets`' object verbatim.
  `totalValueUsd` is the wallet's value (already unified-account aware — never add `spotBalances` to it
  yourself, and never call `accountValueUsd` "account value": on a unified account it is a margin figure
  that matches nothing Hyperliquid shows). A non-empty `unpricedCoins` → say the total excludes those
  coins ("total excludes STHYPE"). `positions[]` carry `protection` (`FULL` / `PARTIAL` / `NONE`) and
  `stopOrders[]` (`ARMED` or `WAITING_TO_ACTIVATE`). `protection` is the live stops on the exchange, not
  `protected` (a Senpi strategy's runtime exit) — never merge the two words. A `WAITING_TO_ACTIVATE`
  trailing stop protects nothing until it activates; say so.
- **Positions are read on the Hyperliquid main and xyz dexes only.** Scope every positions answer that
  way — "no open positions on the Hyperliquid main and xyz dexes", never a bare "no positions" — and
  never present the total as covering another HIP-3 dex.
- **Unknown is never empty.**
  - `external_wallets.status: "unavailable"` → say "I couldn't load your saved wallets", never
    "you have none".
  - `state: null` (`state_read: "unavailable"`) → "couldn't load this wallet"; never $0, never "no
    positions".
  - `state_read: "error"` → couldn't load this wallet: its balances, positions and protection are all
    unknown and every number is null, never $0. The `readError` code is matched by prefix — never print
    the code; with an `ORDERS_UNAVAILABLE` prefix you may add "its orders couldn't be read".
  - `state.role: "MISSING"` with `totalValueUsd` null or `"0"` → "no Hyperliquid activity yet". A
    non-zero `totalValueUsd` wins — quote the value (the role may be cached from before the first
    deposit).
- **Not applicable, not a fault.** DSL, runtime health, mandate, funded/drained and telemetry
  (`not_applicable`) do not exist for a wallet the user trades by hand — never "no DSL", "not running",
  "unprotected strategy" or "drained".

## Run it in steps — narrate as you go

**Asked to run this on a schedule? Say the cost first.** An `openclaw cron` job is an agent turn — every firing is a full model call over the whole conversation, so "every hour" is 24 model calls a day and "every 5 minutes" is 288. Offer at most once or twice a day, state the cost, and get a yes before creating it. Never a cron to watch a strategy: the runtime supervises it at zero model cost, and this skill reads it on demand.

Run the read as **fast, resumable STEPS** (`money` → `strategies` → `positions`; what each returns:
`references/output-contract.md`) and **narrate each slice the moment it returns** — each step a
separate `exec` call, so no single call hangs.

**Narrate each slice as it returns — never wait for all steps.** **For a NARROW ask, run only the
minimal step:** the router at the top names it. The full read's narration order, step by step, is in
`references/output-contract.md`; the shared state file, `--no-market` and the fail-open contract are
in `references/output-fields.md`.

## How to run the engine

```
python3 scripts/portfolio.py [money|strategies|positions|all] [--no-market] [--state PATH]
```

Prefer the **steps** above for a full read (they stream and don't trip the
timeout); use `all` only when a single blocking call is fine.

Returns `{book, totals, embedded_wallet, external_wallets, strategies, strategy_groups, exposure, signals, meta}`:
every field is described in `references/output-fields.md` — read it before quoting a field you
have not used yet.

## Output contract

Order matters: **strategy verdicts lead; positions are evidence underneath them.** (When the question
is purely "how much / where is my money," you can open with the wallet list instead — but for anything
about "my strategies / how am I doing," lead with the per-strategy read.)

**Lead from `strategy_groups[]` — one verdict per real strategy, NOT per wallet.** A multi-wallet
strategy (long+short, core+ballast, multi-sleeve) is ONE strategy across N wallets; present it as one.
See "A strategy is ALL its wallets."

1. **One wallet list + the book total.** `book.wallets` as one table in the engine's order — label,
   value, open positions, protection, PnL, kind — then `book.totals.line`: the book total, the managed
   subtotal (`grand_total_usd`, broken into idle-in-embedded / idle-in-strategies / deployed — each
   labeled by *where*) and the read-only subtotal, with `book.totals.excludes_note` when present. Keep it
   tight; this is the money map, not the analysis.
2. **Per-strategy verdict (the real value).** For **each `strategy_groups[]` entry** (one per real
   strategy — never one per wallet), in this order:
   1. **Label + mandate.** 2. **Is it doing its job — against its OWN mandate.** 3. **Positions as
   evidence — across ALL the strategy's instances.** Flag any position fighting the tape *for a
   directional-momentum mandate* / near `liq_px` / oversized. 4. **PnL — realized + unrealized, summed
   across the strategy.** 5. **DSL protection — ladder + live tiers.** State the group's `protected`
   posture (⟹ **all** instances ship a DSL exit), then **how its DSL works** from `group.dsl` /
   `profile.dsl` (hard stop at `hard_stop_roe_pct`, ratchet arms at `arm_at_roe_pct`, the tier ladder),
   then **each open position's live tier** from `positions[].dsl`. 6. **Any lever is
   WHOLE-STRATEGY.** (Each in full: `references/output-contract.md`.)
3. **Portfolio-level read.** Net exposure (net long/short and by sector), concentration (largest
   position), idle drag (capital sitting in cash), and the overall posture — is this book hedged,
   directional, mostly in cash? Compare the net tilt to where the broader market is.
4. **The two CTAs** (the menu in full: `references/output-contract.md`).

Formatting: group by strategy (a `strategy_groups[]` entry = one strategy; show its instances as its
sleeves, not as peers); show `Δ%` and leveraged return; emoji sparingly (🟢/🔴 for green/red books).
Show strategy wallet addresses in short form (`0x35d1...acb1`) unless asked for full.

## Resilience (engine handles; narrate honestly)

- **Token app-scoped / no wallet data** → `meta.degraded`. Say you can't read the account with this
  token (it needs a USER-scoped token); don't report an empty portfolio as "$0."
- **A strategy's clearinghouse read failed** → it's in `meta.warnings`; that wallet's positions may be
  incomplete. Say so rather than implying it's flat.
- **A strategy's closed-history read failed** → `closed.realized_pnl` is `null` + a `meta.warnings`
  entry (`trader_history … failed/returned no data`). Report realized PnL as **unavailable** for that
  strategy — never as `$0`.
- **`totals.reconciles == false`** → the per-wallet sum and the portfolio aggregate disagree; the engine
  also appends a `TOTALS DO NOT RECONCILE` entry to `meta.warnings` quoting both figures and the gap.
  STOP and re-run first (see above); if it persists, surface it and trust the per-wallet (live) figures.
- **Never** report `total_withdrawable` as embedded idle, never skip a wallet, never skip the CTAs (on `meta.no_strategy_path` the saved-wallets closing replaces them).

## Mandatory closing (verbatim)

**First, the deep-dive question** when `book.deep_dive.offer` is true — see the end of "One wallet
list — every wallet first-class" above.

Then the two CTAs. CTA 1 and CTA 2 are about managed wallets only (`kind: managed`). On
`meta.no_strategy_path` the saved-wallets closing replaces them — see "One wallet list — every wallet
first-class".

> **1. Want me to rebalance or adjust any of these positions?**
> **2. Want me to put the idle capital to work in a new strategy?**

- **CTA 1 → strategy / position management.** For an **autonomous strategy**, route to the
  STRATEGY-level levers (`strategy_pause` / `strategy_update` config / `strategy_close` / `strategy_top_up`)
  and apply them to the **whole strategy (all its wallets)** — never to a single sleeve of a multi-wallet
  strategy, and never hand-close a position the scanner will just re-open. Only use per-position tools
  (`edit_position` / `close_position`) for a genuinely ad-hoc position the user placed by hand on a Senpi
  wallet — never a saved wallet, which is read-only (quote its `access` line). Confirm
  before any change; never trade unprompted.
- **CTA 2 → deploy idle.** If there's meaningful **truly-free** idle capital (lead from
  `signals.idle_drag_pct` and `idle_in_embedded` — NOT a flat sleeve of a live multi-wallet strategy,
  which is committed, and never `book.totals.read_only_usd` or any read-only row, which Senpi cannot
  deploy), name the ready options rather than opening a blank picker. One per line, so it
  can be answered with a digit:

  > **What should the idle go into?**
  >
  > 1. **Penguin** — the Hyperfeed striker, crypto only: one position at up to 10x, 90% margin, on the
  >    strongest live rotation, with a DSL floor that ratchets up to lock gains. High risk, high reward,
  >    with -15% SL.
  > 2. **Pelican** — the same striker across all assets: crypto plus stocks, commodities, indices
  >    and pre-IPO.
  > 3. **Puffin** — the smart-money signal feed, concentrated into one position at a time at high
  >    leverage.
  > 4. **Signals Hunter** — that same feed traded on a clock, spread across several slots.
  > 5. **Athena** — a smart-money hedge fund, forked under your name.
  > 6. Or **something else** — tell me how you want to trade and we'll find or build it.

  Penguin and Pelican are the two most people start with, because a live rotation shows up fast and
  they are built to be forked and modified. Be able to unpack the numbers: **-15% SL is 15% ROE, not a
  15% price move** — at 10x that is a **1.5%** move, so answer in price when asked. On 90% margin it is
  **~13.5% of that wallet per stop-out**, and say **per stop-out** — their risk guard rails are off, so
  stops compound. **Up to 10x**, never a flat 10x: the per-name venue cap clamps many instruments, which
  moves the price behind every number, not the wallet cost. Say **rotations**, never "pumps".
  Routes 1-5 hand to **senpi-strategy-ops**;
  route 6 to **senpi-strategy-discover** (find one) or **senpi-strategy-author** (build one). Topping up
  an existing *whole* strategy via `strategy_top_up` stays available whenever that fits better.
  Propose; never deploy without confirmation.

## Reference index (`references/`)

Read the file before doing what its line names; each is short enough to keep whole.

- `why-not-trading.md` — "why hasn't it traded / why didn't it open X": every reason code, no signals,
  no reason codes.
- `runtime-health.md` — any liveness verdict: ACTIVE ≠ running, the `runtime_health` states, the
  corroboration grid, the broken-strategy ladder, the minimum-runtime pattern.
- `mirror-strategies.md` — judging a copy-trade: how a mirror is protected, its only remedies.
- `mandate.md` — a per-strategy verdict: the `profile` fields and the mandate source.
- `dsl.md` — a DSL / "is it protected" answer: the tier ladder, each position's live tier.
- `output-contract.md` — a full read: the per-strategy verdict order, the portfolio read.
- `output-fields.md` — quoting an engine field you have not used: every field the engine returns.
- `analysis-framework.md` — a positions read: money map, positions vs the market, exposure.
- `rationale.md` — a user disputes a hard rule: the live failures behind them.

## Install — both scripts are required

The engine is **two files** in `scripts/`: `portfolio.py` (the engine) and `mcp_client.py` (its vendored
MCP helper, imported at runtime). **Install the whole `scripts/` directory** — copying `portfolio.py`
alone fails with `No module named 'mcp_client'`. Stdlib only, no other runtime dependencies.
