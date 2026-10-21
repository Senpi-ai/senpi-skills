# senpi-portfolio 1.35.0 — dropped, reworded and moved sentences

Restructure of `senpi-portfolio/SKILL.md` (base `b1916b12`, 90,344 est chars; now 54,994) for the 10.4
size limit. No rule was deleted: every old sentence is in SKILL.md or a reference, or re-pointed in
the next table (`.github/scripts/check_conservation.py` passes against `b1916b12`). Three things a reviewer has to see:

1. **Re-pointed cross-references** (next table): the rule is unchanged, only "above"/"below" now names
   the file or section that holds the target.
2. **Imperatives that left core** (section below): old lines with never / always / must / do not /
   only / verbatim that now live only in a reference. The rule is that every imperative stays in
   SKILL.md core; these did not, so each one needs the maintainer's OK. The four destructive-action
   rules (`close.py` last resort, no restart verb, no "close and relaunch" for
   `withdrawable_unavailable`, no redeploy over a "no" row) are back in SKILL.md's Liveness digest
   word for word, and the "Still true on every follow-up" summary repeats "There is no restart verb;
   close → redeploy is a last resort, asked first" so it replays on 5.7 follow-ups. A line in that list that starts with one of them is there for the sentence after
   it on the same line.
3. **Rules that no longer replay on 5.7 follow-ups** (last section): imperatives that 5.7 kept on every
   follow-up at `b1916b12` and no longer keeps, with what the agent still sees instead.

| Old sentence | Now | Why |
|---|---|---|
| a strategy row's `protected` + `runtime_health` (the runtime exit — read them by the DSL rules above); a read-only row's `protection` counts (live stops | "…read them by the DSL rules below…" | The one wallet list now sits near the top of SKILL.md, before the DSL section. |
| **No runtime registered at all** → the "ACTIVE ≠ running" section below, not this one. | → `runtime-health.md` ("ACTIVE ≠ running"), not this one. | Both sections moved to separate references. |
| `edit_position`, or redeploy via `senpi-strategy-ops`, and never call it "redundant." See **Copy-trade / mirror strategies** below. | "…See `mirror-strategies.md`." | The mirror section is its own reference now. |
| This is DISTINCT from the flat-but-running case above: a flat sleeve with a *registered, ticking* runtime is waiting for a signal | "…the flat-but-running case (SKILL.md, "A flat/empty instance"):…" | That case stayed in SKILL.md; the ACTIVE ≠ running text moved to `runtime-health.md`. |
| Say it is running but has not found a trade, and use "No signals at all" above. | "…use "No signals at all" in `why-not-trading.md`." | That section moved to `why-not-trading.md`. |
| **`mirror`** — a **copy-trade** strategy: no runtime BY DESIGN (see **Copy-trade / mirror strategies** below). | "…(see `mirror-strategies.md`)." | As above. |
| A raw position is the reverse case — the bullet above. | "…the raw-position bullet in SKILL.md." | The resting-orders bullet moved to `dsl.md`; the raw-position bullet stayed in SKILL.md. |
| See "DSL — how it works per strategy" above. | "See `dsl.md`." | In `output-fields.md` now; the ladder detail is in `dsl.md`. |
| Config-level posture, not a live per-position check — see the tri-state rule above. | "…see the tri-state rule (SKILL.md, Golden rules)." | In `output-fields.md` now; the rule stayed in SKILL.md. |
| not a hand-picked position the scanner will just re-open. See the HARD rule under "A strategy is ALL its wallets" and "Recommend at the STRATEGY level." | "…See the HARD rule (SKILL.md, "The job, and the hard rules") and…" | In `output-contract.md` now; the no-no moved up into the replayed head (see the last section). |

The old "For a NARROW ask, run only the minimal step" table is folded into the router at the top of
SKILL.md; its four rows are kept word for word in the router's first column.

## Imperatives that left core

Each entry quotes the `b1916b12` sentence(s) holding a moved line, grouped by the reference that holds
it now and the router row (or SKILL.md pointer) that sends the agent there. Many are field glossary,
long-tail cases or incident detail; some are real rules. Generated from the conservation check's own
matching (88 moved lines, quoted as 78 entries), so it is the complete list.

### `references/dsl.md` — reached by router row "are my strategies protected? / do they have a stop-loss? / … / what's their DSL" (protection)

- Read it as "arms at +8%, locks 40% of the peak by +18%, 60% by +35%, 78% by +60%, 88% by +100%." `lock_hw_pct: 0` at Tier 1 = priming only (arms the trail, no lock yet).
- **Config-level `protected` ≠ live per-position tier.** `strategy.protected` / `group.protected` (`true`/`false`/`null`) is the **config posture** — `true` only when the deployed `runtime.yaml`'s `exit:` block was actually READ by the engine; `null` means the read didn't happen, never assume `true` from `skill_name` alone.
- **For a runtime-managed position, never infer "no stop" from the absence of a resting stop order.** DSL exits are **runtime-managed**, not resting venue orders — you won't see them as open orders.

### `references/mandate.md` — reached by router row "are my strategies protected? / how are my strategies doing / … / mandate" (verdicts)

- **Catalog enrichment (templates only, may be absent):** `belief_plain`, `thesis`, `archetype`, `sub_style`, `asset_classes`, `risk_level`, `time_horizon`, `tagline` — extra facets the engine adds for a strategy deployed from one of our packages (keyed by `skill_name`).
- `profile.source` — `"registry"` (authored/custom, description only), `"registry+catalog"` (one of ours, description + facets), or `"catalog"` (facets only, registry unreadable).
- **Anti-patterns — these exact misreads happened live; never repeat them:**

### `references/mirror-strategies.md` — reached by router row "a copy-trade (`strategy_kind: "mirror"`)"

- Its `name` is **"copy of `mirror_of`"** — never call it "unnamed."
- **Judging one, and the ONLY correct remedies.** A mirror's risk = the copied trader's risk × `mirror_multiplier`.
- High leverage or a lopsided book is worth *surfacing* ("this copies `mirror_of` at 20x — a sharp adverse move liquidates fast; it has [no] strategy-level stop"), but the fix is **never** a DSL or a per-position stop.

### `references/output-contract.md` — reached by router row "analyze my portfolio / how am I doing" (the full read)

- `portfolio.py money` → **narrate the one wallet list IMMEDIATELY** — `book.wallets` as one table in the engine's order, then `book.totals.line`: the book total, the managed subtotal (`grand_total_usd`) broken into the three buckets (idle-in-embedded / idle-in-strategies / deployed-in-positions), each labeled by *where*, plus `reconciles`, and the read-only subtotal beside it.
- Don't wait for the other steps.
- **Per-strategy verdict (the real value).** For **each `strategy_groups[]` entry** (one per real strategy — never one per wallet), in this order:
- "cub is a K-shaped long/short dispersion book — long the structural winners, short the laggards; the P&L is the spread." **If `is_multi_wallet`, name it as ONE strategy across its sleeves** ("cougar is a market-neutral long/short pair") — never as two strategies.
- **Positions as evidence — across ALL the strategy's instances.** The open positions (from every instance in the group) that *show* it's on-mandate: direction, leveraged return (`return_on_equity_pct`), and **vs the market** (`market_24h_pct`, `vs_market`) — "short ETH, +11% on margin, *with* today's 4% selloff." A group instance in `flat_instances` is the strategy's **OTHER sleeve waiting for its signal** (for a multi-wallet strategy) or the whole strategy waiting for its setup (for a single-wallet one) — say that, never "dead money." Flag any position fighting the tape *for a directional-momentum mandate* / near `liq_px` / oversized.
- **DSL protection — ladder + live tiers.** State the group's `protected` posture (⟹ **all** instances ship a DSL exit), then **how its DSL works** from `group.dsl` / `profile.dsl` (hard stop at `hard_stop_roe_pct`, ratchet arms at `arm_at_roe_pct`, the tier ladder), then **each open position's live tier** from `positions[].dsl` — "armed at Tier N, locked L% of peak" or, for a sub-Tier-1 position, "protected from entry, ratchet arms at +X% — currently +Y%." **Never say a live position has "no DSL" because it lacks a ratchet record** (sub-Tier-1 positions have none by design).
- **Any lever is WHOLE-STRATEGY.** If you suggest close / pause / adjust-config / top-up, it applies to the **entire strategy (all its wallets)** — never one sleeve.

### `references/output-fields.md` — reached by router row "what a JSON field means"; "How to run the engine" sends every field question here

- **These are two views of one wallet, not two separate pools.** The `withdrawable` (idle cash) is **shared** and reported *identically* in both views — so it is counted **once**, never summed.
- Each view's `accountValue` = that shared idle + only *that* DEX's position equity, so `wallet_value = main.av + xyz.av − shared_idle`.
- Same fail-open contract as `all`: each step returns valid JSON with `meta.warnings` on partial data and **never crashes on a missing/corrupt state file, nor on a runtime CLI that is absent or cannot even be spawned** (it self-heals by recomputing its prerequisites — every step also works STANDALONE, just slower; an unreadable runtime costs you the runtime fields, which come back `null` + a warning, not the read).
- PRESENT THE LIST FROM HERE.** `wallets[]` (sorted by the engine — keep its order): `label`, `kind` (`managed` / `read-only`), `origin` (`embedded` / `strategy` / `saved`), `value_usd` (null = couldn't load), `loaded`, `open_positions`, `unrealized_pnl_usd`; a strategy row adds `strategy_group` (its `strategy_groups[].label`), `wallet_count`, `strategy_wallets[]`, `wallets_couldnt_load`, `protected`, `runtime_health`, `realized_pnl_usd` (or `not_read_this_step` on the `money` step); a saved row adds `address`, `protection` counts, `excludes_coins`, `no_hyperliquid_activity`, `positions_scope`, `access`, `not_applicable`.
- `totals`: `total_usd`, `managed_usd` (= `grand_total_usd`), `managed_breakdown` (the three buckets + `reconciles`), `managed_complete`, `read_only_usd`, `read_only_wallets` (`total` / `loaded`), `read_only_note`, `excludes` (+ `strategies_unreadable`), `excludes_note`, `line` (the rendered total — quote it).
- `deep_dive`: `offer`, `question`, `order` (labels, largest first — only wallets with something to go deeper on).
- `external_wallets` — the saved wallets' raw reads: `status` (`ok` / `unavailable`) and `wallets[]`, each `state` verbatim from `account_get_external_wallets` (see "One wallet list — every wallet first-class").
- `totals` — the MANAGED money map: the three buckets + `grand_total_usd`, `unrealized_pnl`, and a `reconciles` flag (cross- checks the per-wallet sum against the portfolio aggregate; if `false`, say the numbers don't tie out and lead with the per-wallet figures).
- **This is the unit of analysis and recommendation** — present and reason at this level, never at individual wallets.
- `protected` (`true` / `false` / `null`) — `true` **only if ALL instances are protected**; `null` if ANY instance is `null` — an unread instance is never laundered into a `false`.
- For a multi-wallet strategy these are the OTHER sleeve(s) **waiting for a signal** — NOT redeployable idle, never "dead money."
- **Trust `name` as a name only when `name_source` is `"strategyName"`.**
- Optional **catalog enrichment** (templates only, keyed by `skill_name`; `null` for authored strategies): `belief_plain`, `thesis`, `archetype`, `sub_style`, `asset_classes`, `risk_level`, `time_horizon`, `tagline`.
- **This is the yardstick — judge the strategy against `profile.description`, not memory and not a momentum benchmark.** `profile` is `null` only when the strategy is in neither the runtime registry nor the catalog (`meta.profile_source` records `registry`/`catalog`/`mixed`/`null`).
- `protected` (`true` / `false` / `null`) — `true` only when the deployed `runtime.yaml`'s `exit:` block was actually READ by the engine (`dsl_preset` or `engine: dsl`); a `skill_name` attribution stamp alone no longer counts.
- `null` = the runtime read did not answer — say "could not verify on this host," never "protected" or "not protected." Config-level posture, not a live per-position check — see the tri-state rule above.
- On a read failure `realized_pnl` and the counts are `null` and a `meta.warnings` entry is added — treat as "unavailable," never as zero.
- Never present it as "no DSL." If the ratchet read failed entirely, every position still gets this config-based `armed: false` object (+ a `meta.warnings` note).
- If the runtime registry is unreadable, mandates fall back to the catalog (templates only); if that's also gone, `profile` is `null` and you judge on behavior.

### `references/rationale.md` — reached by Reference index line "a user disputes a hard rule" (the incidents behind the rules; the rules themselves are in SKILL.md)

- "Close ox's \$600 wallet, keep the \$1,400 one" — **gutting one sleeve of a risk-parity core destroys the design.** The two sleeves are balanced *against each other*; keeping one is a different, unbalanced strategy the user never chose.
- "Keep cougar's short sleeve, repurpose its flat long sleeve" — **closing one sleeve of a long/short strategy leaves a NAKED directional position.** A market-neutral book with only its short leg is just a short — the exact opposite of neutral.

### `references/runtime-health.md` — reached by router row "is it running / health check / degraded / not running / could not verify"; the Liveness digest in SKILL.md names it

- Its `runtime_registered` / `not_running` / `running_blind` / `protected` are **`null` (N/A), never `false`** — do NOT report it as "not running / unprotected," do NOT tell the user to add a DSL, set a stop via `edit_position`, or redeploy via `senpi-strategy-ops`, and never call it "redundant." See **Copy-trade / mirror strategies** below.
- Such a strategy is **not running at all** — its scanner has never ticked, so it has **no DSL and no guardrails** — even though it shows ACTIVE and holds capital.
- `running_blind: true` is a third, previously-invisible state: the runtime IS registered and ticking, but its entry scanners never wired (`running — NO ENTRY SCANNERS`), so it **cannot produce entry signals** — report **⚠ RUNNING — NO ENTRY SCANNERS**, not a clean "running." When `runtime_registered` (or `not_running` / `running_blind`) is `null`, the registry read did not answer — say **"could not verify on this host,"** never "running" and never "not running."
- Only this earns "running / protected."
- **Do not warn on it.** Do not put a ⚠ on the strategy, and do not send the user to a diagnostic.
- This is the one that always deserved the warning.
- **Say "has not CLOSED a trade in Xh", never "has not traded"**: a position carries no open time, so an entry taken since that close is invisible here.
- Quiet while **holding** means fully allocated, not stuck — check slots and entry gates first, and note every guard rail halts ENTRIES only, so a gate counter is moot while every slot is full.
- No closed record is never flagged: "never closed" cannot be told apart from "deployed ten minutes ago".
- **`risk_pause`** (beside `runtime_health`, never instead of it) — the runtime's OWN entry gate says `CLOSED` or `COOLDOWN`: the strategy is **healthy and paused by its own rule** (daily entry cap, daily loss halt, drawdown halt, a cooldown).
- Name the gate and quote its `reason` verbatim ("Max Entries/Day — Max entries: 4/4 entries today"), say when it lets go (`reset`: daily gates at 00:00 UTC, cooldowns on their own; a latched drawdown halt with `drawdown_reset_on_day_rollover: false` never lets go on its own — it waits for the user's decision, and you never clear it or resume entries without their explicit approval), and that nothing is broken.
- Say *"running — some scans errored"*, never *"your strategy is broken."* Confirm with `status.py <id>` before going further.
- **They disagree.** Do **not** give a clean all-clear — a scanner can read `healthy` and still be blind.
- **When the two disagree, say which one you are trusting and why.** If the money is moving, that is the headline and the field is the footnote — do not report the field and bury the evidence.
- #### When it IS genuinely broken, act — don't hand the user a to-do
- Only the last step touches their money, and only that one needs their consent:
- **Before the ladder: if the row carries `runtime_reason`, say it.** It is the runtime's own words, read off `senpi status --json`'s scanner rows (`degradedReason`) — on today's runtime only a crash loop, e.g.
- No `runtime_reason` means the runtime gave none — never invent one; the ladder finds it.
- **Mind the vocabulary:** `status.py` reports the engine's raw words, and they do not line up with this skill's.
- **`unknown`** — registered, but health **not yet proven**: a scanner it has never heard from, a runtime just restarted, or a `senpi status` document that carried no health verdict this engine recognises.
- Say **"runtime liveness unverified — not confirmed running"** — never upgrade to "healthy/protected" or downgrade to "broken." The runtime is deliberately fail-closed about `unknown` (it refuses to call an unproven scanner healthy); repeating it back is the whole point.
- A runtime *process* that exists (`status: running`) is not a health verdict and never reaches `live` on its own.
- Say **"could not verify on this host"** — never "protected," "not protected," "running," or "not running." **`null` is not `false`.** `meta.warnings` names the failed command; quote it, never invent a cause.
- Never `live` / `recovering` / `degraded` / `not_running` / `unverified` — those don't apply to it.
- Judge it on `mirror_of` + `mirror_multiplier` + `stop_loss_pct` / `take_profit_pct`, never on a runtime it was never meant to have.
- **Minimum runtime — `openclaw senpi runtime list --json`.** The engine asks the runtime for its own inventory through that command (it never reads the runtime's private state files).
- **That whole-fleet pattern means the runtime on this box is too old for this skill's registry read, not that the strategies are broken.** Say so in those words, quote the warning, and do not diagnose the strategies from it: no strategy may be called running, not-running, protected or unprotected off that run.
- A thorough health check does not stop at the verdict: for a strategy that is `not_running`, `degraded` or `unknown` — **never for `recovering`** — **`python3 senpi-strategy-ops/scripts/status.py <id>`** re-asks the runtime directly and returns its verdict **beside a position count**, which is the corroboration below.
- Reference the right tool to *confirm* — never re-derive its analysis here.

### `references/why-not-trading.md` — reached by router row "why hasn't it traded / why didn't it open X / no signals"; the Liveness digest in SKILL.md names it

- A running, healthy strategy that has not traded is the single most common "is it broken?" question, and the answer is almost never "it's broken".
- only if it persists
- The strategy has no way to size a position, so it can never open one.
- The gate carries its own `reason`, so name **which one**; never just "a risk gate".
- no, until it resets — but a drawdown halt with `drawdown_reset_on_day_rollover: false` never resets on its own: that is **the user's decision**, never clear it for them
- Check live positions before saying anything, and never suggest retrying until you have.
- **Do not offer "close and relaunch" for `withdrawable_unavailable`.** It reads like the obvious fix and it is not one: relaunching rebuilds the same percent-sized recipe against the same balance read, and a fresh wallet does not mend a read that is wedged.
- The table above only covers signals that were **evaluated**.
- Two commands, and they answer different questions — `senpi runtime list` prints only id, source and status, so it will show you `running` and leave you stuck.
- **Nothing qualified.** The entry bar has not been met — name it, never "being selective": the binding gate, its threshold and the universe count from its `runtime.yaml` (`senpi-strategy-ops` `references/liveness-verification.md`, "Healthy, live — and it has never traded").
- **Never call it healthy on the strength of `health=healthy`.** For this failure that field is exactly the thing that is wrong — which is why users find it by noticing the quiet, not from anything we show them.
- It is the one outcome that never reaches the per-signal path, so it carries **no `reason_code`** and will not appear in the table.

## Rules that no longer replay on 5.7 follow-ups

At `b1916b12` 5.7 kept head 0–11,829 + tail 86,378–end of SKILL.md on every follow-up. The file is now
head-only: 5.7 keeps its first ~15,930 chars (`openclaw_measure.replay_57`). The router, the follow-up
rule, the one-list rules, "Source of truth", "Use this skill FIRST", "This skill reads" and the no-no all
replay; `test_portfolio_skill_layout.py` pins them and the mode. These base-replayed imperatives are past
the cut now (all still in SKILL.md, word for word); what the agent still sees on a follow-up:

| No longer replayed | Still replayed instead |
|---|---|
| The wallet model: "Only the Hyperliquid legs can fund strategies — EVM USDC does not bridge in"; the trap ("It is NOT idle cash in the embedded wallet", "always say *where*", "The engine computes these as two separate fields precisely so you don't mix them") | "Still true on every follow-up": "Say where idle sits. `total_withdrawable` is idle in strategy wallets, NOT embedded idle cash … Only embedded idle's Hyperliquid legs fund strategies." |
| Cross-DEX: "Never add the two views' account values or withdrawables yourself"; "counted once, never summed" and the `accountValue` formula (now in `output-fields.md`) | "Still true…": "Never add a wallet's `main` + `xyz` views (shared collateral)." |
| "they are `read-only` rows in the one wallet list and the read-only subtotal beside `grand_total_usd`" | One wallet list: "**Never Senpi money.** … it is a `read-only` row in the one list and part of `book.totals.read_only_usd` only." |
| "**Present each group as one strategy**; never present its wallets/instances as separate strategies." | One wallet list: "**A Senpi strategy is one row with all its wallets.**" and "`strategy_wallets[]` are its sleeves, never rows of their own". |
| CTA 1: "never a saved wallet, which is read-only (quote its `access` line). Confirm before any change; never trade unprompted." | "**No write suggestions on these wallets** … CTA 1 applies to Senpi wallets only", the access line, and "Still true…": "confirm before any change, never trade unprompted". |
| CTA 2: "never `book.totals.read_only_usd` or any read-only row, which Senpi cannot deploy"; "Propose; never deploy without confirmation." | "Never Senpi money … CTA 2 ("put the idle to work") never counts it"; "Still true…": "propose, never deploy without confirmation". |
| CTA 2's menu and numbers: the Penguin … Athena lines, "**Up to 10x**, never a flat 10x", "Say **rotations**, never "pumps"" | "Still true…": "CTA 2: -15% SL is 15% ROE; up to 10x, never a flat 10x; say rotations, never "pumps"." The menu itself does not replay: the router row "a yes to CTA 1 / CTA 2, or a digit" sends the agent to `references/output-contract.md`, which carries a verbatim copy of the "Mandatory closing" CTA block (pinned to match SKILL.md). |
| Resilience: "don't report an empty portfolio as "$0."", "Report realized PnL as **unavailable** … never as `$0`", the `TOTALS DO NOT RECONCILE` re-run, "**Never** report `total_withdrawable` as embedded idle, never skip a wallet, never skip the CTAs" | "Still true…": "A failed read is not $0 or flat …" (one line per case) and "Never skip a wallet or the CTAs." |
| Install: "Stdlib only, no other runtime dependencies." | Nothing: an install note, not a follow-up rule. |

The persona paragraph ("You are a sharp portfolio analyst…", no imperative) also left the replay; it is
now "The analyst's bar", after the wallet model.

