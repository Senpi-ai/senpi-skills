# Variant families — helping a user choose between near-identical strategies

Fifteen listed strategies are **variants of another listed strategy**, differing in one or two
numbers. Ranked on keyword overlap they look interchangeable, and six of them are penguins. A user
who picks the wrong one does not get a worse strategy — they get a strategy whose results they will
read as if it were the parent's.

This file is how to tell them apart, and how to say so in one line each.

## The rule

**Never offer two members of the same family in the same shortlist** — with one exception, the
chase caps below, where 1.5% is now a shipped DEFAULT and the user should be told what it does
and offered the looser settings.

**The rule, restated:** Offer the PARENT, and name the
variant only when the user's own words ask for the thing it varies. "I want fewer, bigger positions"
earns the duo. "I keep getting in late" earns a chase arm. Nothing in a generic request earns any of
them.

If a user asks for a variant by name, give it to them — but say what it varies from, in the same
breath, every time. A variant without its parent named is unreadable.

## The families

### 1. Chase caps — how late an entry may be

**The default is now 1.5%, and this family is the exception to the rule above: ASK.**

As of 2026-10-07, `penguin`, `penguins-duo`, `pelican`, `pelicans-duo`, `purple-penguin` and
`penguin-x5` all ship `maxPreMovePct: 1.5` — the strictest setting. A user who just says "run
penguin" gets the 1.5% gate.

Because that is now a default rather than a user choice, **say what it does and offer the looser
settings, in one line, before the budget question** — do not ship it silently:

> It skips any entry where the price has already moved **1.5%** in the signal's own direction over
> the last hour — about one entry in five. There are 2% and 3% versions if you want it looser.

Then let them answer. If they say nothing, 1.5% is what they get.

The alternatives are separate packages: `penguin-chase-{200,300}bp` ·
`penguins-duo-chase-{200,300}bp`. (`*-chase-150bp` is now identical to its parent — offer the
parent, not the arm.)

Every arm rejects an entry once price has already moved **in the signal's own direction** over the
last hour by more than the cap: **1.5%**, **2.0%** or **3.0%**. Direction-aware — buying after a
fall, or selling after a rally, still passes. It gates *lateness*, not volatility.

Nothing else differs from `penguin` / `penguins-duo`. Same detector, same sizing, same exits.

| cap | rejects | what to say |
|---|---:|---|
| **1.5% (default)** | 18.2% of entries | "strictest — skips about one entry in five for arriving late" |
| 2.0% | 9.1% | "middle" |
| 3.0% | 6.8% | "loosest — closest to taking every signal" |

Measured over 7 days to 2026-10-06 by replaying the real selection rule (the top-scoring passing
candidate per scan, which is what the strategy enters), n=44 entries. p50 +0.73%, p90 +1.89%.

**Tell the user this before they pick:** **2.0 and 3.0 differ by about one trade in 44**, so a user
running those two against each other should expect weeks, not days, before anything separates. The
jump from 1.5 to 3.0 is the only one that shows up quickly.

### 2. Duos — one big position or two half-sized ones

`penguins-duo` (2 × 45% vs penguin's 1 × 90%) · `pelicans-duo` (2 × 40%) · `puffin-duo` (2 × 45% vs
puffin's 1 × 75%)

Same signal, same entries, same exits. The capital is split in two.

What actually changes for the user: **one wrong call costs about half as much, and one right call
pays about half as much.** On puffin-duo a stop-out is 11.2% of the account instead of 18.8%. Gross
exposure is unchanged — this moves concentration, not leverage.

Offer a duo when someone says a single position feels too all-or-nothing. Offer the parent when they
say they want maximum conviction behind the best idea.

One honest caveat for `puffin-duo`: puffin sizes *up* on its highest-scoring signals (75% → 90%).
The duo cannot — two slots and that tier do not fit under the same margin cap — so both of its
positions are always 45%. A user who valued the conviction tier should stay on puffin.

### 3. Leverage sibling — the same ladder at half the leverage

`penguin-x5` — penguin at 5x instead of 10x, with every exit re-derived so the stop and every profit
rung sit at the **same distance in price**. It is not "penguin with less risk at the same exits"; it
is the same exits reached with half the leverage, so a given price move produces half the ROE.

### 4. Conviction forks — a higher bar, a bigger position

`wild-cheetah`, `wild-condor`, `grizzly-wild` — each asks for MORE conviction than its parent
(a higher score floor) and sizes larger when it gets it. `purple-penguin` is the mirror: penguin at
score **8** instead of **9**, asking whether the floor is set too high.

These bet on the parent's own ranking being informative. If it is, they beat it; if the ranking is
noise, they are the parent with more variance. Say exactly that.

### 5. Concentration at leverage — the carry trade as one bet per side

`camel-concentrated` — camel's funding carry as **1 position per side at 10x** instead of 4 at 5x.
Margin deployed is unchanged (4 × 18% = 1 × 72%), but **leverage doubles gross notional**, so unlike
every other variant here this one moves *two* things on purpose: concentration and size. Say so.

The pitch is as much about the bill as the bet: camel pays **58% of its gross profit in fees**, and
one position costs roughly a quarter of the fees four do for the same money at work.

Offer it only to someone who has said they want leverage *and* concentration. It is the most
aggressive thing in the catalog: one stop-out costs **8.6% of the arm**, against 1.08% on camel,
and **no clock closes anything** — the ladder and the 1.20%-of-price stop are the only exits, so
a carry that never reverts holds the arm until the stop. Funding does keep accruing while it waits.

### 5. Concentration — fewer, larger bets inside a multi-arm strategy

`athena-concentrated` — athena with its phalanx arm holding **2 positions at 22.5%** instead of 3 at
15%. The same 45% of that sleeve is at risk either way; it is carried by fewer names. The aegis
hedge and the 65/35 split are untouched.

## How to present a family when it IS relevant

Name the parent, the one number, and the consequence. One line each, no table:

> **Penguin** — one position, up to 10x, 90% margin.
> **Penguins Duo** — the same thing across two positions at 45% each. Halves what one wrong call
> costs, and what one right call pays.

Then stop. Do not list all six chase arms to anyone; name the one that matches what they said.

## Reading results

Every one of these is **only interpretable against its parent over the same window.** A variant's
P&L on its own says nothing — the parent could have done better or worse on the same days, and the
whole reason the variant exists is that nobody knows which. When a user asks "how is my chase-150
doing", the useful answer compares it to penguin over the same period, not to zero.

Variants are also deliberately narrow: most differ in a setting that fires on a minority of entries,
so **small samples will show nothing**, and "no difference yet" is the expected early result rather
than a finding.
