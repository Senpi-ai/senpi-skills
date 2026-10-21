# Exit records — why and how a position closed, what is locked now

Guardrail 6b in `SKILL.md` is the rule: **read the record, never infer a mechanism**, label the source, and
treat `CLOSED_OFF_LADDER` as UNKNOWN. This file is the field-level detail for those reads. Read it before
explaining why or how a position closed, what is locked in now, or a `senpi explain` lifecycle.

A live review once
answered *"Exit mechanism: undetermined"* because one command timed out, while the record sat one call away.

## A CLOSED position — `senpi dsl closes --runtime <id> --json`

"Archived closes with reason and ROE". Each row is the answer *and* the details:

| field | what it tells the user |
| --- | --- |
| `closeReason` | **why it closed** (vocabulary below) |
| `phase` + `currentTierIndex` | **which rung was governing** at the close |
| `currentROE` | the ROE it closed at — already at the REAL leverage, so quote it |

…plus `entryPrice` / `lastPrice` / `elapsedMinutes` / `closedAt`.

### `closeReason` vocabulary

Translate `closeReason`, never paste the enum:
- **`exchange_sl_hit`** — the resting exchange stop fired. It fills at **market**, so expect a burst of taker
  fills at one timestamp and some overshoot past the floor. The most common DSL exit by far.
- **`dsl_breach`** — the DSL's own floor broke and the runtime closed it via `closePosition`, honouring
  `exit.order_type`.
- **`hard_timeout`** — the clock, regardless of PnL. OFF on penguin / pelican.
- **`weak_peak_cut`** — never reached `min_value` ROE inside the window. A **death** cut, not a profit cut.
- **`dead_weight_cut`** — stagnation cut.

**The three above are TIMERS, not reactions to price, and none is phase-scoped.** State the configured
interval and the actual hold (the worked line is in `SKILL.md` 6b).
Saying it "detected the position rolling over" or "cut early before the hard stop" invents a mechanism —
a real review did exactly that on a position whose hold time (5.03h vs a 300m timer) was the giveaway.
`weak_peak_cut`'s timer also RESETS whenever ROE clears `min_value`, so it bounds DEAD positions, not
slow ones.

## A LIVE position — `senpi dsl inspect <asset> --json`

Returns the full `DslState`, which already holds every number users ask for. Quote these rather than
computing anything:
- `floorPrice` / `tierFloorPrice` — where the stop actually is
- **`lockedProfitPct`** — what is locked in *right now* (the answer to "at what point does it lock profits")
- `highWaterRoe` / `peakROE` — the best it has seen
- `currentTierIndex` + `phase` — which rung is governing
- `distanceToNextTierPct` — how far to the next rung
- `slOrderId` / `lastSyncedFloorPrice` — the resting exchange order the floor is synced to

`DslState.leverage` is the leverage the position actually opened at (hard rule 0b).

## When the record can't be read

**If the gateway is down**, `senpi dsl closes` exits non-zero and prints *"start it with: openclaw gateway
run"*. **Say that.** An unreadable record is an unreadable record — do not fall back to guessing a mechanism
from fill shapes and present it as fact.

## `openclaw senpi explain <ASSET> --runtime <id> --json` — the lifecycle

It returns `{ok, asset, entries[]}` — the asset's events oldest-first (`position.opened` → `dsl.created` →
`dsl.tier_advanced` → `dsl.closed`/`position.closed`), threaded by `senpi.position.id`. You need the
**runtime id**, which is the key the event log is addressed by. Get it from the runtime registry
(`installed_runtimes.json`, the same source the engine reads for mandates — each entry's `id`) or from
`openclaw senpi runtime list`. A closed strategy has no ring → `explain` returns nothing; that's expected.

The event shapes behind it: `references/event-log.md`.
