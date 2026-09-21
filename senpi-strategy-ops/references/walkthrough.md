# The walkthrough (Step 0.75) — what to say before the budget question, and how to make the fork

The resident rule is in SKILL.md: before a dollar is asked for, ops reads the package and says what it does,
two levers, the fee load and design budget, and the name; asks as-is-or-fork once; the budget question comes
after the answer. This file is the depth: which levers, the name rules, the cost classes, the consent
sentences, and the mechanics of the fork on disk.

## 0. Every block is bullets

A paragraph on the walkthrough gets skipped — the eye lands on the bullets beside it. Nothing here is
prose: **what it does** is three bullets (the signal it reads · when it enters · how it exits), **how it
is set** is the four lines, each lever is one bullet, the name is one line. No config keys anywhere in
the chat — a key is for the edit, and a user cannot know what `marginPctBase` means.

## 1. What it does and how it is set by default

Three bullets for what it does, then the same four lines as the post-live "How it runs" block (cadence ·
scoring and the entry bar · protection as outcomes · daily cap), read from `strategy.yaml` (`catalog:`) and
each instance's `runtime.yaml` — plain language, no YAML. Say it here, before the money moves, then again
after it is live.

## 2. Two levers (one for a `tier: starter` template)

Each lever is one bullet in the user's words — **what it controls**, what it is set to now in their
terms, what moving it down and up does to trades, fees and risk, and what it means at the budget on the
table. The key never appears in the chat; it is what you edit on the fork afterwards. The shape:

- *"**How picky it is about crowding** — now: 65% of the cohort on one side before it counts. Lower =
  more trades (and more fees); higher = fewer, stronger ones."* (`tiltThreshold: 65`)
- *"**How much of the wallet each position uses** — now 15%: about $95 of margin per position on your
  $635, $285 of exposure at 3×. Higher = bigger positions, fewer of them at once."* (`marginPctBase: 15`)

Pick the two that matter for THIS template from `scanners[].inputs` (the template-specific knobs: an
agreement bar, a threshold, a cohort size, a basket) and from the universal ones every runtime.yaml has —
**read these keys first, they are in 100% of the catalog**:

| Lever | Key | Say it as |
|---|---|---|
| Max positions | `strategy.slots` | "up to N open at once" |
| Leverage | `strategy.default_leverage` | "N×" |
| Risk per trade | `exit.dsl_preset.phase1.max_loss_pct` | in ROE **and** in price at the leverage: 15% ROE at 5× is 3% of price |
| Daily entry cap, drawdown halt | `risk.guard_rails` | "at most N entries a day; halts at −X%" |
| Cadence | the scanner's `interval_seconds` | "looks every N minutes" |
| Sleeve weighting (multi-wallet funds) | `instances[].funding_share` in `strategy.yaml` | "65/35 alpha/hedge" — the shares must total 100% and each wallet keeps the $10 floor; on a fund it is the first lever |

Position size (`strategy.margin_pct`, present in about half the catalog) is the biggest *felt* lever after
the stop — offer it where it exists. Scanner-side `maxSlots` / `maxLeverage` / `leverageTiers` exist in a
minority of templates: read them where present, never look for them first. A static universe is a lever
where there is one. Name the rest in one line.

## 3. The name — two spellings, one fact

Every template deploys under the user's name, moved levers or not. Read their Senpi username first
(`user_get_me` → `userName`) — never an id or an email; ops reads the same field when it forks. The name is
**stated, never asked** — unless the account has no username, and then you ask what to call it:

*"It deploys as **Ignas's Phalanx** (`ignas-phalanx` in your strategy list) — say a different name if you
want one."*

Two spellings because the platform has two places: the spoken possessive (`Ignas's Phalanx`) lives in the
fork's `catalog.name` and the runtime description — it is what you say, before and after; the strategy
list shows the id the deploy verb derives from the package (`<owner>-<template>`, lowercase, no spaces:
`ignas-phalanx`). Their own words win: `--name "Shield Wall"` → `shield-wall`. A second fork of the same
template needs a name that tells them apart — ask.

## 4. The question

*"Run it as-is, or shift one of these first? Say **go** and it deploys as-is."* A bare "go", "deploy",
"yes" or "as-is" = the defaults, under their name. A status check — "did you finish?", "how's it going?" —
is not a yes: answer it and wait. Never a gate, never re-asked; the budget question comes after the answer.

## 5. Fee load and the design budget

Say what the cadence, slots, margin and leverage cost in fees at the budget on the table — a 10-minute
scanner turning a $200 book pays more in fees than it wins or loses on direction — and say the design
budget (`min_budget` is the floor; the catalog card says what the design assumes). **Deploying below the
design budget needs the user's explicit yes after hearing what degrades**; the `[W_BUDGET_*]` warn after
the fact is not that consent.

**"What does it need?" is answered with the calculated minimum — never the $10 wallet floor.** The floor is
what the *platform* accepts; the calculated minimum is what the *design* needs, and below it the smallest
slot cannot reach the engine's bumped notional, so the strategy **no-trades**: it funds, it installs, it
scans, and it never opens. `min_budget.py` is the arithmetic —
`max($10, $12 / (marginPct/100 × min_leverage) + $1.50)` per wallet — so a 15%-margin, 3× sleeve needs about
$28, not $11.50, and quoting the floor hands the user a funded strategy that cannot trade. Quote the
calculated figure (`minBudget` on the deploy report, `min_budget` on the catalog card), and if neither is to
hand, do that division before naming a number. **Never describe a below-minimum deploy as "tight, but it'll
trade."**

## 6. Guardrails are consent, both ways

Removing a guardrail (a daily-loss limit, a cap) or lowering a threshold you just recommended gets a
one-line consequence and an explicit yes — never "done". "It tripped three times in four days" is the
sentence, not an afterthought.

## The cost class — a fact beside the choice

State it once, never as a discouragement: as-is is the cheapest thing the agent does; a lever fork adds a
little; a bespoke edit (a new universe, a different signal — `senpi-strategy-author`'s edit path) adds
more; a strategy written from scratch is roughly two to three times a template. Named installs ("just
install spider") get the walkthrough too — one turn, skippable — the user who names a strategy is the one
most likely to fork it.

## The fork — the verb makes it

`deploy.py create <template> --budget <usd>` does the fork and the deploy in one go: it reads the user's
username (`--owner` only overrides it) and copies the fetched template to `<strategies root>/<username>-<template>/` (the root `deploy.py where`
prints — never a CWD-relative `strategies/`), leaving out `.deploy-state.json`, any proof and any
`__pycache__`; rewrites `id`, `catalog.name` (the spoken name), each runtime's `name`/`group` linkage and
the first line of its `description` (`Ignas's Phalanx — forked from Phalanx 1.1.0.` — the mandate
`senpi-portfolio` reads back); records `forked_from: {id, version}`; validates the copy; proves it
(`openclaw senpi validate --stage live`); then deploys **the fork**. `--name "<their words>"` names it their
way. A second `create` reuses the same fork. A user ID passed as a name is refused, and so is a bare template
id when no username can be read — nothing is created; ask what to call it and pass `--name`. From then on the id is the fork's (`ignas-phalanx`) for `status`, `verify`,
`update` and `close`.

When levers move first: `deploy.py fork <template>` makes the copy and stops; apply the
moved levers with the edit path — values only, never structure; `wallet_env` names stay as they are — run
the normal gate (`python3 /data/.openclaw/skills/senpi-strategy-author/scripts/validate_strategy.py <dir>` — it warns on a stop
that is too tight at the leverage, sizing with no free-margin gate, a daily cap at or below the slots, a
fee load above 0.5% of the budget a day, and refuses a maker-only entry — relay every warn; then
`openclaw senpi validate <dir>`), and `deploy.py create <dir> --budget <usd>` — the **directory**.

A fork is a copy: a later fix to the template does not reach it — say so if asked.
