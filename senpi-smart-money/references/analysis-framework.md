# Analysis framework — reading where smart money is moving

The engine hands you two cohorts' net positioning + the divergences + the near-term flow. This is how
you turn that into the read a human couldn't assemble by eyeballing a few whale wallets. **The signal
is in positioning *relative to the crowd*, and in conviction — never in a single wallet.**

## 1. Cohorts are defined by realized PnL — and that's the point

- **Smart money = ≥ $1M lifetime realized gains.** Realized, not total (total includes unrealized,
  which isn't proof of anything — a paper gain isn't a closed win). This is the only honest "who's
  actually good" filter.
- **The crowd = $10k–$100k realized.** Good enough to have made money, but the followers.

When you cite the smart cohort, you're citing wallets that have *extracted* millions from this exact
market. That's why their positioning is information.

## 2. Bias + members, always together

`bias = net_notional / gross_notional`, in [−1, +1]:
- **+1** = every dollar the cohort holds in that coin is long. **−1** = every dollar short. **0** =
  evenly split / no conviction.

But bias alone lies. **Always pair it with `members`:**
- −0.9 across **40** wallets = a strong, broad conviction. Lead with it.
- −0.3 across **6** wallets = weak and thin. Mention it, don't headline it.

A high bias on few members is noise; a moderate bias on many members is a real lean. The engine only
surfaces coins with ≥ `MIN_MEMBERS` for exactly this reason.

## 3. Divergence is the core signal

The highest-value read is **where the proven cohort and the crowd are on opposite sides of the same
coin.** The engine flags two cases:

- **`opposite_sides: true`** — smart is net long, crowd net short (or vice versa). The cleanest
  signal. The winners and the followers literally disagree.
- **Large `gap`** — same side but very different conviction (smart −0.9, crowd −0.2 → the winners are
  far more committed).

Read it as: *"the wallets that have been right are positioned against the wallets that chase."* That's
the whalehunter thesis, and it's the line the user remembers. Lead with the opposite-sides cases,
ranked by how lopsided they are.

## 4. All-time positioning vs. near-term flow — say which is which

Two different clocks, and conflating them is the most common mistake:

- **Cohorts (`smart_leaning`, `divergences`)** = *all-time* proven positioning. Slow-moving, high
  conviction, the structural read.
- **Near-term (`near_term`)** = the *last-4h* Leaderboard/Hyperfeed momentum. Fast, noisy, the live
  flow.

The interesting reads are at the seam:
- **They agree** → the proven money and the hot money are both leaning the same way = high conviction.
- **They conflict** → the proven cohort is positioned *against* what's hot right now. Either the
  winners are early/contrarian (often the better read), or the near-term move is running away from
  them. Name the tension; don't average it away.

In `near_term.momentum_events`, **scale-in** events in the smart cohort's direction = the move is
*building*; **exits** = it's *fading*. "Smart is short HYPE and the 4h flow shows fresh short
scale-ins" is a much stronger statement than the static bias alone.

## 5. Smart money is often early — frame it as positioning, not timing

"The winners are short" and "it reverses tomorrow" are different claims. Proven wallets are
frequently right on *direction* and wrong on *timing* — and some of a big short is hedging long spot,
not a directional call. Surface the positioning honestly and let the user (and the risk engine, if
they act on it) handle timing. Never present a cohort lean as a guaranteed reversal.

## 6. Compose the read

Lead with where smart money is leaning, make the divergence the centerpiece, check whether the
near-term flow confirms it, and give the trigger that would resolve the divergence. Example:

> "The proven cohort (≥$1M realized, 30 wallets) is heavily short HYPE — bias −0.8. The $10–100k
> crowd (120 wallets) is long it, +0.6. They're on opposite sides, and the 4h flow shows the smart
> cohort still adding shorts (fresh scale-ins), not covering. **What to watch:** if the crowd
> capitulates and flips short, the divergence is resolving — that's often where the move accelerates."

That's the difference between "whales are short" and a read worth acting on.
