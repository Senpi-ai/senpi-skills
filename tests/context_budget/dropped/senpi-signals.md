# senpi-signals 2.12.0 — build-first routing: dropped and reworded sentences

Strategy conversations now default to building the user's own strategy (`senpi-strategy-author`).
A template is opt-in: offered only when the user asks for something pre-built, wants to browse, or
names one, and never named unprompted. Each quote below is base text that left SKILL.md, with why.

> - **Penguin (crypto) and Pelican (all assets)** trade this exact detector: offer them by name when a
>   user wants it traded, with the cost in the same breath.

Why: A template pitched by name, unprompted. No skill names a template the user did not ask about; the build route (`senpi-strategy-author`) replaces it.

> > stop and the liquidation price before anything is placed. Or run it as a strategy:
> > **(1) Signals Hunter** — trades the smart-money signals;
> > **(2) Puffin** — same smart money engine, one position at a time, higher risk/reward (one stop-out
> > ≈ 19-23% of the account);
> > **(3) Athena** — hedge fund built on these smart-money signals, forked under your name;
> > **(4) Trade the Hyperfeed momentum** — e.g. **Penguin** (crypto) or **Pelican** (all assets), which
> > chase live Hyperfeed rotations.

Why: A template pitched by name, unprompted. No skill names a template the user did not ask about; the build route (`senpi-strategy-author`) replaces it.

> **This is a major entry point.** Ask it every time, in full, with all four routes. Offer **all four**
> strategy routes, every time.

Why: Reworded in place for build-first routing; the new wording is in SKILL.md.

> Routes (1)-(3) trade the reads above. Route (4) deliberately does **not**, which is why its label
> names **the Hyperfeed** rather than these signals. The label is the disclosure: Hyperfeed momentum is
> a different source from the smart-money cohort above, so it is offered AS a different signal, never as
> another way to trade what they just read.

Why: Reworded in place for build-first routing; the new wording is in SKILL.md.

> - **(1) Signals Hunter → senpi-strategy-ops.** `signals-hunter` uses the same detectors, scores and
>   wording the backend is built from, on an hourly clock. It runs its own scan, so its reads are close
>   to this feed but not always identical, and its score floor is higher, so it acts on fewer reads than

Why: A template pitched by name, unprompted. No skill names a template the user did not ask about; the build route (`senpi-strategy-author`) replaces it.

> - **(2) Puffin → senpi-strategy-ops.** `puffin` is the same engine on one position: score floor 85,
>   75-90% of the wallet at up to 10x, a 15-minute clock. Name the cost with the upside: no second
>   position to average against the first, one stop-out costs about **19-23% of the account**, and a
>   round trip pays roughly **0.8-0.9% of the wallet in fees**. Offer it when the user asks for size or
>   "the aggressive one" — never as the default, and never for a first strategy. `signals-hunter` stays
>   the default answer for this feed.
> - **(3) Fork Athena → senpi-strategy-ops.** `athena`: its Phalanx sleeve follows the proven cohort,
>   its Aegis sleeve hedges the regime. `athena-x` is the same at conviction size (25% at 5x rather than
>   15% at 3x) — only when the user asks for size, with the trade-off named.
> - **(4) Trade the Hyperfeed momentum → senpi-strategy-ops, or senpi-strategy-discover.** Name the
>   signal the template trades in the same sentence as the template. `penguin` chases a coin jumping 15+
>   places up the top-50 rows of the 4h leaderboard, one position at up to 10x — Orca's detector rather
>   than this feed's — and runs with its risk guard rails off. For anything else, hand off to

Why: A template pitched by name, unprompted. No skill names a template the user did not ask about; the build route (`senpi-strategy-author`) replaces it.

> - **Roll your own → senpi-strategy-author.** Not in the offer, but the answer as soon as a user wants
>   something none of the four do. The read is the brief (asset, side, numbers). Guardrails: a stop on

Why: Reworded in place for build-first routing; the new wording is in SKILL.md.

> - **Ops runs the walkthrough, then asks the budget.** Read the minimum budget from the catalog, never

Why: Reworded in place for build-first routing; the new wording is in SKILL.md.

> - **When the user picks one read**, start from the template built on that kind of read:

Why: Template-first routing flipped: building the user's own strategy (`senpi-strategy-author`) is the default; a template only when they ask.

>   | Any, traded as a feed | `signals-hunter` |
>   | Any, one position at a time | `puffin` (state the per-stop and fee cost) |
>   | A whale open | `signals-hunter` (it acts on whale *moves*, not opens) |
>   | Smart money vs the crowd | `athena`, or `phalanx` (the cohort sleeve alone) |
>   | A funding extreme | `pangolin` (fades the crowd paying) or `camel` (collects the carry) |

Why: Reworded in place for build-first routing; the new wording is in SKILL.md.

>   | A laggard behind BTC | `mantis` |

Why: Reworded in place for build-first routing; the new wording is in SKILL.md.

