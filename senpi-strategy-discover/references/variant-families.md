# Variant families — helping a user choose between near-identical strategies

Fifteen listed strategies are **variants of another listed strategy**, differing in one or two
numbers. Ranked on keyword overlap they look interchangeable, and six of them are penguins. A user
who picks the wrong one does not get a worse strategy — they get a strategy whose results they will
read as if it were the parent's.

This file is how to tell them apart, and how to say so in one line each.

## What the engine hands you

`discover.py` folds every family into one card before the shortlist cap: the parent's card lists its
eligible siblings' ids in `variants` (and carries the family's best theme score), so a variant reaches
the user only through that list and the rule below. If the parent was filtered out, the closest eligible
member stands in and its `varies` names the parent.

## The rule

**Never offer two members of the same family in the same shortlist** — with TWO exceptions:

- **the chase caps** below, where 1.5% is now a shipped DEFAULT and the user should be told what
  it does and offered the looser settings;
- **the concentration axis** (next section), where a family holding both a spread and a
  concentrated member means the user is choosing how many bets their money is split across, and
  that choice is theirs rather than ours to make silently.

**The rule, restated:** Offer the PARENT, and name the
variant only when the user's own words ask for the thing it varies. "I want fewer, bigger positions"
earns the duo. "I keep getting in late" earns a chase arm. Nothing in a generic request earns any of
them.

If a user asks for a variant by name, give it to them — but say what it varies from, in the same
breath, every time. A variant without its parent named is unreadable.

## The concentration axis — offer it whenever the family has one

**Standing instruction (Jason, 2026-10-12): where a strategy has a concentrated sibling — or is
itself the concentrated one — offer BOTH, in one line, before the budget question.** Not a
recommendation, a choice. Concentration is the one variant axis a user has a real opinion about
before they have any results, because it is about how much a single wrong call costs them.

### How to tell, without a list that goes stale

Read it off the family's own catalog records. Every record carries **`max_slots`** and
**`leverage_max`**, both DERIVED from the packages themselves, so they cannot drift from what
ships:

> Within a family (follow `varies` to the root), the member with **fewer slots and/or higher
> `leverage_max`** is the concentrated one. The member with **more slots and/or lower
> `leverage_max`** is the spread one.

Both axes matter and they are different claims. Fewer slots at the same leverage moves
*concentration only* — the same money behind fewer names. Higher leverage moves *size* — more
gross notional for the same margin. A variant that moves both (`hornet-concentrated`) is doing two things at once, and you must
say so.

**Do NOT key off tags.** They are unreliable and in places simply wrong: `penguin-x5` is tagged
`concentrated` and `high-leverage` while being penguin at HALF the leverage, and `puffin-duo`,
`penguins-duo` and `pelicans-duo` are tagged `concentrated` while doing the opposite — splitting
one position into two smaller ones. The config fields are the truth; the tags are not.

### What to say

Two lines, each naming the one number and its consequence. Quote **what one stop costs as a
fraction of the account** — that is the number a user can actually feel, and it is
`max_loss_pct x marginPct / 100`:

> **Hornet** — up to 3 positions at 18% margin, 4x. One stop-out costs about **1.4%** of the account.
> **Hornet Concentrated** — 2 positions at 45%, 10x, with every exit at the same distance in price.
> One stop-out costs about **9%**, and one good run pays correspondingly more.

Then stop and let them answer. If they say nothing, **ship the spread member** — the parent is the
default for a reason, and nobody should land in the most aggressive package in a family by silence.

### What not to do

- Do not offer the concentration choice for a family that has no concentrated member. Most
  families vary something else entirely (a chase cap, a score floor), and inventing the axis
  implies an option that does not exist.
- Do not present it as "safe vs risky". Gross exposure is often similar or identical; what moves
  is how much rides on one call. Say that.
- Do not let it turn into a menu. One line each, two members, then the budget question.

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

### 5. Concentration — fewer, larger bets inside a multi-arm strategy

`athena-concentrated` — athena with its phalanx arm holding **2 positions at 22.5%** instead of 3 at
15%. The same 45% of that sleeve is at risk either way; it is carried by fewer names. The aegis
hedge and the 65/35 split are untouched.

### 6. Concentration at leverage — fewer bets, and bigger ones

Unlike section 5, this moves *two* things on purpose: concentration AND size. Say so.

`hornet-concentrated` — hornet's semiconductor supply-chain momentum as **2 positions at 45% and
10x** instead of 3 at 18% and 4x. Same breadth gate, same 16-name basket, same scanner bytes; every
DSL distance re-derived so the 2.00% stop and the 5.00/7.50/12.50/18.75/25.00% rungs are unchanged
IN PRICE, with one extra rung at 2.50% that 4x could not afford to lock. Two slots rather than
three because at 45% per open a third has no room — measured on a live instance over 3 days and 7
opens, peak concurrent was 2 and never 3.

One stop-out costs **9.0% of the account** against hornet's 1.44%, which is why its daily loss
limit is 18% and its halt 30%: hornet's 15% daily limit would stop the book after a single loss.
It also drops hornet's 48h `hard_timeout`, so nothing but the ratchet and the 2.00%-of-price stop
closes a position — including through the Friday-to-Sunday equity gap that timeout existed for.
Say that last part out loud to anyone running it over a weekend.


## Not a family any more: camel

`camel-concentrated` **took the `camel` id on 2026-10-08** — one position per side at 10x is now
what "camel" means, and the 4-slot/5x original is delisted as `camel-spread` (on disk so the forks
deployed from it stay readable, not offered). There is one camel and nothing to choose between.

Two things to say when someone funds it, because the name is doing less work than it looks:

- **It is the most aggressive thing in the catalog.** One stop-out costs about **8.6% of that side**
  against ~1% on the old 4-slot config, and **no clock closes anything** — the ladder and the
  1.20%-of-price stop are the only exits.
- **The 10x is what it asks for, not always what it gets.** The venue clamps to
  `min(requested, the name's cap)` and **53.6% of listed perps cap at 5x or below, 35.8% at 3x**.
  Because every DSL rung is an ROE number, a clamp widens every rung *and* the stop in price by
  `10 ÷ cap` — on a 3x name the whole ladder runs 3.3× wider than authored.

Anyone still running the old config is on a fork of `camel-spread`'s recipe; read their results
against that, not against today's camel.

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
