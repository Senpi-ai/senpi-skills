# How often will it trade? — the question every user asks and no card answers

A user funding a strategy has two expectations in their head: how much it might make, and
**how often something will happen**. The cards answer the first and say nothing about the
second, so the user supplies their own number — and when reality is ten times slower, they
conclude the thing is broken and pull the money out.

This is not hypothetical. Measured on 2026-10-12:

| strategy | what the user would have assumed | what actually happened |
|---|---|---|
| `shadow` | a copy-trader — constant activity | **0 fills, ever**, across 10 installs; all 10 wallets withdrawn to $0 |
| `condor` | "One Amazing Trade per Day" — its own name | 69 closes across 6 users, **44% of realized PnL paid in fees**, 5 of 6 net negative |
| `moose` | a crossover system — a few a week | **13 entries in 120 days** across 3 coins, including a 24-day gap |

Every one of those is a frequency mismatch, not a returns mismatch. Shadow's users did not
leave because it lost money — it never traded at all.

## The rule

**State the expected trade frequency, and what would make it go quiet, before the budget
question.** One line. It costs nothing and it is the single thing most likely to stop a user
reading normal silence as a fault.

> **Moose** — about one trade a week across its three coins, and it went 24 days without one
> in the last four months. It only acts when a 20/50 EMA cross happens *and* the daily trend
> agrees, so a quiet month means no crossover has happened yet, not that it is stuck.

## Where the number comes from — three tiers, say which one you used

**1. Measured (best).** Replay the real gate over real candles, or read what the fleet
actually did. Never estimate when you can count. Two ways:

- *Replay*: walk closed candles one bar at a time, calling the package's own `scoring`
  functions with only the data visible at that bar, and count the bars where it would have
  fired. This is how moose's "13 in 120 days" was produced, and it is the method in
  `feedback_measure_before_calling_a_scanner_starved`.
- *Live*: count fills on the wallets already running it. This is how shadow's "0 fills ever"
  and condor's "69 closes, 44% fees" were produced.

**2. Ceiling (always available).** The config imposes hard caps even when you cannot measure
the pass rate. These are honest upper bounds, and they are often far below what a user
imagines:

| field | what it bounds |
|---|---|
| `risk.guard_rails.max_entries_per_day` | entries per day, full stop |
| `strategy.slots` / `maxSlots` | positions that can be open at once |
| `per_asset_cooldown_seconds` | how soon the same coin can be re-entered |
| `cooldown_seconds` + `max_consecutive_losses` | how long a losing run freezes it |
| scanner `interval_seconds` | how often it can even look |

Say the ceiling as a ceiling: *"at most one entry a day, and only one position at a time"* —
never as an expectation.

**3. Shape (always available).** Count the conditions that must hold AT ONCE, and the window
they must hold in. This is the strongest predictor of rarity and it needs no data:

- **Conjunction depth.** Each additional ANDed gate multiplies the rarity. A strategy needing
  trend + momentum + a cohort + a freshness window is not "selective", it is *rare*, and
  should be described that way.
- **The freshness window.** `maxEntryAgeSeconds: 240` or `maxBarsSinceSignal: 2` is the real
  constraint far more often than the signal itself. Quote it in human units: "the trade has to
  be taken within four minutes of the trader opening it."
- **Universe size.** Three coins give three chances per bar; a top-50 universe gives fifty.

## What to say when it goes quiet

Silence is the normal state of a selective strategy, and the user must be told which silence
is which. Give them the distinction up front:

- **Expected silence** — no setup has qualified. The scanner is ticking and reporting it.
- **Broken silence** — the scanner is erroring, its cohort is empty, or its gate cannot pass.

If the package logs a per-tick line (the good ones do — `[squid.scan] WAITING — no emit | …`),
say so: *"it writes a line every two minutes saying what it is waiting for, so we can always
tell the difference."* A user who knows that does not need to guess.

## What not to do

- **Do not read a frequency off the name or the tagline.** "One Amazing Trade per Day" is a
  name. condor's users averaged nowhere near one a day, and nothing in the package enforced it.
- **Do not promise a floor you have not measured.** A ceiling is a fact; a floor is a forecast.
- **Do not quote a backtest-shaped number you did not produce.** If you did not replay it or
  count it, say which tier you are on: *"the config caps it at one a day; I have not measured
  how often it actually fires."*
- **Do not let "selective" stand in for a number.** Every strategy claims to be selective. The
  user is asking how many trades, so answer in trades.
