# Explaining the exit — the preview you show before you build

The exit is the part users misread most, and the misreading costs them money. The classic shape:
someone sets `lock 10% at +100%` believing it means *take 10% profit at +100%*. It actually means
*once you are up 100%, put the stop at 10% of your best*. A trade that peaks at +93% never reaches
that only rung, so nothing is ever locked and the entire gain round-trips to the stop. The config is
valid, the strategy reports healthy, and the user finds out afterwards.

Say what the exit does, in their words, before you build it.

## The one sentence, every time

> Your Dynamic Stop Loss (DSL) moves your stop loss up as the price moves in your favor (up for long,
> down for short). As the trade gains it follows behind, locking more of the gain in — so if the price
> dips you keep most of what you made instead of giving it back. It never sells while the trade is
> still going your way.

The parenthetical is doing real work on a short book. The stop always climbs in **ROE** terms, but in
**price** terms a short's floor sits above entry and falls as the trade wins — `roeToPriceFloor`
branches on direction. Keep it; a short-seller told "your stop moves up as the price increases" will
read it as backwards and lose confidence in the whole preview.

**It is a stop-loss that climbs, not profit-taking.** Nothing is sold on the way up. No rung ever
closes a winner. A rung only raises the price at which a *reversal* closes you. If the user says
"take profit at X" anywhere in the conversation, correct the framing before you set a number.

## The arithmetic (from the engine, not from memory)

`src/dsl/engine/floors.ts` in senpi-trading-runtime:

- `tierIndexFromPrice` — the active rung is the **highest** tier whose `trigger_pct` the ROE has
  reached. Below the first trigger there is no rung at all.
- `computeTierFloor` — `floorRoe = highWaterRoe × lock_hw_pct / 100`.

Two consequences worth saying out loud, because both surprise people:

1. The floor tracks the **high-water** ROE — the best the trade ever reached — not where it is now.
2. Below the first `trigger_pct` nothing is locked; the only floor is `phase1.max_loss_pct`
   (`phase1.enabled: false` fleet-wide, so the trailing floor does not apply).

## The template

Lead with where the trade is now — the downside floor — then climb. Rungs, then how it
behaves, then the preset's own time cuts.

> **Here's the DSL settings on this strategy**
> • Below +20% → nothing is locked in yet; your only floor is the **−8% max loss**.
> • Up 20% → your stop moves to **+5%**. From here the trade can't lose money.
> • Up 50% → your stop moves to **+30%**.
> • Up 100% → your stop moves to **+85%**.
>
> The stop follows the **best** price the trade ever hit, not where it is now — peak at +50%, drift
> back to +35%, and that +30% stop is still the one holding. It only moves up, never down, and
> nothing is sold while the trade is still climbing.

That worked example is `let_winners_run`, which carries **no time cuts**. Add a line for the ones the
chosen preset actually has — read them out of `dsl-presets.yaml`, do not recall them:

| preset | time cuts to mention |
|---|---|
| `let_winners_run` | none |
| `balanced` | flat-and-fading for 6h → out · 72h → out |
| `mean_reversion` | flat-and-fading for 6h → out · hard timeout |
| `scalp` | dead weight after 8h → out · hard timeout |
| `parabolic_runner` | hard timeout only (14d) |

## When the ladder doesn't fit — offer, don't just warn

Every one of these ends in a concrete alternative the user can say yes to.

**First rung above ~40% ROE.** Most trades never get there, so the position runs its whole life with
nothing locked in. No shipped preset trips this — it catches hand-rolled ladders.
> "Your first lock is at +100%. A trade that peaks at +93% locks nothing and can ride all the way
> back down to your stop. `balanced` starts locking at +10%. Want that instead?"

**`lock_hw_pct: 0`.** Exits flat and still pays both sides of the fee, so a "scratch" is a loss.
Never ship one; there is no reading of the user's intent that this serves.

**Locks that shrink as triggers rise** (`50` then `30`). The ladder loosens as the trade wins, which
is backwards. Almost always a typo for the reverse order — read it back and ask.

**Preset against the thesis.** A fader on `let_winners_run`, a multi-day trend on `scalp`.
> "You described fading spikes, and those resolve in hours — `mean_reversion` banks at +5% instead of
> waiting for +20%."

If the user keeps their choice after you have explained it, build what they asked for. The rule is
that they choose knowingly, not that they choose what you would.
