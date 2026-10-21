# senpi-improve-trades 1.20.0: dropped, reworded and moved-out-of-core sentences

The restructure moved SKILL.md from 63,956 to 54,066 est chars. Every other sentence of the 1.19.1 SKILL.md is
still in `SKILL.md` or moved verbatim to a `references/*.md` file (the conservation script checks this). This
lists what was reworded or split, every imperative that left core, and the rules 5.7 no longer keeps on a
follow-up, so reviewers need not read the diff.

## Reworded (the old wording no longer exists)

- "Quick actions this skill handles" → heading is now "Quick actions — the data and the lever per intent".
  Its Step(s) column moved to the Router at the top; its Data column moved to `references/output-shape.md`.
- "Each intent maps to the **minimal engine step(s)** to run (fastest for a narrow ask — see "Run it in
  steps"), a specific engine output (its data), and a specific **actionable lever** (the fix)." → split into
  three places, so the sentence now says the steps are in the Router and the data in output-shape.md. Its
  second sentence ("Run only the step(s) the ask needs; route every fix through the depth choice at the end —
  never auto-act.") is kept verbatim, in the Router.
- "**For a NARROW ask, run only the minimal step(s)** from the Quick-actions "Step(s)" column (e.g." → "from
  the Router (e.g." because that column moved there.
- "What the engine gives you" (heading) and "**Full field-by-field catalog + the `exit_reason.terminal` enum:
  [`references/output-shape.md`](references/output-shape.md).**" → the paragraph moved to output-shape.md
  "Reading order" (it summarizes rules that stay in core: One book, hard rule 1, the outlier sample). The link
  now reads "below".
- "Don't re-implement these — call them and weave their output into the four-part contract above." →
  "below": the output contract now follows Composition, at the end of the file.
- Guardrail 1b: "Keep **floor vs fill** separate: on AVAX the floor was +8.23% and it filled +7.67%, which is
  0.55 ROE points of execution slippage, not calibration." → core keeps the rule without the numbers ("the
  gap between the floor and the fill is execution slippage, not calibration"). The AVAX version is in
  guardrails.md.
- Guardrail 4: ""HYPE ran +18% and you weren't in it" is only interesting if HYPE is *in the strategy's
  mandate* and the miss reveals a gate that's too tight — otherwise it's just an asset the strategy was never
  designed to trade." → core keeps the test in general terms ("A missed mover is only interesting if it is in
  the strategy's mandate and the miss reveals a gate that's too tight"). The HYPE example is in guardrails.md.
- Guardrail 2: "A fix reads like *"Kodiak's SOL exit locked at tier 2 …"* — and it routes to
  **`senpi-strategy-author` / `senpi-strategy-ops`** to apply." → core keeps "Route the fix to
  `senpi-strategy-author` / `senpi-strategy-ops` to apply." The Kodiak example is in guardrails.md.
- Guardrail 1: the "Degraded-read honesty" paragraph moved (it restated hard rule 1 word for word in meaning).
  Core now says "NOT `realized_pnl_total` alone (hard rule 1, including the "at least $X" floor)".
- Guardrail 6b: the `senpi dsl closes` field table became one sentence naming the same three fields, and the
  `DslState` field list became one sentence naming the same fields. Full tables are in exit-records.md.
  "Translate `closeReason`, never paste the enum:" now names the five reasons inline and points to
  exit-records.md for the meanings. "**The three above are TIMERS, not reactions to price, and none is
  phase-scoped.**" now names the three (`hard_timeout`, `weak_peak_cut`, `dead_weight_cut`), because the list
  above it moved. "Saying it "detected the position rolling over" or "cut early before the hard stop" invents a
  mechanism — a real review did exactly that…" → core says "Never say it "detected the position rolling over"
  or "cut early before the hard stop" — that invents a mechanism." The incident is in exit-records.md.
- Guardrail 8: "This is the split that stops the worst live-run failure." moved to guardrails.md. The next
  sentence ("The engine enumerates strategies of *all* statuses …") is unchanged.
- "The nine guardrails" intro: "Each fixes a real failure from live agent responses to these prompts." moved
  to guardrails.md. Core now says "The live failure behind each, and the worked examples:
  `references/guardrails.md`."
- "The one pending upgrade — authoritative fee $" section: its imperative sentence stays in core verbatim
  ("That per-order join is the future upgrade; it's intentionally **not** called per-trade (rate-limit risk), so
  until it's wired, quote the maker ratio as a fee-tier signal, never a fee dollar total.") with
  "`execution_quality` reports the maker-vs-taker **rate** today, not fee dollars." under "**Fee $:**" in Quick
  actions. The rest of the section is in sources.md.
- Sources: "**Telemetry is read ONLY from the current book.** … A closed strategy's exit reasons therefore come
  from the ratchet record or honest `UNKNOWN` …" kept; the CPU-starvation explanation between them moved to
  sources.md, so "therefore" now follows the bold rule directly.

- Hard rule 0b "The venue caps leverage per asset and **clips silently**: `strategy_create_custom_strategy`'s
  own schema warns "EXCEEDING THE ASSET CAP DOES NOT ERROR", so a strategy asking `default_leverage: 10` opens
  PONS at **3x**." → core keeps the rule and its clause ("**clips silently**: `default_leverage` is a request,
  not what opened"); the schema quote and the PONS example are verbatim in `guardrails.md` ("Hard rule 0b").
- guardrails.md "See the "Sources — onchain vs runtime" note above." and guardrail 9's "**What TO say instead
  — branch on `meta.book_state` (see "Nothing to review?" above):**" → "… in SKILL.md": both sections stayed in
  SKILL.md.

## Split across files (each part kept verbatim, the line just no longer exists in one file)

- "*"… here are the strategies built for that."* A strategy matched to the current regime is a far better
  first experience than a generic top-N list." → the quote stays in SKILL.md, the second sentence (rationale)
  is in guardrails.md.
- "**Comparison: quote `book.comparison.line` after the table when it is non-null.** The engine computes it —
  …" → the rule stays in SKILL.md, the description of what the engine computes is in one-book.md.
- "**`trades[]` is a curated OUTLIER SAMPLE, not the whole book.** To keep the model-context payload small …"
  → the rule stays in SKILL.md, the why is in sources.md.
- "…from raw MCP calls.** The engine is the entire point — it computes the timing table, the exit reasons, the
  mandate reads, …" → the rule stays in SKILL.md, the why is in sources.md.

## Imperatives that left core

Each of these is still stated in core in other words; the maintainer's rule is that the words stay too, so
each one needs the maintainer's OK.


- Guardrail 6 bullets "**Name your source (onchain vs runtime).** …" and "**Exit attribution is
  telemetry-first, ratchet-fallback.** …" → guardrails.md. Core keeps the same rules in "Sources — onchain vs
  runtime" (never mix the sources; say "exit mechanism not recorded on this build"; surface
  `meta.telemetry_source` / `meta.exit_reason_source_counts`), "Use this skill FIRST" (never `audit_*`) and
  6b ("Never infer a mechanism").
- Guardrail 9 "**What TO say instead — branch on `meta.book_state`** …" (three bullets) → guardrails.md. Core
  keeps the same branch in the `meta.book_state` table ("Diagnose the strategy they already have. Never pitch
  another one."), the `no_strategies` section (pivot to market-pulse then discover; "Idle cash is an option,
  never a loss") and guardrail 9's first bullet ("Zero exposure on a new autonomous strategy is normal, not a
  leak").
- One book rules 2, 3 and 6, the partial and gate long tail → one-book.md: "`value_read: "partial"` → … say
  "(1 of 2 wallets)" …"; "A subtotal over only some of its wallets says so …" (the engine already writes
  `book.total.line` that way, and core says to quote it verbatim); "`order` holds only wallets with a value …"
  (engine behaviour).
- 6b "**If the gateway is down**, `senpi dsl closes` exits non-zero and prints *"start it with: openclaw gateway
  run"*. **Say that.** …" → exit-records.md (an exit-code edge case). Core keeps "**If the record cannot be
  read, say so.** Fill shapes corroborate, they never substitute".
- "**Scope the ask.** … **pass `--last N`** …" → sources.md. Core keeps the imperative verbatim in the Router
  ("**pass `--last N`** when the ask names a count").

### Every moved line, quoted

The conservation check's own list (28 moved lines carrying never / always / must / do not / only /
verbatim, quoted as 23 entries), grouped by the reference that holds each now and the router row that
reaches it. It overlaps the summary above and is the complete set.

#### `references/exit-records.md` — reached by router rows "shaken out / how my exits fire / why did it close / what is locked now" and "walk me through my [asset] trade"

- Translate `closeReason`, never paste the enum:
- **`weak_peak_cut`** — never reached `min_value` ROE inside the window.
- **Say that.** An unreadable record is an unreadable record — do not fall back to guessing a mechanism from fill shapes and present it as fact.

#### `references/guardrails.md` — reached by the router rows that name `guardrails.md` (sell too early, what did I miss, make more gains, why is it losing) and "The nine guardrails" pointer

- A live review headlined **+$410.14** on a trade whose net was **+$310.23** ($99.92 of fees), and it never mentioned fees at all.
- **The mistake to avoid:** telling someone whose funded strategy is silently blocked to "go find a strategy that fits the market." They don't need another strategy — they need to know why the one they already paid for is idle.
- Realized-only is half the ledger: it calls a book riding open winners a "loser" and penalizes hold-strategies.
- **Degraded-read honesty (same principle as `undetermined ≠ all-clear`):** if `pnl_summary.unrealized_partial` is true (some current wallets read, some didn't), `total` is a **FLOOR** — headline it as *"at least $X (N of M wallets readable)"*, never as a complete total.
- **Account value ≠ P&L — never narrate the value curve as trades.** The engine sources closed trades from on-chain fills, so you should not need `strategy_get_pnl_and_account_value_history` for a trade review at all.
- "HYPE ran +18% and you weren't in it" is only interesting if HYPE is *in the strategy's mandate* and the miss reveals a gate that's too tight — otherwise it's just an asset the strategy was never designed to trade.
- **Name your source (onchain vs runtime).** Closed trades + every onchain fact come from **`discovery`** (`discovery_get_trader_history`) — **never `audit_*`** (deprecated).
- When it's `UNKNOWN` — no telemetry event and no ratchet record — say *"exit mechanism not recorded on this build"* (or, for a closed strategy, *"…its event ring is gone"*), **never guess** the mechanism.
- **Do not pitch another strategy to someone whose strategy hasn't traded.**
- Either way, next moves are **options, not obligations**, and idle embedded cash is an **opportunity to deploy if they want**, never a "$0/day leak."

#### `references/one-book.md` — reached by router row "the wallet picked from `book.deep_dive.question`" and the One book pointers

- `value_read: "partial"` → a strategy whose value covers only some of its wallets: say "(1 of 2 wallets)" from `wallets_loaded` / `wallet_count`. *(After review a short form is back in core, One book rule 2: "`value_read: "partial"` → say "(1 of 2 wallets)" from `wallets_loaded` / `wallet_count`.")*
- A subtotal over only some of its wallets says so ("1 of 2 wallets"); one whose wallets all failed is "unknown", never $0; with no saved wallets the line has no read-only clause.
- **Comparison: quote `book.comparison.line` after the table when it is non-null.** The engine computes it — of the closes measurable against today's price, the share that got out ahead of the later move (hindsight context, never a grade: guardrail 2) — only when 2+ wallets have `min_closes` (8) measurable closes, and it names any wallet whose trades it couldn't read or that hit the fill ceiling.
- `order` holds only wallets with a value, an open position, trades to review or a value that couldn't load — an empty $0 wallet stays in the table, never in the question.

#### `references/output-shape.md` — reached by router rows "what my limits blocked" and "where am I leaking"; "when an engine field's meaning is unclear"

- **Open with `book`** (the one list, then `book.total.line`); within the Senpi detail, **lead with `pnl_summary.total`** (realized+unrealized); the PROCESS counts are in `timing_summary`; per-strategy verdicts in `strategies[]` (CURRENT book only); the telemetry streams (`dsl_close_reason_mix` / `blocked_summary` / `leaks` / `execution_quality`) are real only when `telemetry_availability.streams_computed` is true; `trades[]` is a curated outlier sample (counts from the aggregates, never `len(trades)`).

#### `references/sources.md` — reached by router rows "why did it close" and "where am I leaking, fees", and the Sources section pointer

- **Telemetry is read ONLY from the current book.** The event-log ring lives on-disk with a running runtime, so the engine shells `openclaw senpi events` **only for ACTIVE/PAUSED strategies** — a *closed* strategy's ring is torn down with its runtime.
- Same fail-open contract as `all`: each step returns valid JSON with `meta.warnings` on partial data and never crashes on a missing/corrupt state file (it recomputes).
- **`trades[]` is a curated OUTLIER SAMPLE, not the whole book.** To keep the model-context payload small (the raw ledger on a big multi-strategy account is tens of thousands of tokens and blows the delivery timeout), the engine emits only the top handful of trades by biggest realized PnL + biggest hold-to-now delta — the ones a coach actually cites.
- (The telemetry step still event-fetches every *current* strategy regardless of `--last` — those events also feed `missed_signals`/leaks — but a **closed** strategy is never probed, which is what removes the heavy fan-out on a large, churned book.)
- `all` (the default with no step) composes every slice into one dict — the same output the engine always produced.

## Rules that no longer replay on 5.7 follow-ups

At `b1916b12` 5.7 kept head 0–11,896 + tail 60,031–end of SKILL.md on every follow-up (the word "total"
near the end switched it to head+tail). The file is now head-only: 5.7 keeps its first ~15,900 chars
(`openclaw_measure.replay_57`). The Router, the follow-up rule, One book, Your wallets, HARD RULES 0–5,
"these five are the floor" and "Use this skill FIRST" all replay; One book and Your wallets did not replay at
`b1916b12`. `test_improve_trades_skill_layout.py` pins them and the mode. These base-replayed imperatives are
past the cut now (all still in SKILL.md, word for word); what the agent still sees on a follow-up:

| No longer replayed | Still replayed instead |
|---|---|
| "Sources — onchain vs runtime": "Never mix them, and never reconstruct one from the other"; discovery owns the onchain facts; telemetry "never becomes the trade list and never re-derives a price or PnL"; "Telemetry is read ONLY from the current book" | "Still true on every follow-up": "Sources. Discovery owns the trade list and its onchain facts; telemetry (current book only) owns exit reasons and blocked signals: never mix them or rebuild one from the other." |
| "A ratchet-sourced exit never names who closed the trade … never "you closed this" … never print the raw status" | "Still true…": "A ratchet-sourced exit never names who closed it: "not closed by its stop", never the raw status." |
| Telemetry unavailable: "Say **"exit mechanism not recorded on this build"** … — **never** report it as a bug." | "Still true…": "No telemetry: "exit mechanism not recorded on this build", never a bug." |
| The output contract: "Process-framed, NEVER "premature / left on the table."" | HARD RULE 2: "NEVER say "you exited too early / N% premature / left $X on the table"". |
| "Discuss individual reversals only after the aggregate, and only as evidence." | "Still true…": "Reversals only after the aggregate, as evidence." |
| Closed strategies: "never as a live strategy to act on"; "do **not** give them a verdict, do **not** flag their missing mandate, and do **not** spin them into a "consolidate N wallets" recommendation" | "Still true…": "Closed strategies are history: no verdict, no "consolidate"." |
| "`book.deep_dive.question` (when non-null) comes last of all — after the detail and the steps the request needs, never here." | One book rule 6 ("ask `book.deep_dive.question` verbatim", at the end of the answer). |
| The fix-depth menus' option descriptions ("I lay out the diagnosis + the plain-terms fix, and stop." and the others); "Never pick for the user, and never act unprompted." | "Still true…": "Fix depth. End every improvement with "How deep do you want me to go?" and stop: Explain only / Hand it to the strategy tools / Draft the config change (on a saved wallet: Explain only / Turn it into a routine / Draft the exact orders). Never pick the fix depth for the user." (the question and every option name, word for word; a test keeps them equal to the menus); the Router: "route every fix through the depth choice at the end — never auto-act". |
| "quote the maker ratio as a fee-tier signal, never a fee dollar total" | "Still true…": "The maker ratio is a fee-tier signal, never a fee dollar total." |
| Composition: "Don't re-implement these — call them and weave their output into the four-part contract below." | Nothing in the replay (a first-turn rule about which skills to call). |
| Hard rule 0's incident ("A live review headlined **+$410.14** … it never mentioned fees at all.") | Hard rule 0 itself replays; the incident is in `guardrails.md`. |

## Added (new text, not in 1.19.1)

- After review: the Router's "compare to the market" row also reads "/ the whales" and names
  `senpi-smart-money` (the old Quick-actions row "Compare me to the whales / the market"); the Shape line
  points to "Quick actions" for the maker ratio; guardrail 9 and "Use this skill FIRST" keep their reason
  clauses ("raises fees", "un-attributed dumps").
- The Router and the follow-up rule ("**On a follow-up, answer only what is new** — never re-send the `book`
  table, `book.total.line`, the TOTAL PnL headline or a figure already shown …"), per the placement rules for
  a SKILL.md over 16k. The Router's Run cells are the old Step(s) cells, verbatim.
- One-line pointers to the new references, and the reference index at the end of the file.
- "Still true on every follow-up" after the Router: a short restatement of the Sources and output-contract
  rules that no longer replay (previous section), and the fix-depth menus by their question and option
  names. The full rules and menus stay where they were.
- "## Your wallets — unknown reads and fix depth": the "Unknown is never empty" and "Open positions" bullets
  of Your wallets and "Fix depth on a saved wallet", moved below HARD RULES and "Use this skill FIRST" so
  those replay; the text is unchanged. 5.7 keeps only its heading and the "Unknown is never empty" lead-in;
  the rest is past the cut (none of it replayed at `b1916b12`: it sat in the middle of the file).
