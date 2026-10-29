# senpi-portfolio 1.36.0 — dropped, reworded and moved sentences

Restructure of `senpi-portfolio/SKILL.md` for the 10.4 size limit: base `b1916b12` 90,344 est chars,
1.35.0 55,735, 1.35.1 55,469, 1.35.2 55,499. No sentence was deleted: every old sentence is in SKILL.md or a
reference, or quoted here (`.github/scripts/check_conservation.py` passes against `b1916b12`).

1.35.1 applies the maintainer's rule that every imperative's own words stay in SKILL.md. Of the
sentences 1.35.0 had moved out, the rules came back to SKILL.md word for word ("Rules brought back to
SKILL.md", below). The room came from explanations, incidents, engine notes and examples, which moved
to the reference the router already opens for that topic, each one quoted below. A review of the first
1.35.1 commit (16907c2f) then asked for every command-verb sentence of the base, trigger word or not:
those rules came back too, and the room came from more non-rules (the analyst's persona, the steps code
block, engine notes, Skill Attribution). What is still outside SKILL.md is sorted in "Imperatives that
left core":

1. **Moved to references (not rules)**: a field glossary, an incident, an engine note, an example or a
   long-tail table cell. Maintainer OK requested for these as one group.
2. **Duplicates**: the base stated the rule twice. SKILL.md keeps the other base sentence word for word,
   quoted beside each entry.
3. **Rules still outside SKILL.md**: none.

The 5.7 follow-up table (last section) says where each base-replayed rule is on a follow-up turn.

1.35.2 (Box A live test, 2026-10-22) only widens the liveness router row with "it looks stuck / restart
it": "restart it, it looks stuck" had gone to a raw `strategy_list` read. No sentence moved.

1.35.3 (#919) rewords the `runtime_reason` bullet in place: the reason is a crash-loop cause or the
scanner's last tick error. SKILL.md is at 55,529 est chars. No sentence moved.

1.36.0 corrects one premise ("Reworded in 1.36.0 — the sub-Tier-1 premise corrected", below): a
sub-Tier-1 position may have a ratchet record at tier -1 with a null floor, not only no record. It also
reverses one rule ("Reworded in 1.36.0 — a named-string preset", below): a named-string preset is no
longer "still protected". SKILL.md is at 55,528 est chars. No rule moved out of SKILL.md.

## Re-pointed cross-references

The rule is unchanged; only "above"/"below" now names the file or section that holds the target.

| Old sentence | Now | Why |
|---|---|---|
| a strategy row's `protected` + `runtime_health` (the runtime exit — read them by the DSL rules above); a read-only row's `protection` counts (live stops | "…read them by the DSL rules below…" | The one wallet list now sits near the top of SKILL.md, before the DSL section. |
| **No runtime registered at all** → the "ACTIVE ≠ running" section below, not this one. | → `runtime-health.md` ("ACTIVE ≠ running"), not this one. | Both sections moved to separate references. |
| `edit_position`, or redeploy via `senpi-strategy-ops`, and never call it "redundant." See **Copy-trade / mirror strategies** below. | "…See `mirror-strategies.md`." | The mirror section is its own reference now. |
| This is DISTINCT from the flat-but-running case above: a flat sleeve with a *registered, ticking* runtime is waiting for a signal | "…the flat-but-running case (SKILL.md, "A flat/empty instance"):…" | That case stayed in SKILL.md; the ACTIVE ≠ running text moved to `runtime-health.md`. |
| Say it is running but has not found a trade, and use "No signals at all" above. | "…use "No signals at all" in `why-not-trading.md`." (SKILL.md and `runtime-health.md`) | That section moved to `why-not-trading.md`. |
| **`mirror`** — a **copy-trade** strategy: no runtime BY DESIGN (see **Copy-trade / mirror strategies** below). | "…(see `mirror-strategies.md`)." | As above. |
| A raw position is the reverse case — the bullet above. | "…the raw-position bullet in SKILL.md." | The resting-orders bullet moved to `dsl.md`; the raw-position bullet stayed in SKILL.md. |
| For anything deeper, hand off to `senpi-strategy-ops` `status.py <id>` as below — this skill interprets, it does not re-derive. | "…`status.py <id>` (step 1 of the ladder in `runtime-health.md`) — …" | In `why-not-trading.md` now; the ladder is in `runtime-health.md`. |
| See "DSL — how it works per strategy" above. | "See `dsl.md`." | In `output-fields.md` now; the ladder detail is in `dsl.md`. |
| Config-level posture, not a live per-position check — see the tri-state rule above. | "…see the tri-state rule (SKILL.md, Golden rules)." | In `output-fields.md` now; the rule stayed in SKILL.md. |
| not a hand-picked position the scanner will just re-open. See the HARD rule under "A strategy is ALL its wallets" and "Recommend at the STRATEGY level." | "…See the HARD rule (SKILL.md, "The job, and the hard rules") and…" | In `output-contract.md` now; the no-no is in SKILL.md's replayed head. |
| returns its verdict **beside a position count**, which is the corroboration below. | SKILL.md: "…which is the corroboration above." | In SKILL.md's Liveness digest the corroboration lines come first; `runtime-health.md` keeps "below". |
| "Judge against the mandate" below. | `mandate.md`'s copy of the strategy-first rule: "See "Judge each strategy against its OWN mandate" in SKILL.md." SKILL.md drops the pointer. | The sentence before it moved to `mandate.md` (below). |
| 4. **The two CTAs** (next section). | `output-contract.md`: "(the last section)". | The Resilience copy now sits between them. |

The old "For a NARROW ask, run only the minimal step" table is folded into the router at the top of
SKILL.md; its four rows are kept word for word in the router's first column.

Reworded, not moved: "`status.py`'s `degraded` is our `recovering`, its `unhealthy` our `degraded`."
(1.35.0's own line) is now the base's "**Mind the vocabulary:**" sentences; "`unknown`: "runtime
liveness unverified — not confirmed running"" is the base's "Say … — never upgrade to … or downgrade
to "broken."" sentence; the Still-true CTA line is the base's "Confirm before any change; never trade
unprompted." and "Propose; never deploy without confirmation." Nothing of the base was dropped by these.

## Imperatives that left core

The conservation check reads this heading: every base sentence with never / always / must / do not /
don't / only / verbatim that is now only in a reference is quoted under it. Several entries are one
base line that carries a rule SKILL.md keeps plus text that moved; the note says which part stayed.

### Moved to references (not rules) — maintainer OK requested as one group

**`references/dsl.md`** — router row "are my strategies protected? / do they have a stop-loss? / … /
what's their DSL" (protection):

- Read it as "arms at +8%, locks 40% of the peak by +18%, 60% by +35%, 78% by +60%, 88% by +100%." `lock_hw_pct: 0` at Tier 1 = priming only (arms the trail, no lock yet). *(Field glossary.)*
- `ratchet_stop_list` shows **only** the live ratchet for positions that have already crossed Tier 1 — it does **not** carry the strategy's config DSL exit, so a by-hand read makes every sub-Tier-1 position look "unprotected" when it isn't. The engine reads BOTH the config ladder (`profile.dsl`) and the live tier (`positions[].dsl`) and frames every position correctly; run it. *(Why; the rule "do NOT hand-roll them" and "Never assemble a protection verdict…" stay in SKILL.md. Moved 1.35.1.)*
- **Deployed strategies are already risk-managed — don't prescribe a stop-loss they already have.** Every strategy deployed from a Senpi template runs a built-in DSL exit (trailing stop) + risk guard-rails, enforced every tick. *(The fact behind the rule; the bold rule and "Never tell a user to "add a 10–15% SL…"" stay in SKILL.md. Moved 1.35.1.)*
- This is the failure this section exists to prevent. An **empty ratchet record on a sub-Tier-1 position is NOT "unprotected"** — `ratchet_stop_list` only returns a record **once a position crosses Tier 1**, so a position at, say, +6% ROE correctly has **no ratchet record yet**. It is protected **two ways**: the phase1 hard stop (active from entry) and the phase2 ratchet that will arm at Tier 1. *(Why; "An empty ratchet record on a sub-Tier-1 position is NOT "unprotected"", "Never read "no ratchet record" as "no DSL."" and the "Say …" line stay in SKILL.md. Moved 1.35.1.)*
- **An ERRORED or empty DSL query is "unknown," never "unprotected."** `ratchet_stop_list` can fail (e.g. `SERR031` auth, or the DSL engine lagging behind a just-opened position) or come back empty. The engine fails open here (config framing stands alone, plus a `meta.warnings` note); a by-hand call has no such fallback, which is why hand-rolling produces false "unprotected" verdicts. *(Examples and why; the bold rule, "treat it exactly like `dsl:null`" and "Never turn a failed read into a risk finding." stay in SKILL.md. Moved 1.35.1.)*

**`references/mandate.md`** — router row "… / how are my strategies doing / … / mandate" (verdicts):

- **Catalog enrichment (templates only, may be absent):** `belief_plain`, `thesis`, `archetype`, `sub_style`, `asset_classes`, `risk_level`, `time_horizon`, `tagline` — extra facets the engine adds for a strategy deployed from one of our packages (keyed by `skill_name`). *(Field glossary; "use catalog facets like `belief_plain`/`archetype` when present" stays in SKILL.md.)*
- `profile.source` — `"registry"` (authored/custom, description only), `"registry+catalog"` (one of ours, description + facets), or `"catalog"` (facets only, registry unreadable). *(Field glossary.)*
- Moved 1.35.1, no imperative word: "A strategy is doing its job when its behavior matches its *design*, even if that design means small/flat/idle right now." (SKILL.md keeps the base's other wording: ""Is it working?" means "**is it behaving the way its design says it should**""); "A market-neutral book is *supposed* to be short the names the crowd is long; a hedge is *supposed* to lean against the prevailing move; a contrarian book is *supposed* to fade the consensus." (examples); "A strategy can be "behind the current 4h rotation" and still be doing exactly its multi-week job." (explanation); "This is the SAME read whether the strategy is one of ours or user-authored — every deployed strategy has a `runtime.yaml`." (explanation).

**`references/mirror-strategies.md`** — router row "a copy-trade (`strategy_kind: "mirror"`)":

- **Judging one, and the ONLY correct remedies.** A mirror's risk = the copied trader's risk × `mirror_multiplier`. *(A heading and a formula; the remedies themselves, "the fix is **never** a DSL or a per-position stop" and the `senpi-trade` routes, are back in SKILL.md.)*

**`references/output-fields.md`** — router row "what a JSON field means"; "How to run the engine" sends every field question here:

- Each view's `accountValue` = that shared idle + only *that* DEX's position equity, so `wallet_value = main.av + xyz.av − shared_idle`. *(Formula.)*
- Same fail-open contract as `all`: each step returns valid JSON with `meta.warnings` on partial data and **never crashes on a missing/corrupt state file, nor on a runtime CLI that is absent or cannot even be spawned** (it self-heals by recomputing its prerequisites — every step also works STANDALONE, just slower; an unreadable runtime costs you the runtime fields, which come back `null` + a warning, not the read). *(What the engine does.)*
- Every step and `all` print the same `book` (the one wallet list and the book total), so the list never depends on which step answered. *(What the engine does. Moved 1.35.1.)*
- `totals`: `total_usd`, `managed_usd` (= `grand_total_usd`), `managed_breakdown` (the three buckets + `reconciles`), `managed_complete`, `read_only_usd`, `read_only_wallets` (`total` / `loaded`), `read_only_note`, `excludes` (+ `strategies_unreadable`), `excludes_note`, `line` (the rendered total — quote it). *(Field glossary.)*
- `deep_dive`: `offer`, `question`, `order` (labels, largest first — only wallets with something to go deeper on). *(Field glossary.)*
- `external_wallets` — the saved wallets' raw reads: `status` (`ok` / `unavailable`) and `wallets[]`, each `state` verbatim from `account_get_external_wallets` (see "One wallet list — every wallet first-class"). *(Field glossary; "Each wallet's `state` is `account_get_external_wallets`' object verbatim." stays in SKILL.md.)*
- `protected` (`true` / `false` / `null`) — `true` **only if ALL instances are protected**; `null` if ANY instance is `null` — an unread instance is never laundered into a `false`. *(How the engine computes the group field.)*
- Optional **catalog enrichment** (templates only, keyed by `skill_name`; `null` for authored strategies): `belief_plain`, `thesis`, `archetype`, `sub_style`, `asset_classes`, `risk_level`, `time_horizon`, `tagline`. *(Field glossary.)*
- If the runtime registry is unreadable, mandates fall back to the catalog (templates only); if that's also gone, `profile` is `null` and you judge on behavior. *(What the engine does; SKILL.md keeps "If `profile` is `null` … judge conservatively on behavior".)*
- Moved 1.35.1, engine notes; each base line also carries a rule head that stays in SKILL.md:
  - "- **Run the engine; never hand-pull balances.** `python3 scripts/portfolio.py` enumerates the embedded wallet + every strategy sub-wallet, pulls live clearinghouse state per wallet, and classifies the buckets. Read its JSON." (SKILL.md keeps the bold rule and "Read its JSON.")
  - "- **Real-time, always.** The engine forces a fresh fetch (no 12h cache) and reads each strategy's live clearinghouse state." (SKILL.md keeps the bold head and "Never report balances from earlier in the conversation — re-run.")
  - "- **Report realized PnL + closed trades, not only open ones.** Each strategy carries a `closed` block — `realized_pnl` (total booked PnL over the recent history pull), the record over that pull (`trade_count`, `winners`, `losers`, `win_rate_pct`, `longs`, `shorts`, `unknown_side`) and `recent[]` (last few closed trades: asset, direction, realized pnl, `closed_at_utc`)." (SKILL.md keeps the bold rule, "Quote the record; never work a win rate…" and the rest of the bullet.)
  - "- **"Current / my strategies" = ACTIVE only — never CLOSED.** The engine filters `strategy_list(status=["ACTIVE"])`, starts each analysis turn from a clean state, and expires the shared cache after a short window — so a strategy CLOSED since a prior run can't linger as a ghost." (SKILL.md keeps the bold rule and the `status: ["ACTIVE"]` instructions.)
  - "- **A strategy with a null name is still a real strategy.** `strategyName` is optional on `strategy_create_custom_strategy` and absent entirely from `strategy_create`, so it comes back null for many strategies — identify and analyze them by `strategyId` + wallet, never skip, mislabel ("Unnamed"), or double-count them for lacking a display name." (SKILL.md keeps the bold rule and "identify and analyze them … for lacking a display name.")
  - "- **Only `name_source: "strategyName"` means `name` is really its name.** Anything else is a stand-in the engine substituted: `"tradingStrategyName"` is the **package id** (identical on every sleeve of a package — all three cub sleeves read `cub`), `"name"` is a defensive flat-payload alias, and `null` means it has no name at all." (SKILL.md keeps the bold rule and the "When `name_source != "strategyName"`…" rule.)
  - No imperative word: "A strategy row's wallets are grouped by the same key in every step — the package they were deployed under (`skill_name`; a wallet with none is its own row, `strategy_group` names the key) — so the `money` step and the `strategies` step list the same rows." (SKILL.md keeps the base's first statement: "One package is one strategy: its instances are its wallets, grouped by the package they were deployed under (`skill_name`)…"); "`book.totals.managed_usd` is `grand_total_usd` (the same number; `reconciles` and the three buckets are about it alone)."; "`all` is the default when no step is given — it composes every slice into one dict (the same output the engine always produced)."

**`references/rationale.md`** — Reference index line "a user disputes a hard rule" (the incidents and explanations behind the rules; the rules stay in SKILL.md):

- "Close ox's \$600 wallet, keep the \$1,400 one" — **gutting one sleeve of a risk-parity core destroys the design.** The two sleeves are balanced *against each other*; keeping one is a different, unbalanced strategy the user never chose.
- "Keep cougar's short sleeve, repurpose its flat long sleeve" — **closing one sleeve of a long/short strategy leaves a NAKED directional position.** A market-neutral book with only its short leg is just a short — the exact opposite of neutral.
- **It is NOT idle capital to redeploy elsewhere, and never "dead money."** That capital is *committed to the strategy* — it's the dry powder the other half of the design needs to do its job. *(Why; the bold rule stays in SKILL.md. Moved 1.35.1.)*
- Do not abandon a correct reading, and do not invent a story to justify agreeing (a "strategy-grinder cascade," a "close at 14:58," "funds returned to embedded"). *(The incident's details; "Do not abandon a correct reading, and do not invent a story to justify agreeing." stays in SKILL.md. Moved 1.35.1.)*
- Moved 1.35.1, no imperative word: "— a position in a scanner-managed wallet can be reconciled as foreign and DSL-flattened within minutes (order "succeeds," position gone; a raw read of the wrong sub-wallet then shows it "phantom")." (the tail of the "A successful open/close order is NOT proof" rule, which stays); "— a bare number read by eye is how an older close gets called today's." (the tail of the `closed_at_utc` rule, which stays); "Those return un-bucketed dumps that mislead — idle-vs-deployed conflation, per-wallet collateral double-counting, and **sub-wallets mistaken for separate strategies** (a strategy's `main`/`hedge` legs are ONE strategy, not two). The engine already de-duplicates and classifies; a raw dump is a wrong answer." (why "Use this skill FIRST"); "Grading every strategy against a generic momentum benchmark is the failure mode this skill exists to prevent — it graded an all-weather core, a crisis hedge, and a waiting strategy each as "dead weight" when all three were doing exactly their job."; "— it opens and closes positions every tick per its DSL and signal logic." (the tail of "For an **autonomous strategy the scanner owns entries and exits**", which stays); "Recommending action on a strategy that turns out to be already closed is exactly the failure this guards against."; the heading's "(this is a real failure that broke live strategies)".

**`references/runtime-health.md`** — router rows "is it running / health check / degraded / not running / could not verify / it looks stuck / restart it", "how are my strategies doing" and the full read; the Liveness digest in SKILL.md names it:

- Such a strategy is **not running at all** — its scanner has never ticked, so it has **no DSL and no guardrails** — even though it shows ACTIVE and holds capital. *(Explanation; "Report it as **⛔ NOT RUNNING / UNPROTECTED — funded but no runtime; no scanner, no DSL, no guardrails**" stays in SKILL.md.)*
- No closed record is never flagged: "never closed" cannot be told apart from "deployed ten minutes ago". *(What the engine does.)*
- It is the runtime's own words, read off `senpi status --json`'s scanner rows (`degradedReason`) — on today's runtime only a crash loop, e.g. `jackal_main_signals: 5 crashes in a row, each within 10s of starting` (`senpi status` prints it as `crash loop: …`). *(Where `runtime_reason` comes from; "**Before the ladder: if the row carries `runtime_reason`, say it.**", "Quote it in those words…" and "never invent one" are back in SKILL.md.)*
- **`unknown`** — registered, but health **not yet proven**: a scanner it has never heard from, a runtime just restarted, or a `senpi status` document that carried no health verdict this engine recognises. *(Field glossary; the `unknown` "Say …" rule is back in SKILL.md.)*
- **Minimum runtime — `openclaw senpi runtime list --json`.** The engine asks the runtime for its own inventory through that command (it never reads the runtime's private state files). *(What the engine does.)*

**`references/why-not-trading.md`** — router row "why hasn't it traded / why didn't it open X / no signals"; the Liveness digest in SKILL.md names it:

- A running, healthy strategy that has not traded is the single most common "is it broken?" question, and the answer is almost never "it's broken". *(Framing.)*
- only if it persists *(the "do they act?" cell for `withdrawable_unavailable`; SKILL.md keeps "**Do not offer "close and relaunch" for `withdrawable_unavailable`.**")*
- The strategy has no way to size a position, so it can never open one. *(The `no_margin_configured` table cell.)*
- **Do not offer "close and relaunch" for `withdrawable_unavailable`.** It reads like the obvious fix and it is not one: relaunching rebuilds the same percent-sized recipe against the same balance read, and a fresh wallet does not mend a read that is wedged. *(Why; the bold rule stays in SKILL.md.)*
- The table above only covers signals that were **evaluated**. *(Explanation.)*
- Two commands, and they answer different questions — `senpi runtime list` prints only id, source and status, so it will show you `running` and leave you stuck. *(Which command shows what.)*
- **Never call it healthy on the strength of `health=healthy`.** For this failure that field is exactly the thing that is wrong — which is why users find it by noticing the quiet, not from anything we show them. *(Why; the bold rule stays in SKILL.md.)*
- It is the one outcome that never reaches the per-signal path, so it carries **no `reason_code`** and will not appear in the table. *(Explanation of `clearinghouse_unknown`.)*

**`references/analysis-framework.md`** — router row "analyze my positions / … / idle drag"; Reference index "a positions read: money map, …" (moved 1.35.1):

- The engine computes these as two separate fields precisely so you don't mix them. *(Why; the trap's rules — "**It is NOT idle cash in the embedded wallet.**", "**always say *where***" — stay in SKILL.md's replayed head.)*
- No imperative word: "If a user moved all their funds into strategies, the embedded wallet is **$0** even when `total_withdrawable` is large." (example); "They are not the same money and not the same thing." (restatement); "— a 1% price move at 10x is a 10% return on margin." (worked number; "Use leveraged return, not raw price %" stays); "A strategy with `total_funded: 3000` and `account_value: 0` has **$0 now**; one with `account_value: 3000` has **$3K now** regardless of what it was funded." (worked example; "Live capital = clearinghouse `account_value`, NEVER `total_funded`…" stays).

Also moved 1.35.1 with no imperative word, already in `runtime-health.md` in the base's words: "(a funded strategy sat exactly like this while the user believed it was protected and running)"; ""About $7 on 41 trades" is a guess, and it once turned a −$3 wallet into a +$1.69 one in the user's ear."; "— the user then manages a position that does not exist." (the tail of "Never narrate a shadow run…", which stays).

**Moved in the review pass of 16907c2f** (to make room for the rules below; none is a rule):

- `output-fields.md` ("Steps — the fail-open contract"), router row "what a JSON field means" and SKILL.md's pointer at the end of "Run it in steps": "**Narrate each slice as it returns — never wait for all steps.** The steps share a state file (`<tempdir>/senpi-portfolio/state.json`, overridable with `--state`), so a later step reuses what an earlier one fetched instead of re-pulling." *(Engine note; the bold rule stays in SKILL.md.)* Also "`--no-market` applies to every step (skips the `positions` market fan-out). Each step fails open like `all` (valid JSON, `meta.warnings` on partial data) and also works standalone" (1.35.0's line; the base sentence was already in `output-fields.md`).
- `output-fields.md`, same row; SKILL.md "row details" points there: "**Saved wallets.** Their raw reads are `external_wallets: {status, wallets}` (each `state` verbatim), returned from every step (`money`, `strategies`, `positions`) and from `all`, with `meta.no_strategy_path` on each; their rows in `book.wallets` carry the columns." *(Where the field comes from; "Each wallet's `state` is `account_get_external_wallets`' object verbatim." stays in SKILL.md.)*
- `output-contract.md` ("Narrate as the steps return"), router row "analyze my portfolio / how am I doing": the steps code block, `python3 scripts/portfolio.py money        # 1. the FAST money map: …` through `… all          # one-shot fallback: the full composed dict (same output as before)`. *(A command list with comments; SKILL.md keeps the step order, `money` → `strategies` → `positions`, and the router names each step.)*
- `output-contract.md` ("The analyst's bar (moved from SKILL.md)"), same row: "You are a sharp portfolio analyst. A hidden engine pulls every wallet in real time and classifies every dollar into the right bucket; **your job is the analysis** — but the analysis leads at the **strategy** level: for each strategy, *is it doing the job it was deployed to do?* Positions are evidence for that verdict, not the headline. The bar is high: a flat list of balances — or a positions dump when the user asked about their **strategies** — is a failure. The user wants a read." *(Persona. Its rule is stated in SKILL.md's replayed head: "do **not** answer with a positions dump" and "Lead per-strategy".)*
- `rationale.md` ("Skill Attribution (moved from SKILL.md)"), Reference index line "a user disputes a hard rule": "Guide/analysis skill — it *reads* the account and *recommends*; it does not place a trade or move funds. Attribution happens downstream when the execution tools / strategy skills act on a CTA." *(What the skill is; "**This skill reads. It never changes protection.**" stays in the replayed head.)*
- `mirror-strategies.md`, router row "a copy-trade (`strategy_kind: "mirror"`)": "Those are **custom-strategy** remedies — applied to a mirror they break the copy-trade the user deliberately set up." *(Why; the "**NEVER, for a mirror:**" list stays in SKILL.md.)*

### Command-verb sentences without a trigger word

The review of 16907c2f scanned the base for sentences that open on a command verb (Say, Report, Give,
Count, Judge, Use, Read, …) and are no longer in SKILL.md; the check above only sees never / always /
must / do not / don't / only / verbatim. Every hit, sorted:

- **Brought back word for word** (group 3; listed in "Rules brought back to SKILL.md", second pass): "Count the signals rather than describing one, …", "And lead with whether they need to act: …", "Give the exchange's own message, which rides the outcome.", "Wait, then support.", "Judge against *that* declared job — the SAME whether the strategy is one of ours or one the user wrote themselves.", "**Re-run the deploy** — …", "**They agree.** Clean. Say so.", "Say so in those words, quote the warning, …", "State the group's `protected` posture … then **each open position's live tier** from `positions[].dsl`.", "Compare the net tilt to where the broader market is." and the verdict order's bold heads.
- **SKILL.md states it in its other base words** (group 2):
  - `runtime-health.md`: "Narrate it honestly — a registered runtime is not automatically a healthy one:" → "This is the honest bar: **only `live` means "confirmed working.""**" and "A runtime *process* that exists (`status: running`) is not a health verdict and never reaches `live` on its own."
  - `runtime-health.md`: "**They agree.** It really is broken. Report it and work the ladder below." → "For `degraded` corroborated by no activity, or `not_running`, work the ladder yourself and report what you found".
  - `mandate.md`: "Use them when present;" → Golden rules: "use catalog facets like `belief_plain`/`archetype` when present".
  - `analysis-framework.md`: "Cite `return_on_equity_pct` (uPnL / margin), the number that actually reflects the position — a 1% price move at 10x is a 10% return on margin." → the same words up to the dash in Golden rules; the worked number is listed above.
  - `dsl.md`: "Read the strategy's `profile.dsl` (also on each `strategy_groups[]` entry as `dsl` — surface it once per strategy)." and `output-fields.md` "Surface it once per strategy;" → "answer in **two parts, per strategy**" and "**(1) How its DSL works — the tier ladder** (`profile.dsl`)".
  - `dsl.md`: "Report it that way — `note` already phrases it …" and "Use `arm_at_roe_pct` + the position's `roe`: "protected, ratchet arms at +8% — currently at +6%."" → "Say **"protected; profit-ratchet arms at Tier 1 (+X%)"**" and "(The engine already frames every `armed: false` position this way in `dsl.note`; do not override it with an "unprotected" reading.)"
  - `dsl.md`: "Report both: "cougar runs a DSL exit (hard stop −14%, ratchet from +8%); …"" → Golden rules: "State a `true` posture as ("deployed with a DSL exit"), then give the **ladder** (`profile.dsl`: hard stop + arm-at + tiers) and each **open position's live tier** (`positions[].dsl`)."
  - `dsl.md`: "Use the `dsl` objects, not the order book." → "never infer "no stop" from the absence of a resting stop order (DSL exits are runtime-managed, not resting orders)".
  - `output-contract.md`: "Don't wait for the other steps." → "**Narrate each slice as it returns — never wait for all steps.**"
  - `output-fields.md`: "Keep `all` as the one-shot fallback when a single blocking call is fine;" → "use `all` only when a single blocking call is fine."
  - `output-fields.md`: "Judge the whole strategy against this." → "**Lead the per-strategy read with `profile.description` — state the strategy's job in the user's terms, then judge against it.**"
  - `output-fields.md`: "Report the strategy's figures from here, not per-wallet." → "**Lead and reason at the `strategy_groups[]` level, not `strategies[]`.**"
  - `rationale.md`: "Quote a close with its UTC date and time from `closed_at_utc`, never from a raw epoch such as `closed_time` — a bare number read by eye is how an older close gets called today's." → the same words up to the dash in "Source of truth"; the tail is listed above.
- **Not a rule** (group 1): `output-contract.md` "Run as **ONE** call it can take minutes, blow the `exec` timeout, and push you to raw MCP — which loses every guardrail." (why the steps exist; a condition, not a command); `output-contract.md`'s bodies of verdict items 1–4 and 6 (where each figure comes from, and the examples "cub is a K-shaped …", "short ETH, +11% on margin, …"); `dsl.md` "Read it as "arms at +8%, …"" (listed above).

### Duplicates: SKILL.md keeps the base's other statement of the rule

The base said each of these twice. The copy in a reference is the second statement; SKILL.md keeps the
first, word for word, quoted after the arrow.

- `dsl.md`: **Config-level `protected` ≠ live per-position tier.** `strategy.protected` / `group.protected` (`true`/`false`/`null`) is the **config posture** — `true` only when the deployed `runtime.yaml`'s `exit:` block was actually READ by the engine; `null` means the read didn't happen, never assume `true` from `skill_name` alone. → Golden rules: "`true` only when the deployed `runtime.yaml`'s `exit:` block is one the **ENGINE actually read** (`dsl_preset` or `engine: dsl`) — a `skill_name` attribution stamp alone no longer suffices." and "`null` means the runtime gateway did not answer — say **"could not verify on this host"**".
- `dsl.md`: **For a runtime-managed position, never infer "no stop" from the absence of a resting stop order.** DSL exits are **runtime-managed**, not resting venue orders — you won't see them as open orders. → Golden rules: "never infer "no stop" from the absence of a resting stop order (DSL exits are runtime-managed, not resting orders)".
- `output-contract.md`: `portfolio.py money` → **narrate the one wallet list IMMEDIATELY** — `book.wallets` as one table in the engine's order, then `book.totals.line`: the book total, the managed subtotal (`grand_total_usd`) broken into the three buckets (idle-in-embedded / idle-in-strategies / deployed-in-positions), each labeled by *where*, plus `reconciles`, and the read-only subtotal beside it. Don't wait for the other steps. → "**Narrate each slice as it returns — never wait for all steps.**" and Output contract item 1.
- `output-contract.md`: "cub is a K-shaped long/short dispersion book — long the structural winners, short the laggards; the P&L is the spread." **If `is_multi_wallet`, name it as ONE strategy across its sleeves** ("cougar is a market-neutral long/short pair") — never as two strategies. → "**Present each group as one strategy**; never present its wallets/instances as separate strategies."
- `output-contract.md`: A group instance in `flat_instances` is the strategy's **OTHER sleeve waiting for its signal** (for a multi-wallet strategy) or the whole strategy waiting for its setup (for a single-wallet one) — say that, never "dead money." Flag any position fighting the tape *for a directional-momentum mandate* / near `liq_px` / oversized. → "**It is NOT idle capital to redeploy elsewhere, and never "dead money."**", "Say "flat, waiting for its setup," never "idle dead weight."", and the "Flag any position…" sentence, which is back in SKILL.md's Output contract.
- `output-contract.md`: … or, for a sub-Tier-1 position, "protected from entry, ratchet arms at +X% — currently +Y%." **Never say a live position has "no DSL" because it lacks a ratchet record** (sub-Tier-1 positions have none by design). → "**Never call a live position "unprotected" just because it has no ratchet record — sub-Tier-1 positions have none by design.**"
- `output-contract.md`: **Any lever is WHOLE-STRATEGY.** If you suggest close / pause / adjust-config / top-up, it applies to the **entire strategy (all its wallets)** — never one sleeve. → the no-no, "Close / keep / deploy / top-up is a **WHOLE-STRATEGY decision — all its wallets together.**", and CTA 1's "apply them to the **whole strategy (all its wallets)** — never to a single sleeve".
- `output-fields.md`: **These are two views of one wallet, not two separate pools.** The `withdrawable` (idle cash) is **shared** and reported *identically* in both views — so it is counted **once**, never summed. → the same two sentences, back in SKILL.md's Cross-DEX (1.35.1).
- `output-fields.md`: `totals` — the MANAGED money map: the three buckets + `grand_total_usd`, `unrealized_pnl`, and a `reconciles` flag (cross- checks the per-wallet sum against the portfolio aggregate; if `false`, say the numbers don't tie out and lead with the per-wallet figures). → Resilience: "STOP and re-run first (see above); if it persists, surface it and trust the per-wallet (live) figures."
- `output-fields.md`: **This is the unit of analysis and recommendation** — present and reason at this level, never at individual wallets. → "**Lead and reason at the `strategy_groups[]` level, not `strategies[]`.**" and "the *unit of analysis and recommendation* is the group."
- `output-fields.md`: For a multi-wallet strategy these are the OTHER sleeve(s) **waiting for a signal** — NOT redeployable idle, never "dead money." → "**It is NOT idle capital to redeploy elsewhere, and never "dead money."**"
- `output-fields.md`: **Trust `name` as a name only when `name_source` is `"strategyName"`.** → "**Only `name_source: "strategyName"` means `name` is really its name.**"
- `output-fields.md`: **This is the yardstick — judge the strategy against `profile.description`, not memory and not a momentum benchmark.** `profile` is `null` only when the strategy is in neither the runtime registry nor the catalog (`meta.profile_source` records `registry`/`catalog`/`mixed`/`null`). → "**Lead the per-strategy read with `profile.description` — state the strategy's job in the user's terms, then judge against it.**", "**Do not reconstruct the mandate from memory…**" and "do **not** default to a momentum yardstick."
- `output-fields.md`: `protected` (`true` / `false` / `null`) — `true` only when the deployed `runtime.yaml`'s `exit:` block was actually READ by the engine (`dsl_preset` or `engine: dsl`); a `skill_name` attribution stamp alone no longer counts. → Golden rules, as for `dsl.md` above.
- `output-fields.md`: `null` = the runtime read did not answer — say "could not verify on this host," never "protected" or "not protected." Config-level posture, not a live per-position check — see the tri-state rule above. → Golden rules: "say **"could not verify on this host"**, never "protected" and never "not protected.""
- `output-fields.md`: On a read failure `realized_pnl` and the counts are `null` and a `meta.warnings` entry is added — treat as "unavailable," never as zero. → Resilience: "Report realized PnL as **unavailable** for that strategy — never as `$0`."
- `output-fields.md`: Never present it as "no DSL." If the ratchet read failed entirely, every position still gets this config-based `armed: false` object (+ a `meta.warnings` note). → "**Never read "no ratchet record" as "no DSL."**"
- `runtime-health.md`: Its `runtime_registered` / `not_running` / `running_blind` / `protected` are **`null` (N/A), never `false`** — do NOT report it as "not running / unprotected," do NOT tell the user to add a DSL, set a stop via `edit_position`, or redeploy via `senpi-strategy-ops`, and never call it "redundant." → the mirror bullet ("none of the liveness verdicts apply to it") and "**NEVER, for a mirror:** add a DSL / ratchet; set a stop via `edit_position` on its positions; "redeploy it via `senpi-strategy-ops`"; call it "running unprotected / not running"; or call it "redundant with strategy X, close it.""
- `runtime-health.md`: **`live`** — registered, and the runtime reports itself healthy. Only this earns "running / protected." → "This is the honest bar: **only `live` means "confirmed working.""**
- `output-fields.md`: `book` — **every wallet in ONE list by value, and the one book total. PRESENT THE LIST FROM HERE.** `wallets[]` (sorted by the engine — keep its order): `label`, `kind` (`managed` / `read-only`), `origin` (`embedded` / `strategy` / `saved`), `value_usd` (null = couldn't load), `loaded`, `open_positions`, `unrealized_pnl_usd`; a strategy row adds `strategy_group` (its `strategy_groups[].label`), `wallet_count`, `strategy_wallets[]`, `wallets_couldnt_load`, `protected`, `runtime_health`, `realized_pnl_usd` (or `not_read_this_step` on the `money` step); a saved row adds `address`, `protection` counts, `excludes_coins`, `no_hyperliquid_activity`, `positions_scope`, `access`, `not_applicable`. → Output contract item 1: "1. **One wallet list + the book total.** `book.wallets` as one table in the engine's order — label, value, open positions, protection, PnL, kind — then `book.totals.line`" and "**Keep the engine's order and never re-section by origin**".
- `runtime-health.md`: **`unverified`** — … `meta.warnings` names the failed command; quote it, never invent a cause. → Golden rules: "`runtime_health: "unverified"` reads the same way; `meta.warnings` carries the command that failed — quote it rather than inventing a cause." (The rest of the `unverified` line is back in SKILL.md word for word.)
- `runtime-health.md`: **`mirror`** — … Never `live` / `recovering` / `degraded` / `not_running` / `unverified` — those don't apply to it. Judge it on `mirror_of` + `mirror_multiplier` + `stop_loss_pct` / `take_profit_pct`, never on a runtime it was never meant to have. → "none of the liveness verdicts apply to it" and "A mirror is an intentional copy of a trader, judged on the trader + the multiplier + its strategy-level SL/TP."
- `why-not-trading.md`: `risk_gate_CLOSED` — The gate carries its own `reason`, so name **which one**; never just "a risk gate". → `risk_pause`: "Name the gate and quote its `reason` verbatim".
- `why-not-trading.md`: no, until it resets — but a drawdown halt with `drawdown_reset_on_day_rollover: false` never resets on its own: that is **the user's decision**, never clear it for them → `risk_pause`: "a latched drawdown halt with `drawdown_reset_on_day_rollover: false` never lets go on its own — it waits for the user's decision, and you never clear it or resume entries without their explicit approval".

### Rules still outside SKILL.md — needs maintainer OK

None. (The minimum-runtime "whole-fleet pattern" sentence, filed as an exit-code edge case in the first
1.35.1 commit, is back in SKILL.md word for word.)

## Rules brought back to SKILL.md (1.35.1)

Restored over a 1.35.0 paraphrase (the paraphrase is gone):

- Cross-DEX: "**These are two views of one wallet, not two separate pools.**" and "The `withdrawable` (idle cash) is **shared** and reported *identically* in both views — so it is counted **once**, never summed."
- "**Mind the vocabulary:** `status.py` reports the engine's raw words, and they do not line up with this skill's. Its **`degraded`** is this skill's **`recovering`** (one errored tick); its **`unhealthy`** is this skill's **`degraded`** (the real fault)."
- `unknown`: "Say **"runtime liveness unverified — not confirmed running"** — never upgrade to "healthy/protected" or downgrade to "broken.""
- `degraded`, book live: "**They disagree.** The last scans errored; the book is live and managed. Say *"running — some scans errored"*, never *"your strategy is broken."* Confirm with `status.py <id>` before going further."
- Output contract: "**Per-strategy verdict (the real value).** For **each `strategy_groups[]` entry** (one per real strategy — never one per wallet), in this order:"
- "**Anti-patterns — these exact misreads happened live; never repeat them:**"

Brought back word for word (SKILL.md had no statement of them):

- "`running_blind: true` is a third, previously-invisible state: … — report **⚠ RUNNING — NO ENTRY SCANNERS**, not a clean "running.""
- `recovering`: "Do not put a ⚠ on the strategy, and do not send the user to a diagnostic."
- `degraded`: "This is the one that always deserved the warning. Say **"⚠ runtime degraded — running but not healthy,"** not a clean all-clear."
- "A runtime *process* that exists (`status: running`) is not a health verdict and never reaches `live` on its own."
- `live`, not trading: "**They disagree.** Do **not** give a clean all-clear — a scanner can read `healthy` and still be blind. Say it is running but has not found a trade, …"
- "**When the two disagree, say which one you are trusting and why.** If the money is moving, that is the headline and the field is the footnote — do not report the field and bury the evidence."
- "**Say "has not CLOSED a trade in Xh", never "has not traded"**: a position carries no open time, so an entry taken since that close is invisible here." and "Quiet while **holding** means fully allocated, not stuck — check slots and entry gates first, and note every guard rail halts ENTRIES only, so a gate counter is moot while every slot is full."
- "**`risk_pause`** (beside `runtime_health`, never instead of it) — the runtime's OWN entry gate says `CLOSED` or `COOLDOWN`: the strategy is **healthy and paused by its own rule** (…)." and "Name the gate and quote its `reason` verbatim (…), say when it lets go (…), and that nothing is broken."
- "A thorough health check does not stop at the verdict: for a strategy that is `not_running`, `degraded` or `unknown` — **never for `recovering`** — **`python3 senpi-strategy-ops/scripts/status.py <id>`** re-asks the runtime directly and returns its verdict **beside a position count**, …" and "Run it yourself and give the answer."
- "Reference the right tool to *confirm* — never re-derive its analysis here."
- "**Before the ladder: if the row carries `runtime_reason`, say it.**", "Quote it in those words: a scanner that cannot stay up is why it has not traded." and "No `runtime_reason` means the runtime gave none — never invent one; the ladder finds it."
- "**When it IS genuinely broken, act — don't hand the user a to-do**", "For `degraded` corroborated by no activity, or `not_running`, work the ladder yourself and report what you found." and "Only the last step touches their money, and only that one needs their consent:"
- "**Nothing qualified.** The entry bar has not been met — name it, never "being selective": the binding gate, its threshold and the universe count from its `runtime.yaml` (…)."
- Mirror: "Its `name` is **"copy of `mirror_of`"** — never call it "unnamed."", "High leverage or a lopsided book is worth *surfacing* (…), but the fix is **never** a DSL or a per-position stop." and the two `via senpi-trade` remedies.
- Output contract: "Flag any position fighting the tape *for a directional-momentum mandate* / near `liq_px` / oversized."

Brought back word for word in the second pass (review of 16907c2f):

- Liveness digest: "**Two rules before you answer.** Count the signals rather than describing one, and name the assets that were skipped, because "3 eligible entries were skipped" is the part the user actually feels. And lead with whether they need to act: for most of these the honest answer is *nothing*, and saying so is the useful reply."
- `unverified`: "Say **"could not verify on this host"** — never "protected," "not protected," "running," or "not running." **`null` is not `false`.**" (it was filed as a duplicate; the covering sentence did not name "running"/"not running" for `unverified`).
- Minimum runtime: "**That whole-fleet pattern means the runtime on this box is too old for this skill's registry read, not that the strategies are broken.** Say so in those words, quote the warning, and do not diagnose the strategies from it: no strategy may be called running, not-running, protected or unprotected off that run." and "The fix is a runtime upgrade on the box, not a redeploy of the strategies." (introduced by a short lead-in naming the pattern: every runtime field `null` on every strategy, with a `meta.warnings` line naming `openclaw senpi runtime list --json`).
- Corroboration: "**They agree.** Clean. Say so." (`live`, trading).
- The ladder's step 3: "**Re-run the deploy** — a deploy interrupted by a gateway restart does not resume on its own, and re-running reconciles rather than duplicating. This is the fix for `not_running` after a broken deploy."
- `withdrawable_unavailable`: "Wait, then support." `position_open_failed`: "Give the exchange's own message, which rides the outcome."
- Mandate: "Judge against *that* declared job — the SAME whether the strategy is one of ours or one the user wrote themselves."
- Output contract: the verdict order's six bold heads ("1. **Label + mandate.**" … "6. **Any lever is WHOLE-STRATEGY.**"), item 5's "State the group's `protected` posture (⟹ **all** instances ship a DSL exit), then **how its DSL works** from `group.dsl` / `profile.dsl` (hard stop at `hard_stop_roe_pct`, ratchet arms at `arm_at_roe_pct`, the tier ladder), then **each open position's live tier** from `positions[].dsl`", and item 3 whole: "3. **Portfolio-level read.** Net exposure (net long/short and by sector), concentration (largest position), idle drag (capital sitting in cash), and the overall posture — is this book hedged, directional, mostly in cash? Compare the net tilt to where the broader market is."

`test_portfolio_skill_layout.py` pins every restored rule as whole sentences (`RULES_BACK_IN_CORE`).

## Rules that no longer replay on 5.7 follow-ups

At `b1916b12` 5.7 kept head 0–11,829 + tail 86,378–end of SKILL.md on every follow-up. The file is now
head-only: 5.7 keeps its first ~15,930 chars (`openclaw_measure.replay_57`). The router, the follow-up
rule, the one-list rules, "Source of truth", "Use this skill FIRST", "This skill reads", the no-no and
(1.35.1) the wallet model's trap, Cross-DEX, the first two bucket rows and the CTA 2 number rules
replay; `test_portfolio_skill_layout.py` pins them, the mode, and a 150-char margin before the cut.
Where each base-replayed rule is on a follow-up turn now:

| Rule | On a 5.7 follow-up |
|---|---|
| The wallet model: "Only the Hyperliquid legs can fund strategies — EVM USDC does not bridge in"; the trap ("It is NOT idle cash in the embedded wallet", "always say *where*") | In the replay (moved up into the head in 1.35.1). "The engine computes these as two separate fields precisely so you don't mix them" is an explanation and moved to `analysis-framework.md`. |
| Cross-DEX: "Never add the two views' account values or withdrawables yourself"; "counted once, never summed" | In the replay (1.35.1). The `accountValue` formula is in `output-fields.md`. |
| The three buckets' last row ("**Deployed in positions** — Margin actively backing open trades.") and "**`grand_total = idle_in_embedded + idle_in_strategies + deployed_in_positions`.**" | `references/output-contract.md` ("The three buckets", a whole-subsection copy pinned to match SKILL.md), opened by router rows "analyze my portfolio / how am I doing" and "a yes to CTA 1 / CTA 2, or a digit …". They sit just past the cut in SKILL.md; the first two rows and "Every dollar is in exactly one of **three buckets**" replay. |
| "they are `read-only` rows in the one wallet list and the read-only subtotal beside `grand_total_usd`" | `references/output-contract.md` (the same copy), same router rows. The replay also has the rule as the base's other statement: "**Never Senpi money.** … it is a `read-only` row in the one list and part of `book.totals.read_only_usd` only." |
| "A strategy is ALL its wallets": "**This is the most important rule in this skill.**", "It is not. A multi-wallet strategy … is **ONE strategy across N wallets/instances**", "**Lead and reason at the `strategy_groups[]` level, not `strategies[]`.**", "**Present each group as one strategy**; never present its wallets/instances as separate strategies.", "… at least one strategy spans multiple wallets — be especially careful." | `references/mandate.md`, opened by router row "are my strategies protected? / … / how are my strategies doing / analyze my strategies / … / mandate", and `references/output-contract.md`, opened by router row "analyze my portfolio / how am I doing": whole-paragraph copies, pinned to match SKILL.md. The replay also has "**A Senpi strategy is one row with all its wallets.**" and the no-no. Not moved into the head: making room for its ~1,300 chars would push the wallet model (misstated balances) out instead. |
| The mandate source: "**The mandate comes from the strategy's own deployed `runtime.yaml`, so this works for a user's OWN authored strategy …**" and "Judge against *that* declared job — the SAME whether the strategy is one of ours or one the user wrote themselves." | `references/mandate.md` (its opening blockquote, the base words), router row "… / mandate". The second sentence is also back in SKILL.md (past the cut). |
| CTA 1: "never a saved wallet, which is read-only (quote its `access` line). Confirm before any change; never trade unprompted." | "Confirm before any change; never trade unprompted." is in the replay word for word ("Still true … Acting on a CTA"). The whole CTA block is in `references/output-contract.md`, opened by router row "a yes to CTA 1 / CTA 2, or a digit …" (pinned to match SKILL.md). The replay also has "CTA 1 applies to Senpi wallets only." |
| CTA 2: "never `book.totals.read_only_usd` or any read-only row, which Senpi cannot deploy"; "Propose; never deploy without confirmation." | "Propose; never deploy without confirmation." is in the replay word for word. The rest: `references/output-contract.md`, router row "a yes to CTA 1 / CTA 2, or a digit …". The replay also has "CTA 2 ("put the idle to work") never counts it." |
| CTA 2's menu and numbers: the Penguin … Athena lines, "**-15% SL is 15% ROE, not a 15% price move**", "**Up to 10x**, never a flat 10x", "Say **rotations**, never "pumps"" | The number rules are in the replay as a one-line summary ("Still true … CTA 2: -15% SL is 15% ROE; up to 10x, never a flat 10x; say rotations, never "pumps"."). The menu and the full sentences: `references/output-contract.md`, router row "a yes to CTA 1 / CTA 2, or a digit …, or a question about a menu option or its numbers". |
| Resilience: "don't report an empty portfolio as "$0."", "Report realized PnL as **unavailable** … never as `$0`", the `TOTALS DO NOT RECONCILE` re-run, "**Never** report `total_withdrawable` as embedded idle, never skip a wallet, never skip the CTAs" | `references/output-contract.md` ("Resilience", a copy pinned to match SKILL.md), opened by router row "a read failed: `meta.degraded`, `meta.warnings`, `reconciles: false`, a `null` figure". The replay also keeps the one-line summary ("Still true … A failed read is not $0 or flat"). |
| Install: "**Install the whole `scripts/` directory** — copying `portfolio.py` alone fails with `No module named 'mcp_client'`. Stdlib only, no other runtime dependencies." | Not on a follow-up: an install note for whoever installs the skill, not a rule for a chat turn. |
| The persona ("You are a sharp portfolio analyst…", "The bar is high: … is a failure. The user wants a read.") | `references/output-contract.md` ("The analyst's bar"), router row "analyze my portfolio / how am I doing". Its rule replays as "do **not** answer with a positions dump" ("Strategy-first"). |
| Explanations that were in the base head (the phantom-wallet clause, "a bare number read by eye…", "Those return un-bucketed dumps…", the `ratchet_stop_list` detail, "A strategy is doing its job when…", the trap's restatements, the no-no heading's "(this is a real failure…)") | Not rules: in `rationale.md`, `dsl.md`, `mandate.md` and `analysis-framework.md` (quoted under "Imperatives that left core"); each rule they explain replays. |

## Reworded in 1.36.0 — the sub-Tier-1 premise corrected

The base said `ratchet_stop_list` returns a record only once a position crosses Tier 1. That is false
for a strategy with `phase1.enabled: false`: it starts in phase 2, the backend holds its record from
open, and the record reads `currentTierIndex: -1`, `tierFloorPrice: null`, no SL order, while the
runtime's max-loss stop rests on the venue. Each sentence below was reworded in place to say "no record,
or a record at tier -1 — both sub-Tier-1"; the rule ("never call it unprotected / no DSL") is unchanged.
The raw-position bullet gained one sentence (a raw position's tier -1 record is no downside stop until
Tier 1) and keeps the rest word for word. The golden-rule pointer to the DSL section is shortened to
the section's first words to keep SKILL.md net-neutral.

- SKILL.md: "DSL — how it works per strategy, and which position is in which tier" below. **Never call a live position "unprotected" just because it has no ratchet record — sub-Tier-1 positions have none by design.** For a raw position with no runtime, `false` means **no ratchet** — see the HARD rule below.
- SKILL.md and `dsl.md`: never infer "no stop" from the absence of a resting stop order (DSL exits are runtime-managed, not resting orders) or from a missing ratchet record (sub-Tier-1 positions have none).
- SKILL.md and `dsl.md` (HARD rule): An **empty ratchet record on a sub-Tier-1 position is NOT "unprotected"** … Say **"protected; profit-ratchet arms at Tier 1 (+X%)"** — never "unprotected / unmonitored / no stop." (The engine already frames every `armed: false` position this way in `dsl.note`; do not override it with an "unprotected" reading.)
- SKILL.md: - **A raw position (no runtime, `protected: false`) is "no ratchet," not "no stop."** A one-off position the user placed by hand carries its protection as resting orders, which this engine does not read.
- `dsl.md`: - **`armed: true`** → the position has crossed Tier 1; report **"Tier N, locked at L% of peak, high-water +H% ROE"**
- `dsl.md`: - **`armed: false`** → the position is **sub-Tier-1**: the profit-ratchet hasn't armed yet, but it is **still protected from entry by the phase1 hard stop.** Report it that way — `note` already phrases it
- `output-fields.md`: **`dsl`** — this position's live DSL/ratchet state. **`armed: true`** → `tier_index`, `high_water_roe`, `status`, `locked` (= `lock_hw_pct` at the active tier). **`armed: false`** (no ratchet record — the position is sub-Tier-1) → `hard_stop_roe_pct`, `arm_at_roe_pct`, `roe`, and a `note` … **`armed: false` means the profit-ratchet hasn't ARMED yet, NOT that the position is unprotected** — the phase1 hard stop protects it from entry.

### Reworded in 1.36.0 review — "Tier N" from the 1-based `tier`

`currentTierIndex` is 0-based (index 0 = Tier 1), so "Tier N" read straight off `tier_index` named the
first tier "Tier 0". The engine now emits a 1-based `tier` beside `tier_index`, and these two sentences
name it. The rule (report the live tier, never "no DSL") is unchanged.

- `dsl.md`: report **"Tier N, locked at L% of peak, high-water +H% ROE"** from `tier_index` / `locked` / `high_water_roe` (`status` = `ACTIVE`/`PAUSED`/…).
- `output-contract.md`: **each open position's live tier** from `positions[].dsl` — "armed at Tier N, locked L% of peak" or, for a sub-Tier-1 position, "protected from entry, ratchet arms at +X% — currently +Y%."

## Reworded in 1.36.0 — a named-string preset is not "protected"

The base called a named-string preset "still protected". The runtime installs no ladder for a string
preset (`runtime-descriptor.ts`: "string preset — unsupported by this runtime; no ladder installed"),
and the engine reports such a position as `preset_unenforced`, `downside_stop: false`. The bullet now
defers to the engine's `note` and never says "protected", as `dsl.md` already does. The rule changed
on purpose; the old wording is quoted here.

- SKILL.md: - **Named-string preset** (some strategies ship `dsl_preset: conviction`): `profile.dsl` is `{preset_name, note}` — say "DSL preset: `conviction` (ladder managed by the runtime, not inlined)." Still protected — never call a named preset "no DSL."

# senpi-portfolio 1.37.0 — build-first routing: dropped and reworded sentences

Strategy conversations now start in `senpi-strategy-author`: the closest template is the base the user
forks and makes their own (a lever shifted, a rule of their own); scratch when nothing fits. No skill
pitches a template by name outside that match. Each quote below is base text that left SKILL.md, with why.

> | a yes to CTA 1 / CTA 2, or a digit (after the CTAs: that CTA; after the idle menu: that option), or a question about a menu option or its numbers | the levers / the ready options | `references/output-contract.md` (the menu) |

Why: Reworded in place for build-first routing; the new wording is in SKILL.md.

>   (The menu and its numbers: `references/output-contract.md`.)

Why: Reworded in place for build-first routing; the new wording is in SKILL.md.

>   deploy), name the ready options rather than opening a blank picker. One per line, so it
>   can be answered with a digit:

Why: Reworded in place for build-first routing; the new wording is in SKILL.md.

>   > **What should the idle go into?**

Why: Reworded in place for build-first routing; the new wording is in SKILL.md.

>   > 1. **Penguin** — the Hyperfeed striker, crypto only: one position at up to 10x, 90% margin, on the
>   >    strongest live rotation, with a DSL floor that ratchets up to lock gains. High risk, high reward,

Why: A template pitched by name, unprompted. No skill names a template the user did not ask about; the build route (`senpi-strategy-author`) replaces it.

>   > 2. **Pelican** — the same striker across all assets: crypto plus stocks, commodities, indices

Why: A template pitched by name, unprompted. No skill names a template the user did not ask about; the build route (`senpi-strategy-author`) replaces it.

>   > 3. **Puffin** — the smart-money signal feed, concentrated into one position at a time at high

Why: A template pitched by name, unprompted. No skill names a template the user did not ask about; the build route (`senpi-strategy-author`) replaces it.

>   > 4. **Signals Hunter** — that same feed traded on a clock, spread across several slots.
>   > 5. **Athena** — a smart-money hedge fund, forked under your name.
>   > 6. Or **something else** — tell me how you want to trade and we'll find or build it.

Why: A template pitched by name, unprompted. No skill names a template the user did not ask about; the build route (`senpi-strategy-author`) replaces it.

>   Penguin and Pelican are the two most people start with, because a live rotation shows up fast and
>   they are built to be forked and modified. Be able to unpack the numbers: **-15% SL is 15% ROE, not a
>   15% price move** — at 10x that is a **1.5%** move, so answer in price when asked. On 90% margin it is
>   **~13.5% of that wallet per stop-out**, and say **per stop-out** — their risk guard rails are off, so
>   stops compound. **Up to 10x**, never a flat 10x: the per-name venue cap clamps many instruments, which
>   moves the price behind every number, not the wallet cost. Say **rotations**, never "pumps".
>   Routes 1-5 hand to **senpi-strategy-ops**;
>   route 6 to **senpi-strategy-discover** (find one) or **senpi-strategy-author** (build one). Topping up

Why: A template pitched by name, unprompted. No skill names a template the user did not ask about; the build route (`senpi-strategy-author`) replaces it.

