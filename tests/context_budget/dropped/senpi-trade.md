# senpi-trade 1.11.0 — build-first routing: dropped and reworded sentences

Strategy conversations now default to building the user's own strategy (`senpi-strategy-author`).
A template is opt-in: offered only when the user asks for something pre-built, wants to browse, or
names one, and never named unprompted. Each quote below is base text that left SKILL.md, with why.

>   runtime-only, so route to a managed mirror template for real two-sided protection. Any question

Why: Description rewritten build-first: it is the routing surface every turn. The new description is in the frontmatter.

>   senpi-strategy-author (custom runtime) or a template via senpi-strategy-discover,
>   including the named mirror templates (Remora, Shadow, Oxpecker, Raptor, Cuckoo) that
>   size to the user and auto-trail DSL on every fill (Shadow / Jackal also enter fresh-only). Pairs with senpi-trader-research, which finds and vets the trader

Why: Description rewritten build-first: it is the routing surface every turn. The new description is in the frontmatter.

> > a **managed mirror template** has it built in and auto-applies it on every fill. **Don't** tell a user a

Why: Template-first routing flipped: building the user's own strategy (`senpi-strategy-author`) is the default; a template only when they ask.

> > either. When they want a true two-sided ratcheting stop → managed template.

Why: Template-first routing flipped: building the user's own strategy (`senpi-strategy-author`) is the default; a template only when they ask.

>   you babysitting, **budget-relative sizing** (and, with **Shadow / Jackal**, **fresh-entry-only** — no chasing a runner). A raw mirror
>   + per-position DSL means *you* wrap each new fill by hand; a template does all of it automatically.
> - they want to **mirror more than one trader at once, by signal** — a template can position by where the

Why: A template pitched by name, unprompted. No skill names a template the user did not ask about; the build route (`senpi-strategy-author`) replaces it.

>   mirror is strictly **1:1**; multi-trader, signal-driven copying is only possible with a runtime template.

Why: Template-first routing flipped: building the user's own strategy (`senpi-strategy-author`) is the default; a template only when they ask.

> | To **copy / mirror a trader, managed** | **senpi-strategy-discover** → a **named mirror template** | budget-relative sizing, auto-DSL; Shadow/Jackal add fresh-entry-only |

Why: A template pitched by name, unprompted. No skill names a template the user did not ask about; the build route (`senpi-strategy-author`) replaces it.

> **The named copy templates — surface them by name for the copy intent.**
> *Direct-mirror* (copy specific traders' books): **Remora** (whale-cohort, or name your whales) · **Shadow**
> (multi-trader fresh-entry, name 2–3) · **Oxpecker** (elite conviction — their single biggest concentrated
> bet) · **Raptor** (hot-streak — traders winning right now) · **Cuckoo** (copy-the-copiers — consensus of

Why: A template pitched by name, unprompted. No skill names a template the user did not ask about; the build route (`senpi-strategy-author`) replaces it.

> *Smart-money by signal* (position by where the whole cohort leans — **many traders at once, not 1:1**):
> **Stingray** (ranks the entire smart-money board and rotates long/short by net conviction) · **Starling**
> (buys when a flock of top wallets pile into the same name at once) · **Whalehunter** (with the smart cohort,
> against the crowd) · **Phalanx** (follows the proven cohort's headcount flow — enters only while conviction is
> growing, boosted when the cohort diverges from the 4h crowd, wide stops that let winners run) · **Signals
> Hunter** (runs the Senpi Signals detector library on an hourly clock over the whole book — whale opens and
> adds, funding dislocation, OI surges and cohort positioning scored together, taking what clears the score
> floor, long or short). These are the answer for *"follow the smart money"* rather than one specific trader.

Why: A template pitched by name, unprompted. No skill names a template the user did not ask about; the build route (`senpi-strategy-author`) replaces it.

> Offer the managed option **once**, then respect a "no." For mirror, the templates fix the exact pains
> a raw mirror causes (tiny size, stale entries) — offer one before you reach for a raw `strategy_create`.

Why: Template-first routing flipped: building the user's own strategy (`senpi-strategy-author`) is the default; a template only when they ask.

>   When the book has already moved, **lead with a fresh-entry template — Shadow (or Jackal), the ones that wait
>   for the trader's *next* open instead of copying the old book** — or find a trader entering now. Still show
>   the full template menu (more choice is better); just label it honestly: Remora / Raptor / Oxpecker / Cuckoo
>   mirror the cohort's *current* positions, so they're a valid copy style but **not** the fresh-entry fix for an
>   already-run book — don't sell them as such.

Why: A template pitched by name, unprompted. No skill names a template the user did not ask about; the build route (`senpi-strategy-author`) replaces it.

> the goal is to land them on **raw mirror / a named template / custom** — offer as peers, recommend the fit.

Why: Template-first routing flipped: building the user's own strategy (`senpi-strategy-author`) is the default; a template only when they ask.

>    - *"use most of my funds / a few big orders / not 5% sitting idle"* → **a budget-relative template
>      (Shadow / Remora)** — they size to *your* capital and open a few full-size positions, which a

Why: A template pitched by name, unprompted. No skill names a template the user did not ask about; the build route (`senpi-strategy-author`) replaces it.

>    fill → **template**.
> 3. **What shape of copy?** → one specific trader (**raw**) · a whale cohort (**Remora**) · 2–3 named
>    traders, fresh entries only (**Shadow**) · one elite's single biggest conviction bet (**Oxpecker**) ·
>    traders hot right now (**Raptor**) · consensus of the top copy strategies (**Cuckoo**) · a rule the
>    templates don't cover (**→ senpi-strategy-author**).

Why: A template pitched by name, unprompted. No skill names a template the user did not ask about; the build route (`senpi-strategy-author`) replaces it.

>    closer-sized trader, a higher multiplier, or a budget-relative template. The pre-fund sim confirms it.

Why: Template-first routing flipped: building the user's own strategy (`senpi-strategy-author`) is the default; a template only when they ask.

> **Route the answer:** template → hand to **senpi-strategy-discover** by name · custom → **senpi-strategy-author**

Why: Template-first routing flipped: building the user's own strategy (`senpi-strategy-author`) is the default; a template only when they ask.

> reality** (small budget vs big trader = dust; the multiplier is locked; concentrated-use → a template),

Why: Template-first routing flipped: building the user's own strategy (`senpi-strategy-author`) is the default; a template only when they ask.

> (d) a fresh-entry template (Shadow). This one check prevents the core failure: funding capital that then

Why: A template pitched by name, unprompted. No skill names a template the user did not ask about; the build route (`senpi-strategy-author`) replaces it.

> a **runtime** → a managed template (**Shadow / Remora**) that auto-applies it on **every** fill. Don't

Why: A template pitched by name, unprompted. No skill names a template the user did not ask about; the build route (`senpi-strategy-author`) replaces it.

> Whenever you end a mirror or template flow with options ("set it up / simulate first / compare / point a
> template at a specific whale"), render them as a short **numbered or bulleted list, one option per line** —

Why: Template-first routing flipped: building the user's own strategy (`senpi-strategy-author`) is the default; a template only when they ask.

> | Call `ratchet_stop_add` on a raw position "DSL protection", or say it "can't be protected" | It's **profit-lock only** (no downside floor — Phase-1 is dropped); integrated two-phase DSL is runtime-only | Offer profit-lock **+ a static SL** on a raw position; steer to a **managed template** for real two-sided DSL |

Why: Template-first routing flipped: building the user's own strategy (`senpi-strategy-author`) is the default; a template only when they ask.

> | Recommend a "top" trader without checking their book is mirrorable | The best track record is often the worst mirror *today* — the winners already ran, so the mirror opens **nothing** | Read current-position **distance-from-entry** first; if it's run, steer to a fresh-entry template |

Why: Template-first routing flipped: building the user's own strategy (`senpi-strategy-author`) is the default; a template only when they ask.

>   `senpi-strategy-author` (custom) or a template via `senpi-strategy-discover` (mirror: Remora / Shadow /
>   Oxpecker / Raptor / Cuckoo). This skill executes the **direct** trade and can add **per-position** protection.
> - **Mirror with *custom rules* the templates don't cover** — a position-count cap ("max 5; if he opens

Why: A template pitched by name, unprompted. No skill names a template the user did not ask about; the build route (`senpi-strategy-author`) replaces it.

>   "capture his adds", or a conditional exit → a **custom mirror runtime** via `senpi-strategy-author`; the
>   named templates are the fast path for common shapes, author covers bespoke rules. Note the

Why: Template-first routing flipped: building the user's own strategy (`senpi-strategy-author`) is the default; a template only when they ask.

> - You're about to call `ratchet_stop_add` on a raw position "DSL protection" — it's **profit-lock only** (no downside floor without a runtime); offer a static SL and/or a managed template for real two-sided DSL.

Why: Template-first routing flipped: building the user's own strategy (`senpi-strategy-author`) is the default; a template only when they ask.

