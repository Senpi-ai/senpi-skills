# Crane — Managed Pairs / Stat-Arb (BLOCKED on a runtime capability)

**Status: engine complete + unit-tested; NOT deployable.** Crane needs coordinated
multi-leg close, which the current Runtime 3.0 scanner contract does not expose.
This doc records the design, the exact gap, and how to wire it when the capability
lands. The scanner ships **disarmed** (`inputs.armed: false`) so it can never open a
pair it cannot manage.

## What it does
Market-neutral. For a correlated pair `(A, B)` it tracks the log price spread
`ln(A/B)`, z-scores it over a rolling window, and trades the **spread**:
- `|z| ≥ entryZ` → short the rich leg, long the cheap leg (equal notional).
- `|z| ≤ exitZ` → revert: close both legs.
- `|z| ≥ stopZ` → blowout: close both legs.
- The two legs are **one position** — they open together and close together.

The alpha is in `main/scanners/scoring.py` (pure, `test_engine.py` green): spread,
z-score (guards a full window + non-zero dispersion), the pair state machine, entry
directions, z-scaled sizing.

## The safety core — why DSL-only cannot do this
A pair with only one leg left is a **naked directional bet**. If the two legs exit
independently (which is what per-position DSL does), the first leg to hit its stop
leaves the other fully directional until something notices. That is the exact
naked-position failure mode the fleet guards against everywhere.

`decide_pair_action` encodes the rule: **exactly one leg held ⇒ `CLOSE_NAKED`,
unconditionally, before any other branch.** Crane is only safe if it can flatten
the survivor immediately. It cannot, today.

## The gap (verified against the runtime references)
1. **Scan signals are open-only.** `scan-contract.md`: the returned signal dict is
   `{asset, direction, marginPct, leverage, data}` — there is no close/reduce
   intent. A scanner cannot express "close this position."
2. **Scan cannot mutate.** `close_position` / `strategy_close_positions` /
   `strategy_close` raise `PermissionError` inside `scan`.
3. **`CLOSE_POSITION` exists but is unreachable from a scanner.** It is a valid
   `action_type` in `runtime-yaml.md`, but only `OPEN_POSITION` execution params
   are documented, **no fleet strategy uses it**, and the scanner-driven-close
   approach was tried and scrapped previously (per fleet notes). There is no
   documented contract for how a scanner tells a `CLOSE_POSITION` action *what* to
   close.
4. **DSL is per-position.** It trails/stops each leg independently — it has no
   concept of "these two legs are one unit; if one dies, kill the other."

Net: the runtime has no primitive for a **joint multi-leg position** or a
**scanner-driven coordinated close**. Every deployable fleet strategy is
open-on-scanner + exit-on-DSL, which structurally cannot express a managed pair.

## What would unblock it (the ask for the runtime team)
Any **one** of these is sufficient:
- **A close-signal contract** for the scan return value — e.g. a signal with
  `intent: "close"` (or a `reduce_only`/`close: true` flag) that a `CLOSE_POSITION`
  action consumes, with the scanner naming the asset(s) to flatten. Crane's
  `scoring.decide_pair_action` already emits `CLOSE_BOTH` / `CLOSE_NAKED`; it just
  needs an execution path.
- **A joint/basket position type** the runtime opens and exits atomically (both
  legs fill or neither; either leg's stop closes both).
- **A "linked positions" DSL mode** — tag two positions as a group; the exit engine
  closes the group when any member exits or on a group-level spread rule.

The first (a scanner close-signal + working `CLOSE_POSITION`) is the smallest change
and also unlocks other tier-3 strategies (rebalancers, follow-them-out copiers).

## Wiring when it lands
1. Flip `inputs.armed: true`.
2. Route `scan`'s `CLOSE_BOTH` / `CLOSE_NAKED` decisions to the close path (emit
   close-intent signals for the named legs instead of just logging them).
3. Add the `CLOSE_POSITION` action to `runtime.yaml` subscribed to `crane_scanner`
   (the reference wiring is stubbed there, commented).
4. Keep the single-scanner design — the pair's rolling spread state lives in one
   `ctx.state`; splitting open/close across two scanners would fracture it.

## Connection to the Strategy-Spec proposal
This is concrete evidence for the coverage-gap point on Duncan's Path-B plan: his
four Phase-2 stateful primitives (snapshot-diff, rolling-window, derived-universe,
adaptive-threshold) all assume **independent single-asset emits**. A managed pair
needs a **fifth** capability — joint multi-leg positions / coordinated close — that
is missing from both the spec *and* the current runtime. Market-neutral is the
highest-value category that neither covers yet.
