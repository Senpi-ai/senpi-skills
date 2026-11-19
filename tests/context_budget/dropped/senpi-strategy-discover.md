# senpi-strategy-discover 2.54.0 — fork-first routing: dropped and reworded sentences

Strategy conversations now start in `senpi-strategy-author`: the closest template is the base the user
forks and makes their own (a lever shifted, a rule of their own); scratch when nothing fits. No skill
pitches a template by name outside that match. Each quote below is base text that left SKILL.md, with why.

>   Help a user choose a Senpi trading strategy to deploy — a conversational,
>   analyst-style picker. Use when the user asks "what should I trade?", "recommend
>   a strategy", "help me pick a strategy", "what's winning?", "set me up", "I have
>   a view on the world (a war, the economy, one coin winning) — trade it", "run a
>   hedge fund / all-weather / tail-risk book", or wants a strategy but has NOT
>   named a specific one — and every discretionary mandate: "deploy whatever you think is
>   profitable", "trade for me", "be aggressive", "put it to work", "you decide". A mandate is a
>   strategy request even when it names no strategy and no coin; it is never answered with
>   hand-picked raw positions. Surface the closest matching TEMPLATE first — the quick start
>   to the user's OWN strategy (every template deploys as `<User>'s <Template>` — or a
>   name of their own — as-is or with levers moved, after ops walks them through it) — passing their
>   worldview as `--theme` to rank the closest fits; offer building one as a peer with
>   its cost class, never a downsell. You talk and RANK; a hidden engine

Why: Description rewritten fork-first: it is the routing surface every turn. The new description is in the frontmatter.

>   strategy (that's senpi-strategy-ops), or building/designing one from scratch or
>   with a custom DSL (that's senpi-strategy-author).

Why: Description rewritten fork-first: it is the routing surface every turn. The new description is in the frontmatter.

> - **It's their strategy.** A template is a quick start to the user's own strategy, never "our strategy"

Why: Routing reworked fork-first: strategy asks start in `senpi-strategy-author`, which offers the closest template as the base the user forks and makes their own.

>   Building one from scratch is a peer route, not a downsell: state the four cost classes as facts —
>   template as-is ≈ the cheapest thing the agent does · a lever fork (values only) adds a little · a
>   bespoke edit of a template (a new universe, a different signal — author's edit path) adds more ·
>   scratch ≈ 2–3× a template — and let them choose. The same four rungs appear in ops, author and the
>   workspace guardrail; the menu must read identically everywhere.

Why: Cost stays a true fact, said when asked, never a reason to pick a template.

>   bait-and-switch and it buries the thing we actually want them doing — forking a template with their

Why: Routing reworked fork-first: strategy asks start in `senpi-strategy-author`, which offers the closest template as the base the user forks and makes their own.

>   they're after — a user who says they don't know gets *Start here* (Special paths). Candidates below

Why: Reworded in place for fork-first routing; the new wording is in SKILL.md.

>   not told supplies their own number and reads normal silence as a fault — measured 2026-10-12,
>   `shadow` had **0 fills across 10 installs with all 10 wallets withdrawn**, and `moose` runs about
>   **one trade a week with a 24-day gap**. Give the number AND what makes it go quiet: *"about one a

Why: The example of a strategy that goes quiet named one that could not fire at all. The rule stays, without the example.

> - **Always offer build-custom as a peer; never dead-end, never downsell.** Say its cost class beside it.

Why: Cost stays a true fact, said when asked, never a reason to pick a template.

>       picks a window.** For the **hands-off** route, managed copy templates come in **two flavors — surface
>       both, don't show only one**: *copy specific traders* (**Shadow / Remora / Raptor / Cuckoo / Oxpecker /
>       Jackal** — mirror a trader's fresh entries or book) **and** *follow the smart money by signal* (**Athena / Phalanx
>       / Starling / Whalehunter / Pilotfish** — position by where the whole proven cohort leans, many wallets at once, not
>       1:1; Athena is Phalanx with its Aegis hedge as one fund). All auto-apply DSL + budget-relative sizing. (`senpi-trade` carries the full flavor breakdown.)

Why: A hardcoded template pitch. A template is now offered only as a fit for the user's own idea (senpi-strategy-author, via the discover ranking, once), never from a fixed list.

>    | copy | **Don't make them pick a window** — "proven vs hot" is exactly what the `senpi-trader-research` blend unions for them (that's the whole point of the blend). Ask only: *"a specific wallet you already have in mind, or should I find the best to copy?"* — a named wallet → `senpi-trader-research --trader <addr>` (vet, then mirror); "find the best" → hand to `senpi-trader-research` (blended shortlist, ranked by copyability); prefer hands-off → a managed copy template — **surface both flavors**: *copy specific traders* (Shadow / Remora / Raptor / Cuckoo / Oxpecker / Jackal) **and** *follow the smart money by signal* (Stingray / Starling / Whalehunter / Phalanx — many traders at once, not 1:1). |

Why: A hardcoded template pitch. A template is now offered only as a fit for the user's own idea (senpi-strategy-author, via the discover ranking, once), never from a fixed list.

> - **Build custom** — hand to **senpi-strategy-author** at any time; a peer route with its cost class
>   stated (a bespoke edit of a template adds more than a lever fork; scratch ≈ 2–3× a template), never a
>   dead-end and never a downsell.

Why: Cost stays a true fact, said when asked, never a reason to pick a template.

> "You've got ~${user_context.budget} free — start from {top} (I'll walk you through how it's set and the levers before we fund it; it deploys as {user}'s {top} — or a name of your own), add a hedge alongside it, or build your own?"

Why: Reworded in place for fork-first routing; the new wording is in SKILL.md.

> - **"I don't know what to try" / "what should I start with?" / no thesis at all** → **start here, do not
>   run the ranker cold.** With no worldview to rank against, a shortlist beats a filtered list: the engine
>   scores keyword overlap against what the user says, and someone who has said nothing gets noise. Offer
>   these, one per line, answerable with a digit:

Why: Routing reworked fork-first: strategy asks start in `senpi-strategy-author`, which offers the closest template as the base the user forks and makes their own.

>   > **Don't know what to try? Start here.**

Why: Reworded in place for fork-first routing; the new wording is in SKILL.md.

>   > 1. **Penguin** — crypto only. The Hyperfeed striker: it reacts only to the strongest live rotations
>   >    on the feed — a name suddenly rocketing up what winning traders hold — then commits one position
>   >    at up to 10x, 90% margin, with a DSL floor that ratchets up to lock gains as it runs. High risk,
>   >    high reward, with -15% SL.
>   > 2. **Pelican** — the same striker across all assets: crypto plus stocks, commodities, indices

Why: A hardcoded template pitch. A template is now offered only as a fit for the user's own idea (senpi-strategy-author, via the discover ranking, once), never from a fixed list.

>   > 3. **Signals Hunter** — the smart-money signal feed traded on a clock, spread across several slots.
>   >    Slower and less concentrated than 1 or 2.
>   > 4. **Athena** — a smart-money hedge fund, forked under your name.
>   > 5. Or tell me **how you want to trade** and I'll rank the whole catalog against it.

Why: A hardcoded template pitch. A template is now offered only as a fit for the user's own idea (senpi-strategy-author, via the discover ranking, once), never from a fixed list.

>   Lead with 1 and 2: a live rotation shows up fast, so the reader sees what the machinery actually does
>   early, and both are built to be forked and modified once they have a feel for it. **Unpack the numbers
>   if they ask, and never leave them to be misread.** **-15% SL is 15% ROE, not a 15% price move** — at 10x
>   that is a **1.5%** move, so answer in price when asked what the stop means. On 90% margin it costs
>   **~13.5% of the wallet per stop-out**, and say **per stop-out**: their risk guard rails are off, so stops
>   compound (three ≈ 40% of the wallet). **Up to 10x**, never a flat 10x — the per-name venue cap clamps
>   many instruments below it, and that clamp moves the PRICE behind every number, not the wallet cost
>   (ROE is return on margin: ~13.5% at any leverage, while the move doubles at 5x). Say   **rotations**, never "pumps": the detector fires on a jump in what winning traders HOLD, not on price, so a pumping
>   name no smart money rotated into does not fire. Never promise a runner, a win rate or a timeframe —
>   the pitch is what the machinery does, never what it will return.

Why: Unpacking of the numbers in the removed named pitch; it left with the pitch.

