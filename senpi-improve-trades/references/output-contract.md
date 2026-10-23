# Output contract — the full-review shape and both fix-depth menus

Word-for-word copies of three `SKILL.md` sections, so a re-read on a follow-up finds them whole: on
OpenClaw 5.7 a follow-up turn keeps only the first ~15.9k chars of `SKILL.md`, and these sit past that.
`SKILL.md` is the rule; a test keeps each copy the same as its section there. Read this before a full
review ("master my week", "suggest improvements") and when the user picks a fix depth.

## Composition — what the engine hands each skill

Moved from `SKILL.md` Composition (engine notes, not rules): The engine's
`book_vs_market.top_movers` is the hook; market-pulse is the depth. The engine surfaces
`smart_money_pct` per mover; smart-money does the trader-level comparison.

## Copy: SKILL.md "Composition — this skill orchestrates, it doesn't re-implement"

This is the counterpart to `senpi-portfolio`. Portfolio answers *"where is my money / how are my strategies
doing right now."* **Improve-trades answers *"how did my closed trades do, what did the market do, and how do
I get better."*** Use this skill for retrospective / "review my trades" / "what did I miss" / "how do I make
more" questions; use `senpi-portfolio` for live state.

- **`senpi-market-pulse`** → the movers narrative (what moved this window and why).
- **`senpi-smart-money`** → the whale read ("compare my trades to the best whales").
- **`senpi-portfolio`** → live positions, balances, and the current mandate/DSL posture.

Don't re-implement these — call them and weave their output into the four-part contract below.

## Copy: SKILL.md "The output contract — what you produce"

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

## Copy: SKILL.md "Fix depth on a saved wallet — advice they apply on Hyperliquid"

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
