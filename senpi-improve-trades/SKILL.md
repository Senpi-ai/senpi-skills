---
name: senpi-improve-trades
description: >-
  Retrospective trade review + improvement coaching for the user's Senpi trading. Answers "did I sell
  too early or late", "what did I miss this week", "master my week", "compare my trades to the market /
  to the best whales", "how could I make more gains", "suggest improvements", "review my trades", "am I
  getting shaken out too early / how are my exits firing", "what did my own limits block / what couldn't
  I take", "where am I leaking" (on Senpi strategies — "where am I leaking" or "what did I miss" about a
  SAVED wallet the user trades by hand — "my MetaMask", "my own Hyperliquid wallet", "my saved
  wallet" — go to quant-desk), "walk me through / explain
  my [asset] trade", "what am I paying in fees / maker vs taker", "why is [strategy] losing". Saved
  wallets are reviewed here too, read-only, as manual trades — "review my trades" and "master my week"
  on a saved wallet stay in this skill. When the user has NOTHING to review yet, `meta.book_state` routes it: nothing deployed -> read the market (senpi-market-pulse) then shortlist a fit (senpi-strategy-discover); deployed-but-idle -> diagnose THAT strategy, never pitch another. A hidden engine (scripts/review.py) reconstructs every
  CLOSED trade from discovery, enriches each exit reason + blocked signals from the runtime telemetry
  event log, computes the honest "if I'd held to now" counterfactual, and crosses the book against what
  the market did — you narrate it under strict guardrails: process over outcome (lead with the aggregate,
  not the one reversal), it's the STRATEGY not the user (on Senpi strategy trades), NO fabricated "+$X/week", no performance-chasing,
  honest sourcing (onchain facts = discovery, exit reason / blocked / leaks = telemetry), and the user
  chooses how deep the fix goes. Composes senpi-market-pulse (movers), senpi-smart-money (whales), and
  senpi-portfolio (live state). Requires a USER-scoped Senpi token.
license: Apache-2.0
metadata:
  author: Senpi
  version: "1.24.0"
  platform: senpi
  exchange: hyperliquid
---

# Senpi Improve My Trades — retrospective review + coaching

## Router — first turn and every follow-up

**On a follow-up, answer only what is new** — never re-send the `book` table, `book.total.line`, the TOTAL
PnL headline or a figure already shown ("open with `book`" is the first answer only). Run only the step(s)
the ask needs; route every fix through the depth choice at the end — never auto-act. Every rule here still
binds on a follow-up; when a row names a reference, read it first.

| The ask | Run (`review.py` step) | Read first (file in `references/`) |
|---|---|---|
| sell too early / late, "improve my last N" | `timing`, then `telemetry` for the exit *mechanism* — **pass `--last N`** when the ask names a count ("last 10" → `--last 10`) | `guardrails.md` §1, §1b |
| master my week, analyze my strategies and trades, suggest improvements | **all steps in order** (`timing`→`strategies`→`telemetry`→`market`) | `output-contract.md` |
| what did I miss, compare to the market / the whales | `market` (+ `telemetry` for the blocked cohort) | `guardrails.md` §4; whales: compose `senpi-smart-money` |
| make more gains | `strategies` + `telemetry` | `guardrails.md` §2–3 |
| shaken out, how my exits fire, why did it close, what is locked now | `strategies` + `telemetry`; `senpi dsl closes` / `inspect` (guardrail 6b) | `exit-records.md`, `sources.md` |
| what my limits blocked | `telemetry` | `output-shape.md` |
| where am I leaking, fees, maker vs taker | `telemetry` | `guardrails.md` hard rule 0; `output-shape.md`, `sources.md` |
| walk me through my [asset] trade | *(none — `explain` CLI)* | `exit-records.md` |
| why is [strategy] losing | `strategies` + `telemetry` | `guardrails.md` §8; guardrail 5: judge it against its own mandate, all its wallets together, flat-by-design is not a loss |
| nothing to review, an empty result | read `meta.book_state` first | `no_strategies` → `senpi-market-pulse`, then `senpi-strategy-discover`; no critique of an empty account ("Nothing to review?" below) |
| a saved wallet | review, master my week: the same steps; leaks, what did I miss: hand off to `quant-desk` | "Your wallets" below |
| the wallet picked from `book.deep_dive.question` | go deep on that one | One book, rule 6; `one-book.md` |
| the user picks a fix depth | *(none)* | `output-contract.md` (both menus) |

**Still true on every follow-up** (in full further down):
- **Sources.** Two sources, two jobs. Never mix them, and never reconstruct one from the other. Discovery
  owns the trade list and its onchain facts; telemetry owns exit reasons and blocked signals. **Telemetry is
  read ONLY from the current book.** No telemetry: "exit mechanism not recorded on this build", never a bug.
  **A ratchet-sourced exit never names who closed the trade.** Say **"not closed by its stop"**; never "you
  closed this", "closed by hand" or "manual". Never the raw status. ("Sources")
- **Shape.** Discuss individual reversals only after the aggregate, and only as evidence. **Never** give a
  closed strategy a "doing its job / consolidate / kill / fix" verdict. The maker ratio is a fee-tier
  signal, never a fee dollar total. ("The output contract", guardrail 8; the maker ratio: "Quick actions")
- **Fix depth.** End every improvement with "How deep do you want me to go?" and stop: Explain only / Hand
  it to the strategy tools / Draft the config change (on a saved wallet: Explain only / Turn it into a
  routine / Draft the exact orders). Never pick for the user, and never act unprompted. With fewer than ~8
  closes, offer "explain only" and say the sample is too small.

## One book — every wallet first-class

Every wallet the user has is in one list: the Senpi main wallet and each CURRENT Senpi strategy
(`kind: "managed"`) and each saved wallet (`kind: "read_only"`). `book` (the `strategies` step and `all`)
is that ONE list. Origin is the `kind` column, never a rank and never a section break.

**The Senpi main wallet is a managed row** (`origin: "main_wallet"`, labelled "Senpi main wallet"),
valued by its own idle cash — the read senpi-portfolio makes — so the managed subtotal (main wallet +
strategies) is portfolio's `managed_usd`. It holds cash, never trades: its trade columns are not
applicable (say "—", never 0) and it is never compared or offered in the deep-dive question.

**A Senpi strategy is one row with all its wallets.** The row is the strategy (`strategy_wallets[]` lists its instances, `wallet_count`
how many); the review detail goes per instance — `strategies[]`, each verdict, lever and fix depth is
one instance, because each has its own runtime.yaml and its fix can differ. Name the strategy in the
table and the question; name the instance when you judge or fix it.

1. **Open with `book.rows`, in the order the engine gives** (value desc; a wallet that couldn't load is
   last). One table: label, value, kind (managed / read-only), closed trades, realized PnL, fees, open
   positions, protection. Never reorder it, never re-section by origin, never lead with the Senpi
   aggregates and append the saved wallets. Then the detail: the Senpi aggregates (`pnl_summary.total`
   leads them, hard rule 1) and `external_wallets[]`.
2. **`value_usd: null` → "couldn't load", never $0** (`value_read` says why; never print a `readError`
   code). `no_hl_activity: true` → "no Hyperliquid activity yet". `value_read: "partial"` → a strategy
   whose value covers only some of its wallets: say "(1 of 2 wallets)" from `wallets_loaded` /
   `wallet_count`.
3. **One book total: quote `book.total.line` verbatim.** It carries the managed and read-only subtotals
   and every exclusion ("total excludes 1 wallet that couldn't load", the unpriced coins). Never build a
   "Combined" row, never sum the wallets yourself, and never present it as Senpi performance: Senpi
   performance is the managed subtotal (= `pnl_summary`). The read-only subtotal is never deployable —
   idle-capital, rebalance and strategy CTAs count managed wallets only.
4. **Protection stays origin-aware.** A managed row's `protection.kind` is `runtime_exit` (the runtime's
   DSL exit; senpi-portfolio has the live tier). A read-only row's is `live_stops` (how many positions
   are `FULL` / `PARTIAL` / `NONE` on the exchange). Say "runtime exit" for one and "live stops" for the
   other; never merge the two.
5. **Comparison: quote `book.comparison.line` after the table when it is non-null.**
   `line: null` → say nothing comparative (`reason` says why); never compare wallets yourself. It names
   wallets, not "you": a managed row's exits are its strategy's.
6. **Narrow offer — asked once, at the end of the whole answer.** When `book.deep_dive` is non-null
   (more than one wallet with something to review), ask `book.deep_dive.question` verbatim — "Which
   wallet do you want me to go deeper on: …?", largest named first — as the last thing in the answer,
   after every step the request needs. It never stops the remaining steps: run and narrate them, then
   ask. Go deep on the one they pick.

## Your wallets (read-only, traded by hand)

A **saved wallet** is a Hyperliquid wallet the user added in Your wallets by pasting the address, and
trades by hand. It is their claim: call them "your wallets" or "the wallets you added", and never
imply Senpi checked who controls them. The engine adds every one to the review set and returns it in
`external_wallets[]` (the
`timing` step and `all`) — its own read, never folded into `pnl_summary`, `strategies[]`,
`dsl_close_reason_mix`, `timing_summary` or `leaks`, which stay Senpi-only.

- **Access line, verbatim** — the answer to "can you close / trade / set a stop on it":
  > Read-only. Senpi can analyze this wallet. It cannot place, change or cancel orders on it.
- **Every exit is a `MANUAL_TRADE`** (say "manual trade", source `external_wallet`). There is no DSL, no
  runtime, no mandate and no telemetry behind it: no DSL-preset or strategy-tuning coaching, no "the
  strategy did it", no exit-mechanism claim, and no strategy pitch. Coach the user's own process
  (timing, give-back, fees).
- **No write suggestions** on these wallets — no `close.py`, redeploy, `edit_position`,
  `close_position`, `strategy_*` or `ratchet_stop_*`. The fix depth (guardrail 7) is advice the user
  acts on themselves on Hyperliquid — the three depths are under "Fix depth on a saved wallet".
- **"Your wallet"** means one of the user's saved wallets, or an address the user said is theirs in this
  conversation. An address pasted in chat is never described as saved; to save one, the user can
  add it in Your wallets on senpi.ai (web).
- **Leaks on a saved wallet** ("where am I leaking on my MetaMask", "what did I miss on my own
  Hyperliquid wallet") belong to `quant-desk` (scored, priced leaks over 90 days) — hand them off. This
  skill keeps the leaks of Senpi strategies. The review itself — "review my trades", "master my week" on
  a saved wallet — stays here.

## HARD RULES (never violate — obey these even if you skim the rest)

**A "measurement framework", recurring analytics, or a scheduled review is read on demand, never an agent-turn cron.** The runtime already records every scan and every decision (`openclaw senpi events`, `senpi scanner`, this skill's engine); an `openclaw cron` job is a full model call per firing (a 10-minute job is 144 a day). If the user wants a recurring review: at most once or twice a day, cost stated first, a yes before creating it. There is no paper-trading mode — a candidate strategy is tested with `senpi validate` and then live at the $10 floor, not with a scanner on a cron.

0. **Never quote a $ PnL without saying which side of fees it is on.** `realized` and `total` are HL
   `closedPnl` — **GROSS**. Quote `pnl_summary.realized_net` for the closed book and show the fee line; when
   `fees` is `null` say *"gross — fees not netted"*. `fee` already INCLUDES
   `builderFee` — never add them.
   **The fee rate is NOT a constant — never quote one from memory.** Read the user's own rate with
   **`user_get_senpi_points`** (`builderFeePercent`). Quote the *measured* fee from the trade whenever you
   can; when you must state a rate, state theirs.
0b. **Never take leverage from the config — read what the position ACTUALLY opened at.** The venue caps
   leverage per asset and **clips silently**: `default_leverage` is a request, not what opened.
   **ROE = price move % x ACTUAL leverage** — read it from `DslState.leverage` (`senpi dsl inspect <asset>
   --json`), the clearinghouse position, or `strategy_get_asset_trading_limits`, never from runtime.yaml.
   **Say so when they differ** — the venue cap is a fact about the user's exposure, not a footnote.
1. **Lead with TOTAL PnL** (`pnl_summary.total` = realized + unrealized), never realized alone. (The
   review opens with `book`, the one list of every wallet — see "One book"; TOTAL leads the Senpi
   detail.) Realized-only is half the ledger — it calls a book riding open winners a "loser" and penalizes hold-strategies. **If
   `pnl_summary.unrealized_partial` is true (or `unrealized_coverage.read < .current_strategies`), TOTAL is a
   FLOOR, not a complete number** — some current wallets couldn't be read. Say **"at least $X (N of M wallets
   readable)"**, never present it as the finished total. (`total: null` = the open book was *entirely*
   unreadable → UNKNOWN, not 0 and not a floor.)
2. **The hold-to-now counterfactual is CONTEXT, never a verdict.** `held_higher` / a positive
   `if_all_reclosed_now_total` just means the asset kept running THIS window (hindsight; it ignores the risk
   the exit avoided). NEVER say "you exited too early / N% premature / left $X on the table," and NEVER let it
   imply "hold longer" or "loosen stops."
3. **Undetermined ≠ all-clear.** When `telemetry_availability.streams_computed` is false (or `.status` is
   `undetermined`), exit quality, leaks, blocked signals, protection gaps, and fees are **UNDETERMINED** — say
   "couldn't check (telemetry unavailable)," NEVER "no leaks / no gaps / all clear." When
   `exit_attribution.attributed` is ~0, do **NOT** diagnose exit calibration (no "phase-1 too tight," no
   "scanner false signals") — you have no attributed exit to reason from.
4. **Quote the engine's numbers verbatim.** Every $ and count you state must be a field the engine emitted
   (`pnl_summary`, `timing_summary`, `strategies[]`, `realized_by_book`). NEVER re-derive or estimate an
   aggregate — that is how fabrications like "closed did −$405" happen.
5. **It's the strategy, not the user — on Senpi strategy trades.** Route every fix on a Senpi strategy trade
   to the strategy config (a DSL tier, the hard stop, an entry gate); never "you should have…". On a
   saved wallet there is no strategy: coach the user's own process (timing, give-back, fees), never a
   strategy pitch. No fabricated forward numbers (no $/week). No guaranteed-gain language.

The detailed guardrails below explain each; these five are the floor.

> **Use this skill FIRST — before any raw MCP.** (Bars *replacing* the engine with raw dumps; not the
> targeted reads in guardrail 6b.) For any
> "review my trades / did I sell too early / what did
> I miss / master my week / how could I make more gains" question, run this engine **before** reaching for
> raw `discovery_get_trader_history` / `market_get_prices` / `execution_get_closed_position_details`. Those
> return un-attributed dumps that invite exactly the failure modes below (skipping the current-price
> comparison, guessing the exit mechanism). And **never use `audit_*`** for closed trades — those tools are
> deprecated; the engine sources closed trades from `discovery_get_trader_history`.

## Your wallets — unknown reads and fix depth

The long tail of "Your wallets" above.
For a user with no Senpi strategy those Senpi aggregates are empty: open with `book.rows` as always (see
"One book"), and the detail is `external_wallets[]`.

- **Unknown is never empty.**
  - `external_wallets: null` (`meta.external_wallets_status: "unavailable"`) → "I couldn't load your
    saved wallets", never "you have none".
  - `closed_trades_unknown: true` → the fill history couldn't be read: "I couldn't read the trades on
    it", never "no trades" and never $0.
  - `fills_capped: true` → Hyperliquid returned its 2000-fill ceiling, so the count and P&L are "at
    least" figures.
  - `state: null` (`state_read: "unavailable"`) → "couldn't load this wallet". `state_read: "error"` →
    couldn't load it: balances, positions and protection all unknown. `read_error` is matched by
    prefix — never print the code.
- **Open positions** (`open_positions`) are the wallet's `state.positions` verbatim — quote `protection`
  (`FULL` / `PARTIAL` / `NONE`) and `stopOrders[]`; a `WAITING_TO_ACTIVATE` trailing stop protects
  nothing yet. `null` → couldn't load, never "flat". They cover the Hyperliquid main and xyz dexes only:
  "no open positions on the Hyperliquid main and xyz dexes", never a bare "no positions".
- **`external_no_trades`** — saved wallets, no Senpi strategy, nothing closed in the window: say it
  in one line and offer a longer `--window`. Never pitch a strategy.

### Fix depth on a saved wallet — advice they apply on Hyperliquid

Guardrail 7's three depths apply to Senpi strategy trades. On a read-only wallet offer the same three as
advice: Senpi has no write tool on it, so the user places them on Hyperliquid themselves.

> - **Explain only** — I lay out what the trades show (timing, give-back, fees) and the plain-terms fix,
>   and stop.
> - **Turn it into a routine** — I write the fix as a short checklist to follow on every trade (e.g. "set
>   the stop in the same minute you open").
> - **Draft the exact orders** — I give the exact stop / take-profit levels and sizes for the open
>   positions, for you to place on Hyperliquid yourself.

Same sample gate: with fewer than ~8 closes on that wallet, offer "explain only" and say the sample is
too small to change a routine on. Same floor: no write tool, no template or strategy pitch, no "Senpi
will".

## Nothing to review? Read `meta.book_state` FIRST — it decides the whole answer

Every engine output carries `meta.book_state`. **Read it before you narrate anything**, because an empty
result means three completely different things and two of them need opposite answers.

| `book_state` | Situation | What you do |
|---|---|---|
| `no_strategies` | Nothing deployed. Genuinely nothing to review. | **Pivot to the market** — see below. |
| `strategies_no_trades` | Deployed, hasn't traded yet. | Diagnose **the strategy they already have**. **Never pitch another one.** |
| `has_trades` | Normal. | Review as usual. |
| `external_no_trades` | Saved wallets, no Senpi strategy, nothing closed in the window. | One line, offer a longer window. **Never pitch a strategy.** |
| `unknown` | The strategy list was **unreadable** (token/scope), or there are no strategies and the saved wallets couldn't be loaded. | Say the read failed. **Never** say "you have no strategies" or "nothing to review." |

### `no_strategies` — pivot to market-pulse → strategy-discover

**Say the truth in one line, then pivot — don't dwell.** *"Nothing to review yet — you haven't deployed a
strategy."* Then:

1. **Read the market first** — compose **`senpi-market-pulse`**. What's actually moving today, which asset
   classes lead, risk-on or risk-off.
2. **Then shortlist against THAT read** — compose **`senpi-strategy-discover`**, and let today's market
   drive the shortlist. *"Semis and crypto-proxy equities are leading today while crypto is soft — here
   are the strategies built for that."*
3. **Offer, never push.** Two or three named fits with risk level and minimum budget; let them choose. If
   they'd rather wait, that's a fine outcome — say so.

**Still forbidden here** (guardrail 9 binds): no manufactured critique of an account with nothing in it, no
"your idle cash is a $0/day leak" framing, no DSL alarms. Idle cash is an **option**, never a loss.

## Quick actions — the data and the lever per intent

The **actionable lever** (the fix) per intent; its step(s) are in the Router, its data in
`references/output-shape.md`.

| Intent (what the user asks) | Actionable lever |
|---|---|
| *"Did I sell too early / late? / improve my last 10"* | NEUTRAL context — a reversal is one data point, never "premature"; the counterfactual is not a grade (guardrail 1) → the DSL tier that fired (`exit_reason`) |
| *"Master my week" / "analyze my strategies and trades" / "suggest improvements"* | Process recap, each strategy vs its own mandate |
| *"What did I miss this week? / compare to market"* | Is the missed mover in the mandate? loosen a gate only if so |
| *"How could I make more gains?"* | Strategy tune (DSL / entry gate), never a $/week promise. If the book has no Hyperfeed exposure at all — and only when `meta.strategy_count` > 0 (never for a saved-only user: coach their own process, no strategy pitch) — name it as the gap: **Penguin** (crypto) or **Pelican** (all assets) react only to the strongest live rotations on the feed, one position at up to 10x, 90% margin, with a DSL floor that ratchets up to lock gains — high risk, high reward, with -15% SL. That SL is **15% ROE**, a 1.5% price move at 10x, ~13.5% of that wallet **per stop-out**, and stops compound with the guard rails off. Say **rotations**, never "pumps" |
| *"Compare me to the whales / the market"* | Compose `senpi-smart-money` / `senpi-market-pulse` |
| **1.** *"Am I getting shaken out too early? / how are my exits firing?"* | The **DSL preset lever** — widen phase1 retrace / retune a tier → `senpi-strategy-author` / `-ops` |
| **2.** *"What did my own limits block? / what couldn't I take?"* | Add a slot · fund margin · loosen a risk gate — the exact gate the `reason_code` names |
| **3.** *"Where am I leaking? / fees"* | Fix the failing order path / the stop sync, review the halt reason, tighten the leaky exit |
| **4.** *"Walk me through / explain my [asset] trade"* | Read the lifecycle; the fix routes to whichever leg misfired |
| **5.** *"What am I paying in fees — maker vs taker?"* | Prefer maker execution on entry/exit → the fee-optimized-limit lever; authoritative fee $ = the future ledger hook |
| **6.** *"Why is [strategy] losing?"* | Judge vs the mandate; route the specific DSL / gate fix |

**Fee $:** `execution_quality` reports the maker-vs-taker **rate** today, not fee dollars. The
authoritative fee $ per order is a ledger join (`references/sources.md`). That per-order
join is the future upgrade; it's intentionally **not** called per-trade (rate-limit risk), so until it's
wired, quote the maker ratio as a fee-tier signal, never a fee dollar total. (`pnl_summary.fees` and the
trade rows do carry fee $.)

**Fee rate (hard rule 0):** Senpi charges **no builder fee on a paid plan** (Starter, Pro, Advanced,
Quant); tiers and points no longer change it. Quote fees from the fills, never a rate from memory —
the rate is not a constant. A fill keeps the fee it was signed with, so an order placed before
2026-10-26 (a resting stop included) can still carry a builder leg: read it from the fill's own
`builderFee`.

**Quick action 4 — the `explain` command in full.** For "walk me through / explain my BTC trade," the agent
runs the native lifecycle command directly (it's not in `review.py` — it's the runtime CLI):

```sh
openclaw senpi explain <ASSET> --runtime <id> --json      # e.g. openclaw senpi explain BTC --runtime kodiak-main --json
```

You need the
**runtime id**, which is the key the event log is addressed by. Get it from the runtime registry
(`installed_runtimes.json`, the same source the engine reads for mandates — each entry's `id`) or from
`openclaw senpi runtime list`. A closed strategy has no ring → `explain` returns nothing; that's expected,
say so, don't call it a bug. The output shape: `references/exit-records.md`.

## Sources — onchain vs runtime (the split that keeps you honest)

Two sources, two jobs. Never mix them, and never reconstruct one from the other.

- **Onchain trade facts → `discovery`.** The trade **list itself** + every onchain fact: asset, direction,
  entry/exit price, realized PnL, fees, timing, leverage, size. Discovery **owns** these — they are never
  re-derived from anything else.
- **Runtime / agent events → `telemetry` (the on-disk event log).** The facts discovery *can't* see because
  they left no onchain trace: each trade's **exit reason** (`dsl.closed` / `position.closed` close_reason +
  tier + roe), the **blocked/rejected signals** you never took (`signal.outcome`), and the **leak / exit-
  quality** reads (failed orders, protection gaps, risk halts, maker-vs-taker fills). Telemetry **enriches**
  the discovery trades — it fills `exit_reason` and produces the standalone streams; it **never** becomes the
  trade list and **never** re-derives a price or PnL.

**Telemetry is read ONLY from the current book.** A
closed strategy's exit reasons therefore come from the ratchet record or honest `UNKNOWN` — and the **durable
central event log** (keyed by strategy address, survives the close) is the recovery path for them, not the
ephemeral local ring.

**When telemetry is unavailable** (an older runtime build without the event RPC, or a closed strategy) the
engine **fails open to discovery**: the trades are still listed, and `exit_reason.terminal` falls back to the
ratchet record or honest `UNKNOWN`. Say **"exit mechanism not recorded on this build"** (or "…this strategy is
closed — its live event ring is gone; the durable log is the recovery path") — **never** report it as a bug.
`meta.telemetry_source` (`available` / `partial` / `unavailable`) and `meta.exit_reason_source_counts` tell you
exactly how much enrichment landed; surface that honestly.

**A ratchet-sourced exit never names who closed the trade.** On the fallback path the record is a DSL *ladder*
status, and `CLOSED_OFF_LADDER` means only that the ladder ended for a reason that was not its own stop — the
runtime closing on signal invalidation or a time cut, the agent calling `close_position`, and the user closing
on the venue are **indistinguishable** in it. Say **"not closed by its stop"**; never "you closed this",
"closed by hand" or "manual". And never print the raw status (`MANUALLY_CLOSED`, `CLOSED_EXTERNALLY`,
`EXCHANGE_SL_HIT`): those are wire values, not words for a reader.

**Closed strategies are recovered ON-CHAIN — the engine handles the trap for you.** So a closed strategy's trades and realized total come back **real** — an empty discovery result is never "no trades." You do **not** hand-read `strategy_get_pnl_and_account_value_history` for this. If `trades[]` is still empty after the on-chain fallback, the book genuinely never traded (guardrail 9). How the recovery works: `references/sources.md`.

## Run it in steps — narrate as you go

A full review is several MCP round-trips; run as **ONE** call it can take minutes, blow the `exec` timeout,
and make you bail to raw MCP — which loses every guardrail. So run the review as **fast, resumable STEPS**
and **narrate each slice the moment it returns** (this mirrors `senpi-strategy-ops` `deploy.py`
create→runtime→verify: short steps over a shared state file, the skill narrates between). Each step is a
**separate `exec` call**, so your response streams and no single call hangs.

```sh
python3 scripts/review.py timing       # 1. fetch closed trades + prices → trades[] + timing_summary (FAST slice, narrate first)
python3 scripts/review.py strategies   # 2. per-strategy read (mandate/DSL + realized PnL) + closed rollup
python3 scripts/review.py telemetry    # 3. exit reasons + missed_signals + leaks + execution_quality (the slow event shell-outs, isolated)
python3 scripts/review.py market       # 4. book_vs_market (movers × held) — only if the ask needs it
python3 scripts/review.py all          # one-shot fallback: the full composed dict (same output as before)
```

**For a FULL review** — "analyze my strategies and trades", "master my week", "suggest improvements", "how
could I make more" — run the steps **in order** and narrate between:

1. `review.py timing` → narrate the timing teardown (the NEUTRAL exit counts `exits_ahead` / `exits_held_higher`
   + realized so far) — but this is NOT your headline: TOTAL PnL (realized + unrealized) lands with the
   `strategies` step (`pnl_summary.total`). `exit_reason` is still `UNKNOWN` here (telemetry hasn't run) —
   narrate the *timing*, not the mechanism yet, and never call `held_higher` "premature / left on the table."
2. `review.py strategies` → open with **`book`** (rows by value, `book.total.line`, the comparison —
   see "One book"), then narrate the **per-strategy read** (each CURRENT strategy vs its OWN mandate,
   realized PnL as evidence; `closed_strategies[]` is history — no verdict).
3. `review.py telemetry` → narrate **exit quality / leaks / blocked** (now `exit_reason` is filled: the
   refreshed `dsl_close_reason_mix`, `leaks`, `blocked_summary`, `execution_quality`).
4. `review.py market` → narrate the **book-vs-market gap** (run only if the ask needs "what did I miss /
   compare to market").

**After the last step the request needs**, end the answer with `book.deep_dive.question` when it is
non-null — once, never mid-review (One book, rule 6).

**Narrate each slice as it returns — never wait for all steps.** The steps share a state file
(`<tempdir>/senpi-improve-trades/state-<window>d.json`, overridable with `--state`), so a later step reuses
what an earlier one fetched instead of re-pulling. **For a NARROW ask, run only the minimal step(s)** from
the Router (e.g. "did I sell too early" → just `timing`; "where am I leaking" → just
`telemetry`) — faster, and each step self-heals its prerequisites so it also works standalone.

**Scope the ask.** When the user names a trade count — "improve my last 10", "review my last 20 trades" —
**pass `--last N`**: it's a GLOBAL bound (the N most-recent closed trades across the whole book), so the review
stays about what they asked instead of the entire 7-day book.

**`trades[]` is a curated OUTLIER SAMPLE, not the whole book.** When `trades_sample` is present, `trades_sample.total` is the real
count. **Take every COUNT and $ TOTAL from the aggregates** (`timing_summary`, `pnl_summary`,
`dsl_close_reason_mix`, `meta.trade_count`) — they cover ALL trades — and **never** compute a count from
`len(trades)` or imply the sample is the full ledger. Need the complete per-trade list (rare)? re-run with
`--full`.

## How to run (one-shot fallback)

```sh
python3 scripts/review.py                 # `all` (default): last ~7d composed review — the one-shot fallback
python3 scripts/review.py --window 30     # last 30 days
python3 scripts/review.py --last 20       # cap to the last 20 closed trades
python3 scripts/review.py --no-market     # skip the current-price + book-vs-market pull
python3 scripts/review.py --fixture tests/fixtures/review_fixture.json   # offline (tests)
```

Prefer the **steps** above for a full review (they stream and don't trip the timeout); use `all`
only when a single blocking call is fine. Read the JSON on stdout and narrate it under the guardrails. It
fails open end-to-end: partial data still returns valid JSON with `meta.warnings`; `meta.degraded` is set
when there's no usable data (usually a token-scope problem — say so, don't report "no trades" as if the book
were empty).

**The analysis MUST come from `review.py`. Never bypass it and hand-assemble the review from raw MCP
calls.** **If a
single call is slow, that's exactly why the steps exist — run `timing`/`strategies`/`telemetry`/`market` as
separate calls and narrate between** (each is fast and self-healing), or pass `--last 20` / `--no-market` to
trim — do NOT substitute raw tool calls for the engine's output, and do NOT let an `exec` timeout push you
to raw MCP. The only raw MCP you add is to go *beyond* the review (e.g. a live position detail the user asks
for), never to replace it.

## The nine guardrails — the reason this skill exists

These are non-negotiable. The live failure behind each, and the worked examples: `references/guardrails.md`.

### 1. TOTAL PnL leads; `if_held` is NEUTRAL context, symmetric, never the verdict

**Lead with `pnl_summary.total`** (realized closed + unrealized open) — NOT `realized_pnl_total` alone.
(Hard rule 1 has the "at least $X" floor.) Then give the aggregate exit counts (`timing_summary`: `exits_ahead` / `exits_held_higher` / flat). Only after
the aggregate may you mention a single trade, and only as *one data point*, never the headline.

`if_held_delta_usd` / `if_all_reclosed_now_total` are **NEUTRAL context, symmetric both ways — never a grade:**
- A **negative** aggregate → your exits, in aggregate, **got out ahead of reversals** (a *good* exit-discipline
  sign).
- A **positive** aggregate → the market kept running this window after your exits. This is **NOT** "money left
  on the table," and the `held_higher` count is **NOT** "premature exits." Report it as context
  ("held to now, the closed book would be +$X vs the realized +$Y"), **never** a target, a grade, or a reason
  to hold longer / loosen stops.

**The real evidence for "do you let winners run" is the OPEN book, not this counterfactual** —
`strategies[].open_positions[]` (what you hold now + its unrealized ROE). A book with open winners running IS
letting winners run, whatever the closed-trade counterfactual says.

**What `if_all_reclosed_now_total` is NOT:** the counterfactual on the **closed** trades only — it says nothing
about current OPEN positions or live drawdown; don't read it as "the book is bleeding."

### 1b. Peak-to-exit GIVE-BACK is the counterfactual that *is* diagnostic

Report **peak ROE, exit ROE, and the share of peak kept**, per trade and in aggregate. Habitually keeping a small share of peak means a lock too
low or a first rung arming too late — a named lever.

Keep **floor vs fill** separate: on AVAX the floor was +8.23% and it filled +7.67%, which is 0.55 ROE points of
execution slippage, not calibration.

### 2. It's the strategy, not you — fixes route to the strategy config

This section covers **Senpi strategy** trades. A saved wallet's trades are the user's own manual trades:
there is no strategy config to route to, so coach their process (see "Your wallets") and never pitch one.

These are **autonomous strategy** trades. The strategy exited them, not the user clicking sell. So **never**
say "you should have held" or "you sold too early." When the counterfactual favored holding (`held_higher`), the
lever — IF you decide to act at all — is the **strategy config** (the DSL preset / entry gates), reported in
strategy terms; `held_higher` is not itself a defect.
The engine gives you the exact lever: each trade's `exit_reason` (which DSL tier or hard stop fired) and each
strategy's `dsl` ladder (`hard_stop_roe_pct`, `arm_at_roe_pct`, the `tiers[]`). A fix reads like *"Kodiak's
SOL exit locked at tier 2 (+41% high-water) then trailed out; if you want it to ride further, that's the
phase-2 tier ladder, not anything you did"* — and it routes to **`senpi-strategy-author` / `senpi-strategy-ops`**
to apply. Frame every improvement as a strategy tune, never a user scolding.

**Config vs live state — do NOT confuse "not armed yet" with "not configured / unprotected."** Judge what a
strategy HAS from its **`dsl` ladder config** (`profile.dsl`: `hard_stop_roe_pct`, `arm_at_roe_pct`, `tiers[]`),
never from whether a live position has *armed* it:
- **Phase-1 hard stop protects from ENTRY.** A position sitting in phase 1 with no tier locked is **not**
  "unprotected" or "has no stop" — it has the phase-1 floor, and phase 2 simply hasn't **armed** yet because
  the position is **below the `arm_at_roe_pct` (Tier-1) threshold**. That's expected, not a gap. Never say
  "no stop-loss," "running unprotected," or "zero DSL protection" for a position whose strategy ships a DSL.
- **If `tiers[]` is present in the config, phase 2 IS configured** — do not say "no tier-2 locks exist" or
  recommend "add phase-2 tiers." If no live position has armed a tier, say *"phase-2 tiers exist (arm at
  +X%); nothing has reached the arm threshold yet"* — a fact, not a defect. Only recommend adding phase 2 if
  the config genuinely lacks `tiers[]`.
- The crypto exit problem is a **calibration** fix, not a missing-DSL fix: the `arm_at_roe_pct` or the phase-1
  `retrace` may be too tight for crypto's volatility relative to equities — that's the lever, stated from the
  config.
- **A genuinely naked position** (runtime deleted, position open on-chain, no DSL monitor) is a real, urgent
  thing — but only claim it when you can *show the runtime is gone*, not when a `ratchet_stop` record is
  merely empty (empty = sub-Tier-1, which is normal). If unsure, say "verify the runtime is tracking this
  position," not "it's unprotected."

### 3. No fabricated numbers — realized PnL + engine counterfactuals ONLY

**Never invent a forward number.** No "+$1,400–2,200/week," no "deploy 25% = +$800–1,200," no guaranteed-gain
figure of any kind. The only dollar figures you may state are:
- **realized PnL** (`realized_pnl`, `realized_pnl_total`) — what actually happened, and
- the engine's **counterfactuals** (`if_held_delta_usd`, `if_all_reclosed_now_total`) — clearly labeled as
  *"if held to now"* context.

If you feel the urge to annualize, weekly-ize,
or forecast a dollar figure, stop — that urge is the exact failure mode this skill exists to kill.

**Account value ≠ P&L — never narrate the value curve as trades.** The engine sources closed trades from on-chain fills, so you should not need `strategy_get_pnl_and_account_value_history` for a trade review at all. If you ever look at it: `accountValueHistory` is a series of account **snapshots**, not trades — never narrate "$135 → $133 → $143 → …" as individual round-trips. A curve ending at **`$0` after a `strategy_close` / `strategy_withdraw_funds` is capital RETURNED to the main wallet (a withdrawal), not a loss** — the realized result is realized PnL, not the value drop. If realized PnL (e.g. −$21) and the value drop (e.g. −$175) disagree, the difference **is the withdrawal** — reconcile, never report the drop as a loss.

### 4. No chasing — one window is noise; weigh mandate + turnover

Do **not** recommend abandoning a strategy's mandate to buy last window's winners. A `book_vs_market.gap` (a
mover the book didn't hold) is a **question to ask of the strategy's design**, not an order to go buy it. One
window is noise; weigh the strategy's mandate, the durability of the regime, and — critically — **turnover
cost** (fees compound on every rotation; chasing is how books bleed). "HYPE ran +18% and you weren't in it"
is only interesting if HYPE is *in the strategy's mandate* and the miss reveals a gate that's too tight —
otherwise it's just an asset the strategy was never designed to trade.

### 5. Inherit the portfolio rules — mandate, multi-wallet, idle-by-design

- **Judge each strategy against its OWN mandate** (`strategies[].mandate`, from the deployed `runtime.yaml`),
  not a generic momentum benchmark. Realized PnL is *evidence for the mandate verdict*, not the headline.
- **A strategy is ALL its wallets.** A multi-wallet strategy (long+short, core+ballast, multi-sleeve) is ONE
  strategy — **never** recommend closing / repurposing a single sleeve ("close the duplicate wallet"). One
  sleeve of a long/short book is a **naked directional position**, the opposite of the design.
- **NEVER recommend merging or closing one of two ACTIVE wallets that share `group` or `skill_name`.**
  They are the **`long` + `short` sleeves** (or `core` + `ballast`) of ONE multi-wallet strategy; merging
  collapses a market-neutral / two-speed book into a **naked directional bet**. Read the pairing off
  `group` (or `skill_name`) — **never off `label`**, which is each sleeve's OWN name and differs between
  them (`cougar-long` / `cougar-short`), as do their mandates. When **both are null** (registry unreadable
  and no attribution stamp) you cannot tell sleeves from redeployments: **do not recommend consolidating** —
  say "these two wallets are likely the long/short sleeves of one strategy — check the package before
  treating them as duplicates." The "consolidate these wallets" reflex is usually wrong; genuine *closed*
  redeployments live in `closed_strategies[]`, not `strategies[]`, and the live book size is
  `meta.current_strategy_count`, never `meta.strategy_count` (see rule 8).
- **A flat / idle / no-trades-this-window strategy is often by design** — the other sleeve waiting for its
  signal, or a patient strategy between setups. `on_mandate_note` flags this. Never call it "dead."

### 6. Honest sourcing — say what's missing, name your source

- **Undetermined ≠ all-clear (READ `telemetry_availability` FIRST).** `telemetry_availability.status`
  (`available`/`partial`/`undetermined`/`no_trades`) + `.streams_computed` tell you whether the telemetry
  streams are real. When `streams_computed` is **false** (telemetry down / older build / a closed ring), the
  `leaks`, `blocked_summary`, `execution_quality`, and `dsl_close_reason_mix` ZEROS are **fail-open
  placeholders, NOT findings** — report them as "couldn't check (telemetry unavailable)," **never** "no leaks /
  no protection gaps / no blocked signals / all clear." When `exit_attribution.attributed` is ~0 (status
  `undetermined`), **do NOT diagnose exit calibration at all** (no "phase-1 too tight," no "scanner false
  signals") — you have no attributed exit; say "exit mechanism undetermined — I'd need the runtime event log,"
  and stop.
- **Three things never need telemetry** — answer them before writing "undetermined": **maker vs taker**
  (`crossed` on every fill), **fees in $** (`fee`), and **peak ROE**
  (1m candles over the hold). Scope "undetermined" to blocked signals and protection gaps — and for *how the
  position closed*, work the ladder in 6b before you ever say you could not tell.
- When a price / horizon is missing, `if_held_delta_usd` is `null` and the trade counts as `exits_unknown` —
  say so ("couldn't compare — no current price"), don't invent a comparison.
- Read `meta.warnings` and surface material gaps plainly. Missing data is a caveat, not a thing to paper over.

### 6b. Why the runtime closed it — READ THE RECORD. Never infer a mechanism.

**The runtime archives every close with its reason.** There is nothing to deduce.

**A CLOSED position — `senpi dsl closes --runtime <id> --json`** ("archived closes with reason and ROE").
Each row is the answer *and* the details: `closeReason` (why it closed), `phase` + `currentTierIndex` (which
rung was governing), `currentROE` (the ROE it closed at — already at the REAL leverage, so quote it).

Translate `closeReason`, never paste the enum: `exchange_sl_hit`, `dsl_breach`, `hard_timeout`,
`weak_peak_cut`, `dead_weight_cut` (what each means: `references/exit-records.md`).

**`hard_timeout`, `weak_peak_cut` and `dead_weight_cut` are TIMERS, not reactions to price, and none is
phase-scoped.** State the configured
interval and the actual hold, or the reason is not explained: *"held 5.03h against a 300-minute
`weak_peak_cut` with `min_value` 5.0 — it never cleared +5% ROE in that window, so the slot was freed."*
Saying it "detected the position rolling over" or "cut early before the hard stop" invents a mechanism —
a real review did exactly that on a position whose hold time (5.03h vs a 300m timer) was the giveaway.

**A LIVE position — `senpi dsl inspect <asset> --json`** returns the full `DslState`, which already holds
every number users ask for. Quote these rather than computing anything: `floorPrice` / `tierFloorPrice`
(where the stop is), **`lockedProfitPct`** (what is locked in *right now*), `highWaterRoe` / `peakROE`,
`currentTierIndex` + `phase`, `distanceToNextTierPct` — field by field in `references/exit-records.md`.

Two more: **`senpi dsl positions --json`** (what is under DSL management at all — the answer to "is this
protected?") and **`senpi explain <asset>`** (the stitched opened → dsl → closed narrative).

**If the gateway is down**, `senpi dsl closes` exits non-zero and prints *"start it with: openclaw gateway
run"*. **Say that.** An unreadable record is an unreadable record — do not fall back to guessing a mechanism
from fill shapes and present it as fact.

**If the record cannot be read, say so.** Fill shapes corroborate, they never substitute: a burst of
`crossed` close fills at one timestamp fits `exchange_sl_hit`, but a maker fill shows the **order type, not
who placed it** (`close_position` uses `FEE_OPTIMIZED_LIMIT` too, and a DSL exit set to `order_type: MARKET`
produces none) — so it can rule the exchange stop *out*, never rule the DSL *in*. A closed trade has no
*resting* trigger left; read the one that fired from `execution_get_closed_position_details` →
`orderDetails[].triggerPrice`. And `exit_reason.terminal` of **`CLOSED_OFF_LADDER` is not an answer** — it
means only "ended for a reason that was not its own stop", so treat it as UNKNOWN.

Label the source in the answer: *"the runtime's close record says …"* reads differently from
*"the fills are consistent with …"*, and the user is entitled to know which one they are getting.

### 7. The user chooses the fix depth — never auto-act, and never offer a tune off one trade

After you diagnose, **offer a choice and stop.** Never apply a config change or place a trade. Present three
depths (below) and let the user pick. On a saved wallet the same three are advice the user applies on
Hyperliquid — see "Fix depth on a saved wallet".

**Gate the two config branches on sample size.** With **fewer than ~8 attributed closes on that strategy**,
offer "explain only" and say the sample is too small to tune on, naming what more closes would measure. A lever
is worth pulling when the pattern survives dropping any single trade — not when one trade disappointed.

### 8. Verdicts are for the CURRENT book only — closed strategies are HISTORY, not a live problem

The engine enumerates strategies of *all*
statuses so a churned book's **closed trades** are captured — but a closed strategy is **history**, not a
current strategy to analyze, consolidate, or fix.

- **`strategies[]` = the CURRENT book ONLY** (`status` ACTIVE or PAUSED). **Every** per-strategy verdict and
  **every** improvement recommendation ("doing its job" / "consolidate" / "kill" / "fix the DSL") is for
  these and these alone.
- **`closed_strategies[]` = HISTORY** (CLOSED / INACTIVE / retired). Their **trades are part of the timing
  review** (they're in `trades[]`, attributed by label + `strategy_status`) — but the strategies themselves:
  - **Never** give a closed strategy a "doing its job / consolidate / kill / fix" verdict. It's *already
    closed* — there is nothing to fix or decide.
  - **Never** flag a closed strategy's absent mandate/DSL as a bug. It's **deregistered because it's closed**
    — the missing `runtime.yaml` is *expected*, not a "DSL registration bug." Say nothing about it.
  - **Never** build a "you have N wallets, consolidate them" narrative out of closed/historical deployments.
    The live book = `meta.current_strategy_count`. Same-label wallets that are closed are **redeployments over
    time** (redeploy history), **not concurrent live redundancy**.
- **A missing mandate on a CURRENT strategy** → note it **plainly**: *"mandate unavailable — look it up /
  check the runtime registry."* It is a lookup, **not a bug to fix.** (`on_mandate_note` already phrases this.)
  On a **closed** strategy → say **nothing** about the missing mandate.
- **`dsl: null` / `mandate: null` means the registry wasn't readable on this build — NOT that the strategy is
  unprotected. This is a hard rule.** Every template-deployed strategy **ships a DSL exit by construction**
  (the author validator refuses to build one without it). So a `null` `dsl` in this output is a
  **data-availability gap** (the mandate/DSL source — `installed_runtimes.json` — wasn't found on this host,
  same root as `meta.registry_source == null`), never evidence of a naked strategy. **NEVER** say a strategy
  "has no DSL / no exit protection / is running unprotected," **never** recommend "add DSL," and **never**
  invent DSL tier numbers to fix it. If the user wants a strategy's real DSL, look it up (its `runtime.yaml`
  package), don't infer absence from a `null`.
- **Attribute every trade to its actual `strategy_label` from the trade record — never speculate** ("*likely*
  from the old X strategy"). The engine tags each trade with its strategy; use it. If two same-label wallets
  diverge (one winning on equities, one losing on crypto), say exactly that from the data — don't guess a
  mandate for a trade.

### 9. No trades yet? Stop cleanly — do NOT pivot to setup / config nagging

**An empty `trades[]` now means the book genuinely never traded** — the engine already falls back to on-chain HL fills for closed strategies, so it is **not** a coverage gap you need to work around. Do **not** tell a user with closed strategies "you have zero trades" without trusting the engine: if trades existed, the on-chain fallback returned them. When trades were recovered that way (`meta.closed_trade_source == "onchain_fills"`), say so plainly — *"these are your closed strategies' trades, recovered from the chain"* — and never bypass the engine to hand-read `strategy_get_pnl_and_account_value_history` and narrate its curve (that reproduces the withdrawal-as-loss failure — guardrail 3).

A trade review with **no closed trades** — especially **brand-new strategies deployed today with no
positions yet** — is a **complete, correct result**: *"nothing to review yet."* Say that and stop. The
strategies are **autonomous**; they open positions on their own when their scanners fire. **Do not
manufacture a setup/config critique to seem useful.**

- **Never tell the user to "fund a position," "open a position first," "decide which gets action first," or
  "manually exit."** An autonomous strategy trades **itself** on its signal — the user does not hand-open or
  hand-close its positions. A fresh strategy with zero positions is **waiting for its first signal** (rule 5,
  idle-by-design), **not** "$X in budget earning nothing." Zero exposure on a new autonomous strategy is
  normal, not a leak — never frame it as one.
- **Never re-raise DSL / "might run naked" alarms** ("verify the DSL is configured… without DSL positions run
  naked"). Authored + template strategies **ship a DSL exit by construction** (rules 2 & 8) — don't tell the
  user to "verify" protection that exists by design, and never pair it with "until you manually exit."
- **Never give generic execution / config-tuning advice** — slippage, market-vs-limit, leverage. It's out of
  scope for a *trade review*, it second-guesses a strategy that was just set up, and nudging toward MARKET /
  higher-slippage fills **raises fees** — the biggest killer of returns. `slippage: 0`
  is not a defect to "fix." If the user explicitly asks about execution, hand it to `senpi-strategy-author` / `senpi-strategy-ops`;
  don't free-style a number.

## Composition — this skill orchestrates, it doesn't re-implement

This is the counterpart to `senpi-portfolio`. Portfolio answers *"where is my money / how are my strategies
doing right now."* **Improve-trades answers *"how did my closed trades do, what did the market do, and how do
I get better."*** Use this skill for retrospective / "review my trades" / "what did I miss" / "how do I make
more" questions; use `senpi-portfolio` for live state.

- **`senpi-market-pulse`** → the movers narrative (what moved this window and why).
- **`senpi-smart-money`** → the whale read ("compare my trades to the best whales").
- **`senpi-portfolio`** → live positions, balances, and the current mandate/DSL posture.

Don't re-implement these — call them and weave their output into the four-part contract below.

## The output contract — what you produce

0. **One book** — `book.rows` in the engine's order, `book.total.line`, `book.comparison.line` (when
   non-null). The parts below are the detail. `book.deep_dive.question` (when non-null) comes last of
   all — after the detail and the steps the request needs, never here.
1. **Total-PnL + timing teardown** — **led by TOTAL PnL** (`pnl_summary.total` = realized + unrealized), then
   the NEUTRAL aggregate (`timing_summary`: `exits_ahead` / `exits_held_higher`), each exit attributed via
   `exit_reason` (which tier / hard stop fired). Process-framed, NEVER "premature / left on the table."
   Discuss individual reversals only after the aggregate, and only as evidence. Trades whose
   `strategy_status` isn't ACTIVE/PAUSED are **history** (from a closed strategy) — narrate them as past
   timing, attributed by label, never as a live strategy to act on.
2. **Book-vs-market gap** — what moved vs what you held (`book_vs_market`). The honest "what did I miss." For
   the whale angle, **compose `senpi-smart-money`** (the engine gives smart-money concentration per mover, but
   smart-money does the deep whale read). For the movers narrative, **compose `senpi-market-pulse`**.
3. **Per-strategy read** — **`strategies[]` (the CURRENT book only) is the sole verdict surface.** Judge each
   against its **own mandate** (`strategies[].on_mandate_note`), realized PnL as evidence. Not a momentum
   benchmark. Any `closed_strategies[]` are **history** — their trades already count in the timing teardown
   (step 1, attributed by label); do **not** give them a verdict, do **not** flag their missing mandate, and
   do **not** spin them into a "consolidate N wallets" recommendation (guardrail 8; the live book size is
   `meta.current_strategy_count`). For live positions/state, **compose `senpi-portfolio`**.
4. **Improvements** — each tied to a concrete **strategy lever** (a DSL tier, the hard stop, an entry gate),
   **no guaranteed-gain language**, then the fix-depth choice:

> **How deep do you want me to go?**
> - **Explain only** — I lay out the diagnosis + the plain-terms fix, and stop.
> - **Hand it to the strategy tools** — I route the concrete change (e.g. "loosen Kodiak's phase-2 tier 2
>   lock so SOL rides further") to `senpi-strategy-author` / `senpi-strategy-ops` to apply.
> - **Draft the config change** — I produce the exact `runtime.yaml` / DSL-preset diff for you to review
>   before anything is applied.

Never pick for the user, and never act unprompted.

## Reference index — read the file before doing the thing

- `references/guardrails.md` — before coaching an exit, a missed mover, a "make more" or a "why is it
  losing": the live failure behind each guardrail and its worked example.
- `references/exit-records.md` — before explaining why or how a position closed, what is locked now, or a
  `senpi explain` lifecycle: every close-record and `DslState` field.
- `references/one-book.md` — before narrating a partial subtotal, the comparison or the deep-dive question.
- `references/sources.md` — before explaining where a trade, an exit reason or a fee $ came from, or why a
  closed strategy's trades came back from the chain.
- `references/output-contract.md` — before a full review or a fix-depth pick: the output contract,
  Composition and both fix-depth menus, word for word.
- `references/output-shape.md` — when an engine field's meaning is unclear.
- `references/event-log.md` — the telemetry event shapes the engine reads (`signal.outcome` reason codes).
