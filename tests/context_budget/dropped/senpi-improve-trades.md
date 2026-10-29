# senpi-improve-trades 1.20.2: dropped, reworded and moved-out-of-core sentences

The restructure moved SKILL.md from 63,956 (1.19.1) to 54,319 (1.20.0) est chars; 1.20.1 puts rules back in
core word for word and moves more of them into the 5.7 follow-up replay, at 55,569; 1.20.2 copies two
rules short into the replay after the Box A live test (2026-10-22), at 55,681. Every other sentence of
the 1.19.1 SKILL.md is still in `SKILL.md` or moved verbatim to a `references/*.md` file (the conservation
script checks this). This lists what was reworded or split, every imperative that left core, and where each
rule sits on a 5.7 follow-up, so reviewers need not read the diff.

## Reworded (the old wording no longer exists)

Each entry says why the base wording could not stay. Rules got their base wording back in 1.20.1 (see
"Imperatives that left core"); what is left here is either not a rule, or a rule whose words are unchanged
except a pointer that would now point at nothing.

Not rules (headings, descriptions, justification and examples):

- "Quick actions this skill handles" → heading is now "Quick actions — the data and the lever per intent".
  Its Step(s) column moved to the Router at the top; its Data column moved to `references/output-shape.md`.
- "Each intent maps to the **minimal engine step(s)** to run (fastest for a narrow ask — see "Run it in
  steps"), a specific engine output (its data), and a specific **actionable lever** (the fix)." → a
  description of the table, split into three places: the intro now says the steps are in the Router and the
  data in output-shape.md. Its second sentence ("Run only the step(s) the ask needs; route every fix through
  the depth choice at the end — never auto-act.") is kept verbatim, in the Router.
- "What the engine gives you" (heading) and "**Full field-by-field catalog + the `exit_reason.terminal` enum:
  [`references/output-shape.md`](references/output-shape.md).**" → the paragraph moved to output-shape.md
  "Reading order" (a summary of rules that stay in core, see "Rules stated twice" below).
- Guardrail 6b: the `senpi dsl closes` field table became one sentence naming the same three fields, and the
  `DslState` field list became one sentence naming the same fields (glossary; full tables in
  exit-records.md). "Translate `closeReason`, never paste the enum:" is word for word and is followed by the
  five reasons inline (a test keeps them equal to exit-records.md's vocabulary) and a pointer to
  exit-records.md for the meanings.
- Guardrail 8: "This is the split that stops the worst live-run failure." moved to guardrails.md
  (rationale). The next sentence ("The engine enumerates strategies of *all* statuses …") is unchanged.
- "The nine guardrails" intro: "Each fixes a real failure from live agent responses to these prompts." moved
  to guardrails.md (rationale). Core now says "The live failure behind each, and the worked examples:
  `references/guardrails.md`."
- "The one pending upgrade — authoritative fee $" section: its imperative sentence stays in core verbatim
  ("That per-order join is the future upgrade; it's intentionally **not** called per-trade (rate-limit risk), so
  until it's wired, quote the maker ratio as a fee-tier signal, never a fee dollar total.") with
  "`execution_quality` reports the maker-vs-taker **rate** today, not fee dollars." under "**Fee $:**" in Quick
  actions. The rest of the section (engine notes) is in sources.md.
- Sources: "**Telemetry is read ONLY from the current book.** … A closed strategy's exit reasons therefore come
  from the ratchet record or honest `UNKNOWN` …" kept; the CPU-starvation explanation between them moved to
  sources.md, so "therefore" now follows the bold rule directly.
- Hard rule 0b "The venue caps leverage per asset and **clips silently**: `strategy_create_custom_strategy`'s
  own schema warns "EXCEEDING THE ASSET CAP DOES NOT ERROR", so a strategy asking `default_leverage: 10` opens
  PONS at **3x**." → the rule is "**Never take leverage from the config — read what the position ACTUALLY
  opened at.**", unchanged. This sentence is its justification and example: core keeps the one clause
  ("**clips silently**: `default_leverage` is a request, not what opened", CLAUDE.md's one-clause rule, in the
  replay); the schema quote and the PONS example are verbatim in `guardrails.md` ("Hard rule 0b").

Rules whose words are unchanged except a pointer (the base pointer names something that moved):

- "**For a NARROW ask, run only the minimal step(s)** from the Quick-actions "Step(s)" column (e.g." → "from
  the Router (e.g." because that column moved there.
- "Don't re-implement these — call them and weave their output into the four-part contract above." →
  "below": the output contract now follows Composition, at the end of the file.
- Guardrail 6b: "**The three above are TIMERS, not reactions to price, and none is phase-scoped.**" →
  "**`hard_timeout`, `weak_peak_cut` and `dead_weight_cut` are TIMERS, …**": the list "above" is the
  glossary that moved to exit-records.md, so core names the three.
- Guardrail 1: "**Lead with `pnl_summary.total`** (realized closed + unrealized open) — NOT
  `realized_pnl_total` alone." is word for word again (1.20.1); the pointer "(Hard rule 1 has the "at least $X"
  floor.)" follows it in place of the "Degraded-read honesty" paragraph, which restated hard rule 1 (see
  "Rules stated twice").
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
- 1.20.1: "**The fee rate is NOT a constant — never quote one from memory.** Senpi's builder fee falls with the
  user's points tier: …" → the first sentence stays in hard rule 0; the tier ladder sentence ("Senpi's
  builder fee falls with the user's points tier: **0.05% Bronze → … → 0.025% Legend**.") is word for word
  under "Quick actions", labelled "**Fee rate (hard rule 0):**", past the 5.7 cut (it is data, not a rule).
- 1.20.1: "**A Senpi strategy is one row with all its wallets.** One package is one strategy: its instances are
  its wallets, …" → the bold rule stays in One book; "One package is one strategy: its instances are its
  wallets, grouped by the package they were deployed under (`skill_name`); a wallet with no package stamp is
  its own row." (how the engine groups wallets) is word for word in `one-book.md` "Which wallets make one row".

## Imperatives that left core

(The heading is the one `.github/scripts/check_conservation.py` reads.) Sorted by the maintainer's rule —
every imperative's own words stay in SKILL.md:

- **Back in SKILL.md, word for word** (1.20.1) — 11 rules and 2 reason clauses; the reference copies stay and a
  test keeps both the same (`DUPLICATED`):
  - One book rule 2 (it was a shortened form): "`value_read: "partial"` → a strategy whose value covers
    only some of its wallets: say "(1 of 2 wallets)" from `wallets_loaded` / `wallet_count`."
  - Guardrail 3 (only its first half was in core): "**Account value ≠ P&L — never narrate the value curve as
    trades.** The engine sources closed trades from on-chain fills, so you should not need
    `strategy_get_pnl_and_account_value_history` for a trade review at all."
  - Guardrail 4, in place of the paraphrase "A missed mover is only interesting if it is in the strategy's
    mandate …": ""HYPE ran +18% and you weren't in it" is only interesting if HYPE is *in the strategy's
    mandate* and the miss reveals a gate that's too tight — otherwise it's just an asset the strategy was
    never designed to trade."
  - Guardrail 6b, in place of "Translate `closeReason`, never paste the enum (…)": "Translate
    `closeReason`, never paste the enum:".
  - "Run it in steps", in place of the Router's short form (which stays as the Router cell): "**Scope the
    ask.** When the user names a trade count — "improve my last 10", "review my last 20 trades" — **pass
    `--last N`**: it's a GLOBAL bound (the N most-recent closed trades across the whole book), so the review
    stays about what they asked instead of the entire 7-day book."
  - Quick action 4, which SKILL.md did not state (it only pointed to exit-records.md): "You need the
    **runtime id**, which is the key the event log is addressed by. Get it from the runtime registry
    (`installed_runtimes.json`, the same source the engine reads for mandates — each entry's `id`) or from
    `openclaw senpi runtime list`."
  - Guardrail 6b (it was in exit-records.md only): "**If the gateway is down**, `senpi dsl closes` exits
    non-zero and prints *"start it with: openclaw gateway run"*. **Say that.** An unreadable record is an
    unreadable record — do not fall back to guessing a mechanism from fill shapes and present it as fact."
  - Guardrail 1b, in place of "the gap between the floor and the fill is execution slippage, not
    calibration": "Keep **floor vs fill** separate: on AVAX the floor was +8.23% and it filled +7.67%, which
    is 0.55 ROE points of execution slippage, not calibration."
  - Guardrail 2, in place of "Route the fix to … to apply.": "A fix reads like *"Kodiak's SOL exit locked at
    tier 2 (+41% high-water) then trailed out; if you want it to ride further, that's the phase-2 tier
    ladder, not anything you did"* — and it routes to **`senpi-strategy-author` / `senpi-strategy-ops`** to
    apply."
  - Guardrail 6b, in place of "Never say it "detected …" — that invents a mechanism.": "Saying it "detected
    the position rolling over" or "cut early before the hard stop" invents a mechanism — a real review did
    exactly that on a position whose hold time (5.03h vs a 300m timer) was the giveaway."
  - Guardrail 1's lead sentence (see "Reworded").
  - Reason clauses (CLAUDE.md keep-the-reason rule): guardrail 9 "It's out of scope for a *trade review*, it
    second-guesses a strategy that was just set up, and nudging toward MARKET / higher-slippage fills
    **raises fees** — the biggest killer of returns." and "Use this skill FIRST" "Those return un-attributed
    dumps that invite exactly the failure modes below (skipping the current-price comparison, guessing the
    exit mechanism)." (in the replay).

### Moved to references (not rules) — maintainer OK requested as one group

None of these tells the agent what to do or never do beyond what SKILL.md says in its own words: each is
an incident, a note on what the engine already does, a field glossary, a rationale or an example. Each is quoted with its reference and the replayed Router row that opens it.

- Incident — `guardrails.md` "Hard rule 0", opened by router row "where am I leaking, fees, maker vs taker"
  (its Read-first cell names "`guardrails.md` hard rule 0" since 1.20.1): "A live review
  headlined **+$410.14** on a trade whose net was **+$310.23** ($99.92 of fees), and it never mentioned fees
  at all." Hard rule 0 itself is in core.
- Engine notes — `sources.md`, opened by the rows "shaken out, how my exits fire, why did it close, what is
  locked now" and "where am I leaking, fees, maker vs taker":
  - "**Telemetry is read ONLY from the current book.** The event-log ring lives on-disk with a running
    runtime, so the engine shells `openclaw senpi events` **only for ACTIVE/PAUSED strategies** — a *closed*
    strategy's ring is torn down with its runtime." (the bold rule is in core, and in the replay)
  - "Same fail-open contract as `all`: each step returns valid JSON with `meta.warnings` on partial data and
    never crashes on a missing/corrupt state file (it recomputes)."
  - "To keep the model-context payload small (the raw ledger on a big multi-strategy account is tens of
    thousands of tokens and blows the delivery timeout), the engine emits only the top handful of trades by
    biggest realized PnL + biggest hold-to-now delta — the ones a coach actually cites." (the outlier-sample
    rule is in core)
  - "(The telemetry step still event-fetches every *current* strategy regardless of `--last` — those events
    also feed `missed_signals`/leaks — but a **closed** strategy is never probed, which is what removes the
    heavy fan-out on a large, churned book.)"
  - "`all` (the default with no step) composes every slice into one dict — the same output the engine always
    produced."
- Engine notes — `one-book.md`, opened by the row "the wallet picked from `book.deep_dive.question`" (the
  Reference index sends a partial subtotal and the comparison there too):
  - "A subtotal over only some of its wallets says so ("1 of 2 wallets"); one whose wallets all failed is
    "unknown", never $0; with no saved wallets the line has no read-only clause." (what `book.total.line`
    already says — `_book_total` in `review.py` writes it; core says to quote that line verbatim)
  - "The engine computes it — of the closes measurable against today's price, the share that got out ahead
    of the later move (hindsight context, never a grade: guardrail 2) — only when 2+ wallets have
    `min_closes` (8) measurable closes, and it names any wallet whose trades it couldn't read or that hit the
    fill ceiling." (the comparison's gate; core keeps "quote `book.comparison.line`", "never compare wallets
    yourself" and hard rule 2)
  - "`order` holds only wallets with a value, an open position, trades to review or a value that couldn't
    load — an empty $0 wallet stays in the table, never in the question." (who the engine puts in the
    question; core asks it verbatim)
  - 1.20.1: "One package is one strategy: …" (see "Split across files" above).
- Glossary — `exit-records.md`, opened by the rows "shaken out, … why did it close …" and "walk me through
  my [asset] trade": "**`weak_peak_cut`** — never reached `min_value` ROE inside the window."
- 1.20.1 review round, to make room for the rules above (none carries a rule; each is quoted with its
  reference and the row that opens it):
  - `guardrails.md` "Guardrail 1b", rows "sell too early / late" (§1b): "Hold-to-now is hindsight —
    unknowable at exit time. **High-water is not:** it happened *during* the hold, and the floor that failed
    to capture it is a config choice. So give-back is fair game where "it kept going up" is not." (why
    give-back is diagnostic; the heading and "Report **peak ROE, exit ROE, and the share of peak kept**"
    stay)
  - `guardrails.md` "Guardrail 3", row "make more gains" (§2–3): "The engine deliberately emits **no**
    per-week or projection field." (engine note; "Never invent a forward number" stays)
  - `guardrails.md` "Guardrail 5", row "why is [strategy] losing" (§8, guardrail 5): "A market-neutral book
    "underperforming" a raging bull tape is doing its job." (example of "Judge each strategy against its
    OWN mandate", which stays)
  - `output-contract.md` "Composition — what the engine hands each skill", row "master my week, …": "The
    engine's `book_vs_market.top_movers` is the hook; market-pulse is the depth." and "The engine surfaces
    `smart_money_pct` per mover; smart-money does the trader-level comparison." (engine notes; which skill to
    compose stays)

### Rules stated twice at b1916b12 — SKILL.md keeps the other statement in its original words

These are rules, but the 1.19.1 file said each of them twice, and the other statement is still in SKILL.md
word for word (no paraphrase stands in for it). The second copy is in a reference.

- Guardrail 1 → `guardrails.md`: "Realized-only is half the ledger: it calls a book riding open winners a
  "loser" and penalizes hold-strategies." and "**Degraded-read honesty (same principle as `undetermined ≠
  all-clear`):** if `pnl_summary.unrealized_partial` is true (some current wallets read, some didn't),
  `total` is a **FLOOR** — headline it as *"at least $X (N of M wallets readable)"*, never as a complete
  total." Hard rule 1 says both: "Realized-only is half the ledger — it calls a book riding open winners a
  "loser" and penalizes hold-strategies." and "Say **"at least $X (N of M wallets readable)"**, never
  present it as the finished total."
- Guardrail 6 → `guardrails.md`: "**Name your source (onchain vs runtime).** Closed trades + every onchain
  fact come from **`discovery`** (`discovery_get_trader_history`) — **never `audit_*`** (deprecated). …" and
  "**Exit attribution is telemetry-first, ratchet-fallback.** … When it's `UNKNOWN` — no telemetry event and
  no ratchet record — say *"exit mechanism not recorded on this build"* (or, for a closed strategy, *"…its
  event ring is gone"*), **never guess** the mechanism. …" Core: "Sources — onchain vs runtime" (both
  bullets, "Say **"exit mechanism not recorded on this build"** …", "`meta.telemetry_source` … surface that
  honestly"), "Use this skill FIRST" ("**never use `audit_*`** for closed trades") and 6b's heading ("Never
  infer a mechanism").
- "Nothing to review?" → `guardrails.md`: "**The mistake to avoid:** telling someone whose funded strategy is
  silently blocked to "go find a strategy that fits the market." They don't need another strategy — they
  need to know why the one they already paid for is idle." Core: the `strategies_no_trades` row ("Diagnose
  **the strategy they already have**. **Never pitch another one.**").
- Guardrail 9 → `guardrails.md`: "**What TO say instead** …": "**Do not pitch another strategy to someone
  whose strategy hasn't traded.**" and "Either way, next moves are **options, not obligations**, and idle
  embedded cash is an **opportunity to deploy if they want**, never a "$0/day leak."" (with the two branch
  bullets). Core: the `meta.book_state` table, the `no_strategies` section ("Say the truth in one line, then
  pivot", "**Offer, never push.**", "Idle cash is an **option**, never a loss", "no "your idle cash is a
  $0/day leak" framing") and guardrail 9 ("Say that and stop", "Zero exposure on a new autonomous strategy
  is normal, not a leak").
- Guardrail 8 → `guardrails.md`: "Concretely: if you're about to say "you have 13 wallets, kill/merge 11 of
  them," stop — check `meta.current_strategy_count`. …" Core: "**Never** build a "you have N wallets,
  consolidate them" narrative out of closed/historical deployments. The live book =
  `meta.current_strategy_count`."
- "What the engine gives you" → `output-shape.md` "Reading order": "**Open with `book`** (the one list, then
  `book.total.line`); within the Senpi detail, **lead with `pnl_summary.total`** (realized+unrealized); the
  PROCESS counts are in `timing_summary`; per-strategy verdicts in `strategies[]` (CURRENT book only); the
  telemetry streams (`dsl_close_reason_mix` / `blocked_summary` / `leaks` / `execution_quality`) are real
  only when `telemetry_availability.streams_computed` is true; `trades[]` is a curated outlier sample (counts
  from the aggregates, never `len(trades)`)." A one-paragraph summary of One book rule 1, hard rule 1,
  guardrail 8, hard rule 3 / guardrail 6 and the outlier-sample paragraph, each still in core.

## Rules still outside SKILL.md — needs maintainer OK

None.

## Where each rule is on a 5.7 follow-up

At `b1916b12` 5.7 kept head 0–11,896 + tail 60,031–end of SKILL.md on every follow-up (the word "total"
near the end switched it to head+tail). The file is head-only now: 5.7 keeps its first ~15,870 chars
(`openclaw_measure.replay_57`). The Router, the follow-up rule, "Still true on every follow-up", One book,
Your wallets, HARD RULES 0–5, "these five are the floor" and "Use this skill FIRST" replay (One book and
Your wallets did not at `b1916b12`); 5.7 keeps 15,953 chars (1.20.2), and the smallest margin before the cut
is 189 chars (`test_every_replayed_rule_keeps_a_margin_before_the_57_cut` holds every replayed rule to ≥150).
`test_improve_trades_skill_layout.py` pins them and the mode. The base-replayed rules now past the cut (all
still in SKILL.md, word for word):

| Rule past the cut | On a follow-up |
|---|---|
| Sources: "Two sources, two jobs. Never mix them, and never reconstruct one from the other." and "**Telemetry is read ONLY from the current book.**" | in the replay (both copied word for word into "Still true on every follow-up") |
| Sources: discovery **owns** the onchain facts; telemetry "**never** becomes the trade list and **never** re-derives a price or PnL" | `references/sources.md` (a whole copy of "Sources — onchain vs runtime"), opened by router rows "shaken out, how my exits fire, why did it close, what is locked now" and "where am I leaking, fees, maker vs taker"; "Still true" replays "Discovery owns the trade list and its onchain facts; telemetry owns exit reasons and blocked signals" |
| "**A ratchet-sourced exit never names who closed the trade.**" and "Say **"not closed by its stop"**; never "you closed this", "closed by hand" or "manual"." | in the replay (copied word for word) |
| "And never print the raw status (`MANUALLY_CLOSED`, `CLOSED_EXTERNALLY`, `EXCHANGE_SL_HIT`): those are wire values, not words for a reader." | `references/sources.md`, same rows; "Still true" replays "Never the raw status." |
| Telemetry unavailable: "Say **"exit mechanism not recorded on this build"** … — **never** report it as a bug." | `references/sources.md`, same rows; "Still true" replays "No telemetry: "exit mechanism not recorded on this build", never a bug." |
| Output contract: "Process-framed, NEVER "premature / left on the table."" | in the replay (hard rule 2, in its own words: "NEVER say "you exited too early / N% premature / left $X on the table,""); whole in `references/output-contract.md`, opened by router row "master my week, analyze my strategies and trades, suggest improvements" |
| "Discuss individual reversals only after the aggregate, and only as evidence." | in the replay (copied word for word) |
| Closed strategies: "never as a live strategy to act on"; "do **not** give them a verdict, do **not** flag their missing mandate, and do **not** spin them into a "consolidate N wallets" recommendation" | in the replay as guardrail 8's own sentence "**Never** give a closed strategy a "doing its job / consolidate / kill / fix" verdict."; the output-contract sentences in `references/output-contract.md`, router row "master my week, …" |
| "`book.deep_dive.question` (when non-null) comes last of all — after the detail and the steps the request needs, never here." | in the replay (One book rule 6, in its own words: "as the last thing in the answer, after every step the request needs") |
| The fix-depth menus with their option descriptions | `references/output-contract.md` (both menus, word for word), opened by router row "the user picks a fix depth" (new); "Still true" replays the question and every option name |
| "Never pick for the user, and never act unprompted." | in the replay (copied word for word) |
| "quote the maker ratio as a fee-tier signal, never a fee dollar total" | `references/sources.md` (a copy of the **Fee $:** paragraph), opened by router row "where am I leaking, fees, maker vs taker"; "Still true" replays "The maker ratio is a fee-tier signal, never a fee dollar total." |
| Composition: "Don't re-implement these — call them and weave their output into the four-part contract below." | `references/output-contract.md` (a whole copy of Composition), opened by router row "master my week, …" |
| Output contract 3: "**`strategies[]` (the CURRENT book only) is the sole verdict surface.**" | `references/output-contract.md`, router row "master my week, …"; guardrail 8's closed-strategy sentence replays |
| Output contract 4: "**no guaranteed-gain language**" | in the replay as a short copy after hard rule 5 (1.20.2): "No fabricated forward numbers (no $/week). No guaranteed-gain language."; whole in `references/output-contract.md`, router row "master my week, …" |
| Sources: "`meta.telemetry_source` … and `meta.exit_reason_source_counts` tell you exactly how much enrichment landed; surface that honestly." | `references/sources.md`, router rows "shaken out, …" and "where am I leaking, fees, maker vs taker" |
| Composition: "Use this skill for retrospective / … questions; use `senpi-portfolio` for live state." | `references/output-contract.md`, router row "master my week, …" |
| "Use this skill FIRST" reason "(skipping the current-price comparison, guessing the exit mechanism)" | in the replay (back in core, 1.20.1) |
| Hard rule 0's incident ("A live review headlined **+$410.14** …") — not a rule | hard rule 0 replays; the incident is in `references/guardrails.md`, router row "where am I leaking, fees, maker vs taker" |

Rules that replayed in 1.20.0 but not at `b1916b12`, now past the cut again (moved down to make room, word
for word; both are first-turn cases):

| Rule | On a follow-up |
|---|---|
| Your wallets "**`external_no_trades`** — saved wallets, no Senpi strategy, nothing closed in the window: say it in one line and offer a longer `--window`. Never pitch a strategy." | past the cut ("Your wallets — unknown reads and fix depth"); the Router row "nothing to review, an empty result" replays and sends the agent to `meta.book_state` ("Nothing to review?", whose `external_no_trades` row says the same) |
| Your wallets "For a user with no Senpi strategy those Senpi aggregates are empty: open with `book.rows` as always (see "One book"), and the detail is `external_wallets[]`." | past the cut (same section); One book rule 1 ("Open with `book.rows` …") replays |

Rules past the cut that were not in the `b1916b12` replay either, copied short into the replay in 1.20.2
(Box A live test, 2026-10-22: a "Turn it into a routine" follow-up on a wallet with a few closes gave stop
prescriptions, and follow-ups wrote "a guaranteed +$237"). The full rules stay where they were, word for
word; `test_the_short_copies_sit_by_their_lines_and_match_the_full_rules` keeps the copies in step:

| Rule past the cut | On a follow-up |
|---|---|
| The sample gates: guardrail 7 "With **fewer than ~8 attributed closes on that strategy**, offer "explain only" and say the sample is too small to tune on, …" and "Same sample gate: with fewer than ~8 closes on that wallet, offer "explain only" and say the sample is too small to change a routine on." | in the replay, short copy at the end of the "Still true" Fix depth line: "With fewer than ~8 closes, offer "explain only" and say the sample is too small."; the saved-wallet gate is whole in `references/output-contract.md`, router row "the user picks a fix depth"; guardrail 7's is whole only in SKILL.md, past the cut |
| Guardrail 3: "no guaranteed-gain figure of any kind" | in the replay, same copy after hard rule 5 as output contract 4 above |

The copies are byte for byte (`test_sections_copied_into_a_reference_are_byte_identical`,
`test_the_fee_paragraph_copy_in_sources_is_byte_identical`), and a test checks that the replayed Router rows
name the file that holds them.

## Added (new text, not in 1.19.1)

- After review: the Router's "compare to the market" row also reads "/ the whales" and names
  `senpi-smart-money` (the old Quick-actions row "Compare me to the whales / the market"); the Shape line
  points to "Quick actions" for the maker ratio; guardrail 9 and "Use this skill FIRST" keep their reason
  clauses ("raises fees", "un-attributed dumps").
- The Router and the follow-up rule ("**On a follow-up, answer only what is new** — never re-send the `book`
  table, `book.total.line`, the TOTAL PnL headline or a figure already shown …"), per the placement rules for
  a SKILL.md over 16k. The Router's Run cells are the old Step(s) cells, verbatim. 1.20.1: the "master my
  week" row reads `output-contract.md` first, and a new row "the user picks a fix depth" points to it.
- One-line pointers to the new references, and the reference index at the end of the file.
- "Still true on every follow-up" after the Router. 1.20.1: it carries the rules above word for word where
  it can (Sources' first sentence, "Telemetry is read ONLY from the current book", the ratchet sentences,
  reversals, guardrail 8's closed-strategy sentence, "Never pick for the user, and never act unprompted."),
  with short restatements for the rest ("No telemetry: …, never a bug", "Never the raw status.", the maker
  ratio) and the fix-depth menus by their question and option names (a test keeps them equal to the menus).
  The full rules and menus stay where they were. 1.20.2: the Fix depth line ends with "With fewer than ~8
  closes, offer "explain only" and say the sample is too small." (the two sample gates, short).
- 1.20.2: hard rule 5 ends with "No guaranteed-gain language." (output contract 4 and guardrail 3, short).
  "The long tail of "Your wallets" above." now ends its line (whitespace only), so 5.7's cut falls after it.
- "## Your wallets — unknown reads and fix depth": the "Unknown is never empty" and "Open positions" bullets
  of Your wallets and "Fix depth on a saved wallet", moved below HARD RULES and "Use this skill FIRST" so
  those replay; the text is unchanged. 1.20.1: the `external_no_trades` bullet and the "For a user with no
  Senpi strategy …" sentence (first-turn cases) moved there as well, word for word.
- 1.20.1: `references/output-contract.md` — whole copies of Composition, the output contract and "Fix depth
  on a saved wallet"; `references/sources.md` — whole copies of "Sources — onchain vs runtime" and the
  **Fee $:** paragraph. A "**Fee rate (hard rule 0):**" label in Quick actions for the tier ladder. The Quick
  actions intro now reads "The **actionable lever** (the fix) per intent; its step(s) are in the Router, its
  data in `references/output-shape.md`." (pointers only). The fee row's Read-first cell names
  "`guardrails.md` hard rule 0".

# senpi-improve-trades 1.25.0 — build-first routing: dropped and reworded sentences

Strategy conversations now start in `senpi-strategy-author`: the closest template is the base the user
forks and makes their own (a lever shifted, a rule of their own); scratch when nothing fits. No skill
pitches a template by name outside that match. Each quote below is base text that left SKILL.md, with why.

>   on a saved wallet stay in this skill. When the user has NOTHING to review yet, `meta.book_state` routes it: nothing deployed -> read the market (senpi-market-pulse) then shortlist a fit (senpi-strategy-discover); deployed-but-idle -> diagnose THAT strategy, never pitch another. A hidden engine (scripts/review.py) reconstructs every

Why: Description rewritten build-first: it is the routing surface every turn. The new description is in the frontmatter.

> | nothing to review, an empty result | read `meta.book_state` first | `no_strategies` → `senpi-market-pulse`, then `senpi-strategy-discover`; no critique of an empty account ("Nothing to review?" below) |

Why: Routing reworked fork-first: strategy asks start in `senpi-strategy-author`, which offers the closest template as the base the user forks and makes their own.

> ### `no_strategies` — pivot to market-pulse → strategy-discover

Why: Routing reworked fork-first: strategy asks start in `senpi-strategy-author`, which offers the closest template as the base the user forks and makes their own.

> 2. **Then shortlist against THAT read** — compose **`senpi-strategy-discover`**, and let today's market
>    drive the shortlist. *"Semis and crypto-proxy equities are leading today while crypto is soft — here

Why: Routing reworked fork-first: strategy asks start in `senpi-strategy-author`, which offers the closest template as the base the user forks and makes their own.

> 3. **Offer, never push.** Two or three named fits with risk level and minimum budget; let them choose. If

Why: Reworded in place for build-first routing; the new wording is in SKILL.md.

> | *"How could I make more gains?"* | Strategy tune (DSL / entry gate), never a $/week promise. If the book has no Hyperfeed exposure at all — and only when `meta.strategy_count` > 0 (never for a saved-only user: coach their own process, no strategy pitch) — name it as the gap: **Penguin** (crypto) or **Pelican** (all assets) react only to the strongest live rotations on the feed, one position at up to 10x, 90% margin, with a DSL floor that ratchets up to lock gains — high risk, high reward, with -15% SL. That SL is **15% ROE**, a 1.5% price move at 10x, ~13.5% of that wallet **per stop-out**, and stops compound with the guard rails off. Say **rotations**, never "pumps" |

Why: A template pitched by name, unprompted. No skill names a template the user did not ask about; the build route (`senpi-strategy-author`) replaces it.

