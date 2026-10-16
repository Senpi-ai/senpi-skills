# Time cuts belong to the horizon, not to the preset

A time cut closes a position because the **clock** ran out, not because the thesis died. That
makes it right for a strategy that intends to be out within hours and wrong for one that
intends to hold for days — and the DSL has no idea which you are building. You have to say.

**The rule.** A strategy meant to hold for a long period carries no `weak_peak_cut` and no
timed close. Only genuinely short-term sniping carries them. Measured on the live fleet
(15 Oct 2026): `weak_peak_cut` closes are 27% in profit at an average −0.50% of price — it is
cutting positions that went nowhere, which is what it is for. `hard_timeout` closes are **72%
in profit at +1.08%** — that one is cutting winners, and it was enabled on 76 catalog packages.

## Pick the cuts from the horizon you declared

| `time_horizon` | intends to be out in | `weak_peak_cut` | timed closes |
|---|---|---|---|
| `scalp` | minutes to an hour | **yes** — it is the strategy | short; the cut defines the hold |
| `intraday` | within the day | yes, ≥ ~60m | ≥ ~60m |
| `swing` | days | **no** | nothing under 48h |
| `position` | weeks | **no** | nothing under 5d |

`strategies/tests/test_time_cuts_match_horizon.py` enforces exactly this, so a strategy that
gets it wrong fails the same test the templates do. A cut at or beyond the floor for its
horizon is fine — see *Slot recovery* below for why the floor is not "never".

**The default preset is `balanced`, and it is not horizon-neutral**: it ships a 360m
`weak_peak_cut` and a 4320m `hard_timeout`. Take it for a multi-day strategy and you have
given a swing thesis a six-hour clock without deciding to. Of the shipped presets,
`let_winners_run` and `parabolic_runner` carry no weak-peak cut (correct for long holds),
`scalp` carries a 45m `dead_weight_cut` and a 90m `hard_timeout` (correct for a sniper), and
`balanced` / `mean_reversion` sit in between. **Choose the preset from the horizon, then check
its cuts against the table — do not inherit the default.**

## The `min_value` trap

`weak_peak_cut` fires when `peakROE < min_value`. So where you put `min_value` relative to the
**first tier's `trigger_pct`** decides what the cut can reach:

- `min_value` **below** the first tier → the cut can only ever touch a position that never
  armed a floor. Bounded: it cannot close a locked winner. This is the only safe setting.
- `min_value` **at or above** the first tier → the cut can close a position that already
  locked profit. That is a clock cutting a winner, which is the `hard_timeout` failure above.

Every shipped preset has `min_value` below its first tier. Keep it that way, and if you raise
`min_value`, raise the first tier with it.

## Slot recovery is the one legitimate use on a long book

A clock is indefensible as a thesis exit and defensible as **slot recovery**. If a strategy
ships no `CLOSE_POSITION` scanner, the ladder and the stop are its only other exits, so a
position that neither locks nor stops holds its slot indefinitely — and on a one-slot
strategy that ends the strategy. `dog` is the worked example: its `hard_timeout` was widened
from 360m to 2880m rather than removed, because 360m was inside its 393m p90 hold (a thesis
exit) while 2880m is ~7× p90 (it cannot reach a live trade).

So on a `swing` or `position` strategy, either:

- ship a signal-invalidation close scanner and no clock at all — the better design, because it
  exits for being **wrong** rather than for being slow; or
- keep one timed close far outside the hold distribution as a backstop, and say in the comment
  that it is slot recovery, not a thesis exit.

Never both a short clock and a close scanner: the clock will win first and you will never learn
whether the scanner worked.

## Sizing the backstop

Use the strategy's own hold distribution, not a round number. If you have live positions, take
p90 of the hold time and set the backstop at several multiples of it. If you do not yet, the
per-horizon floors in the table are the conservative stand-in. A backstop you can reach in
normal trading is not a backstop.
