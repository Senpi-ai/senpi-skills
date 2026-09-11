# The walkthrough (Step 0.75) — what to say before the budget question, and how to make the fork

The resident rule is in SKILL.md: before a dollar is asked for, ops reads the package and says what it does,
two levers, the fee load and design budget, and the name; asks as-is-or-fork once; the budget question comes
after the answer. This file is the depth: which levers, the name rules, the cost classes, the consent
sentences, and the mechanics of the fork on disk.

## 1. What it does and how it is set by default

The same four lines as the post-live "How it runs" block (cadence · scoring and the entry bar · protection
as outcomes · daily cap), read from `strategy.yaml` (`catalog:`) and each instance's `runtime.yaml` — plain
language, no YAML. Say it here, before the money moves, then again after it is live.

## 2. Two levers (one for a `tier: starter` template)

Each with its default, a sensible range, and what moving it changes. Pick the two that matter for THIS
template from `scanners[].inputs` (the template-specific knobs: an agreement bar, a threshold, a cohort
size, a basket) and from the universal ones every runtime.yaml has — **read these keys first, they are in
100% of the catalog**:

| Lever | Key | Say it as |
|---|---|---|
| Max positions | `strategy.slots` | "up to N open at once" |
| Leverage | `strategy.default_leverage` | "N×" |
| Risk per trade | `exit.dsl_preset.phase1.max_loss_pct` | in ROE **and** in price at the leverage: 15% ROE at 5× is 3% of price |
| Daily entry cap, drawdown halt | `risk.guard_rails` | "at most N entries a day; halts at −X%" |
| Cadence | the scanner's `interval_seconds` | "looks every N minutes" |

Position size (`strategy.margin_pct`, present in about half the catalog) is the biggest *felt* lever after
the stop — offer it where it exists. Scanner-side `maxSlots` / `maxLeverage` / `leverageTiers` exist in a
minority of templates: read them where present, never look for them first. A static universe is a lever
where there is one. Name the rest in one line.

## 3. The name

Every template deploys under the user's name, moved levers or not. Propose the possessive —
**`<User>'s <Template>`**, `PurpleFrog's Starling` — and invite their own name in the same sentence; their
word wins. `<User>` is the name they use with you or their Senpi username (`user_get_me`), never an id or
an email; ask once if you don't have it. A second fork of the same template needs a name that tells them
apart — ask.

## 4. The question

*"Run it as-is, or shift one of these and make it yours? Either way it deploys as PurpleFrog's Starling — or
give it a name of your own."* One word skips it — "as-is" means the defaults, still under their name. Never
a gate, never re-asked; the budget question comes after the answer.

## 5. Fee load and the design budget

Say what the cadence, slots, margin and leverage cost in fees at the budget on the table — a 10-minute
scanner turning a $200 book pays more in fees than it wins or loses on direction — and say the design
budget (`min_budget` is the floor; the catalog card says what the design assumes). **Deploying below the
design budget needs the user's explicit yes after hearing what degrades**; the `[W_BUDGET_*]` warn after
the fact is not that consent.

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

## Making the fork on disk (until a `fork` verb ships)

Copy the fetched package to `<strategies root>/<template>-<user-slug>/`. The root is what
`python3 senpi-strategy-ops/scripts/deploy.py where` prints — `SENPI_STRATEGIES_DIR`, else
`$OPENCLAW_WORKSPACE_DIR/strategies`, else `/data/workspace/strategies` — e.g.
`/data/workspace/strategies/starling-purplefrog/`. **Never a CWD-relative `strategies/`**: the
skills-manager wipes that on the next version bump (the 2026-07-30 incident). Copy the package files only:
leave out `.deploy-state.json` (a legacy marker that would make the fresh fork look like a previously
deployed package) and any `__pycache__`. Then:

- `strategy.yaml` → `id: starling-purplefrog`, `catalog.name: "PurpleFrog's Starling"` (or the user's own
  words), add `forked_from: { id: starling, version: "1.3.0" }` — the loader ignores keys it doesn't know;
  this one is the lineage.
- every instance `runtime.yaml` → `group: starling-purplefrog`, `name: starling-purplefrog-<instance>` (the
  schema's linkage rule), and prefix its `description` with `PurpleFrog's Starling — forked from Starling
  1.3.0. ` (the mandate `senpi-portfolio` reads back).
- apply the moved levers with `edit` — values only, never structure; `wallet_env` names stay as they are.
- the normal gate: `python3 senpi-strategy-author/scripts/validate_strategy.py <dir>` (it warns on a stop
  that is too tight at the leverage, sizing with no free-margin gate, a daily cap at or below the slots, a
  fee load above 0.5% of the budget a day, and refuses a maker-only entry — relay every warn),
  `openclaw senpi validate <recipe-dir>`, `deploy.py validate <dir>`, then
  `deploy.py create <dir> --budget <usd>` — pass the **directory**, not the template id.

A fork is a copy: a later fix to the template does not reach it — say so if asked.
