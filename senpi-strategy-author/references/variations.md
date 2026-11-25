# Variations to simulate: picking them, wording them, reading them

The test plan is how a strategy earns real money: the strategy as designed plus a few variations, run as
simulations on virtual money, then the best one taken live. Offer it unprompted once a design exists (built
here, or a template the user picked). Skip it when they asked to go live, or to take a simulation live. It
is a recommendation, never a gate: they can take it live as it is at any point.

## How many

The strategy as designed plus two to four variations. More only when they ask ("test 10 versions"). Each
simulation uses credits every hour it runs and room on their agent's box, so a small set that answers real
questions beats a sweep.

## What to vary: one thing per variation

Vary what the design left open: a choice you made for them in the interview, a default they did not pick, the
number you were least sure of. One change per variation, so a result can be traced to its cause.

| Area | Examples | What it tests |
|---|---|---|
| Scoring | the entry score a signal needs | fewer, stronger entries against more, weaker ones |
| Scanner thresholds | a lookback, the move or volume a signal needs, a trend filter on or off | what counts as a setup |
| Entry management | cooldown, max entries a day, slots, size per position (margin %), leverage | how hard it presses a setup |
| Exit management | the DSL: a tighter or looser stop, a faster or slower profit lock (a preset from `dsl-presets.yaml`, one field changed) | how long it lets a trade run |

- **Keys come from the package, never memory.** A setting is a value its `runtime.yaml` already has, keyed
  `strategy.<key>`, `scanner.inputs.<name>`, `entry.<param>`, `exit.<path>` or `risk.<path>` (ops
  **Running variations**). A change to code (a scanner's logic, a new rule) is a fork, not a setting.
- **Far enough apart to show.** Leverage 2 against 2.1 tells nothing in a few weeks; 2 against 4 does. Stay
  inside the strategy's own limits and each asset's maximum leverage.
- **Time cuts follow the horizon** ([`time-cuts-by-horizon.md`](time-cuts-by-horizon.md)): a variation never
  adds a timed close to a strategy meant to hold.
- **Fees count.** A variation that trades more pays more fees, and simulations charge them: it has to beat
  them, not just win more often.

## Say each one in plain words

Name it by what it changes and what it tests, never by its key: *"Pickier entries: score 7 instead of 5, so
fewer trades but stronger ones."* Then the list's facts, in one message: how often the strategy should trade
(so how long until a comparison means anything), the virtual budget each runs with ($1,000 unless they name
one), the credits an hour from ops' dry run, and one question: simulate these first (recommended), or take it
live as it is now?

Example, for "crypto is entering a decade-long bull market" (BTC and ETH, long): the strategy as designed buys
pullbacks in an uptrend and gives its exits room; then *deeper pullbacks only* (a scanner threshold), *a
looser trailing stop* (exit management), *half the leverage* (entry management).

## Reading the results

- **Time first.** A strategy that trades about once a week needs weeks of running before a ranking means
  anything. Say when it is worth looking; never set a timer to look for them.
- **Rank as senpi-strategy-ops ranks simulations** (its return is already after fees), with the trade count
  and the drawdown beside it: a variation with two trades has proved little.
- **Recommend, then take it live on their yes.** Ops takes that simulation live as it ran
  (`--from-simulation`), with a real-money budget asked then. A simulated result is a record of a simulation,
  never money made and never a promise of live returns.
