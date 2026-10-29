# senpi-strategy-author 3.16.0 — build-first routing: dropped and reworded sentences

Strategy conversations now default to building the user's own strategy (`senpi-strategy-author`).
A template is opt-in: offered only when the user asks for something pre-built, wants to browse, or
names one, and never named unprompted. Each quote below is base text that left SKILL.md, with why.

>   Build or edit a Senpi trading strategy — interactively, ONE decision at a time.
>   There is no paper-trading mode: a strategy is tested with `senpi validate` and a
>   $10-floor live run, never with a scanner re-run on a timer (a model call per firing).
>   Use for "build a strategy", "create a strategy from scratch", "design a
>   strategy", "I have a trading idea", or ANY strategy that needs DSL (a
>   runtime-supervised exit: stop-loss, trailing stop, profit-lock ladder) — a
>   runtime.yaml authored here is the ONLY way to carry a DSL; raw MCP
>   strategy_create* / create_position calls cannot, and must never stand up a
>   named or protected strategy. Offers the closest TEMPLATE first (via
>   senpi-strategy-discover) as the quick start to the user's OWN strategy, with
>   fork-before-deploy, bespoke edit and scratch as peers, each with its cost class;
>   never asks for a budget to build (it is asked once, at deploy); exits default to

Why: Description rewritten build-first: it is the routing surface every turn. The new description is in the frontmatter.

>   installing (senpi-strategy-ops) or picking one to run (senpi-strategy-discover).

Why: Description rewritten build-first: it is the routing surface every turn. The new description is in the frontmatter.

> ## Start here — templates first, as the quick start to THEIR strategy; the other routes are peers

Why: Template-first routing flipped: building the user's own strategy (`senpi-strategy-author`) is the default; a template only when they ask.

> A template is not "our strategy" the user adopts — it is the fastest way to launch **their** strategy, and
> every one deploys under their name (`PurpleFrog's Starling`, or a name of their own) after ops walks them through what it does,
> how it is set and which levers to shift (ops Step 0.75). So **before the interview, offer the routes as
> peers — template first, each with its cost class as a fact — never a gate, never a downsell:**

Why: A template pitched by name, unprompted. No skill names a template the user did not ask about; the build route (`senpi-strategy-author`) replaces it.

> 1. **Start from a matching template** — *the quick start.* If the user gave any thesis hint, **hand it to
>    `senpi-strategy-discover`** with their words — that skill surfaces the closest matching template(s),
>    which you name in the offer (*"**Cougar** — equity long/short — is close to what you described"*).
>    Discover owns the catalog and the match; ops deploys it under the user's name, as-is or with levers
>    moved. Don't reach into its internals or rebuild the catalog here. Cost: the cheapest thing the agent does.
> 2. **Fork it before it goes live** — the walkthrough's levers (ops Step 0.75): a threshold, the slots, the
>    leverage, the stop, the daily cap. Values only, no new code. Cost: a little more than as-is.
> 3. **Make a bespoke edit to a template** — a new universe, a different signal, a changed exit shape: this
>    skill's edit path ("Editing an existing strategy") on the forked copy, **before** deploy. Cost: more than
>    a lever fork, still well under scratch.
> 4. **Design your own from scratch** — first-class, fully supported; you run the interview below. Cost:
>    roughly two to three times a template. Worth it when nothing close exists, or when the user wants it.

Why: A template pitched by name, unprompted. No skill names a template the user did not ask about; the build route (`senpi-strategy-author`) replaces it.

> **Tone — even-handed, ownership first:** template-first is *"the quick start,"* **never** *"the right way"*;
> scratch is a **peer**, not a downsell, and it works — say so. **The user's choice is final**: if they pick
> scratch (or already gave a specific thesis), go straight into the interview — **never re-pitch or nag**.
> Calibrate to the signal (vague ask → lean template-first; clear custom thesis → surface the closest match
> **once**, then build). No close fit → say so and go straight to scratch; never force a bad-fit template.

Why: Cost stays a true fact, said when asked, never a reason to pick a template.

> Everything below is the **scratch / bespoke** path — the interview, once the user chooses to build or to change more than a lever.

Why: Reworded in place for build-first routing; the new wording is in SKILL.md.

> Before the template offer / Decision 1, read the **deployable** balance ONCE: `account_get_portfolio` → `total_in_hyperliquid` + `total_spot_usd_in_hyperliquid` + EVM stables in `token_balances[]`.

Why: Template-first routing flipped: building the user's own strategy (`senpi-strategy-author`) is the default; a template only when they ask.

>    + `discovery_get_trader_state` — headcount and tick-over-tick change, the `senpi-smart-money` method — and name the
>    template that already does it (the Starling / WhaleHunter / Stingray family) as the fork option before writing a new one.

Why: A template pitched by name, unprompted. No skill names a template the user did not ask about; the build route (`senpi-strategy-author`) replaces it.

