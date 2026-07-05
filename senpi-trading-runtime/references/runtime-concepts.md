# Runtime Concepts — How the Senpi Trading Runtime Works

This document explains the conceptual behavior of the trading runtime: how the runtime runs your
scanner, what guarantees it gives you, what the `position_tracker` scanner and `POSITION_TRACKER`
action do at runtime, and how the DSL exit engine makes exit decisions.

For the `runtime.yaml` field schema see [runtime-yaml.md](runtime-yaml.md). For the author-side
`scan(inputs, ctx)` contract see [scan-contract.md](scan-contract.md). For the operator commands see
[runtime-cli.md](runtime-cli.md).

---

## The runtime pipeline (big picture)

A strategy runs from a `runtime.yaml` plus a Python module. The runtime owns the loop; your code only
reads data and returns signals. Two paths run concurrently:

```
ENTRY:  external_scanner  →  OPEN_POSITION action  →  runtime sizes + executes
        scan(inputs, ctx)        (rule | llm)            (FEE_OPTIMIZED_LIMIT)
        every interval_seconds

EXIT:   position_tracker  →  POSITION_TRACKER action  →  DSL monitor
            (observe)               (react)              (manage exits)
```

- **Entry path:** the runtime runs and supervises your `external_scanner`, calling
  `scan(inputs, ctx)` every `interval_seconds`. You *return* candidate signals; the `OPEN_POSITION`
  action gates them (rule or LLM), and the **runtime** sizes and executes the trade. Your code never
  opens a position itself.
- **Exit path:** the built-in `position_tracker` scanner observes the wallet on-chain, the
  `POSITION_TRACKER` action turns changes into lifecycle events, and the DSL monitor manages
  trailing-stop exits off those events.

---

## What the runtime owns (lifecycle & guarantees)

These are handled for you — you do not write any of it:

1. **Run + supervise.** On boot and hot-install the runtime mounts each `external_scanner`, spawns the
   Python scaffold child, and calls `scan(inputs, ctx)` every `interval_seconds`, time-boxed by
   `timeout_seconds`. A crashed child is restarted with a fresh scanner id (a zombie fence refuses a
   dying child's stray writes).
2. **Deliver + validate.** The scaffold validates each signal's `data{}` against the runtime.yaml's
   `signal_data_schema`, then hands accepted signals to the runtime.
3. **Durable dedup.** Acceptance is a per-`(scanner, asset)` high-water mark plus per-`signal_id`
   dedup, **persisted before** the trade dispatches — so a gateway restart never double-trades or
   re-accepts an old signal.
4. **Transactional state.** `ctx.state` advances only on a clean tick; an exception, timeout, or
   persist failure rolls back the in-memory mutation (state never advances on a failed tick).
5. **Crash-safe reconcile.** On restart the runtime converges its position view with the exchange
   **before** trading — adopts orphan positions, resizes drift, archives gone — behind a fail-safe
   gate (no trading until the first successful reconcile; safe if MCP is unreachable at boot).

---

## Scanner: `external_scanner` (your scanner)

The `external_scanner` is the Python module the runtime runs for you. Every `interval_seconds` the
runtime calls `scan(inputs, ctx)`; the function reads market/account data through the read-only
`ctx.senpi_mcp`, optionally consults `ctx.state`, and **returns a `list[dict]` of candidate signals**.
It is single-pass and synchronous — no loop, no scheduling, no execution. The full author contract
(the `ctx` surface, the signal shape, `scoring.py`) is in [scan-contract.md](scan-contract.md).

## Scanner: `position_tracker`

The `position_tracker` is a **built-in scanner that polls your wallet on Hyperliquid and detects
position changes** since the last scan. It runs on its configured `interval_seconds` (e.g. `10`;
built-in scanners are floored at 7s).

On each tick it compares the current position snapshot to the previous one and emits one signal per
detected change:

| Delta / signal type | Meaning |
|---|---|
| `POSITION_OPENED` | A new position appeared in the wallet |
| `POSITION_CLOSED` | A position vanished from the wallet |
| `POSITION_FLIPPED` | Direction reversed (LONG → SHORT or vice versa) |
| `POSITION_INCREASED` | Size grew in the same direction |
| `POSITION_DECREASED` | Size shrank in the same direction |

Each signal carries both the previous and current snapshots (asset, direction, size, leverage, entry
price, ROE, liquidation price). The scanner makes no trading decisions — it only reports what changed.

**Why it's required for DSL:** the DSL monitor needs to know exactly when a position opens (to start
tracking) and closes (to clean up). Without this scanner the DSL never learns about new positions.

---

## Actions: how signals become trades and exits

An action consumes signals from one or more scanners and reacts. `decision_mode` is `rule`, `llm`, or
`none`.

- **`OPEN_POSITION`** consumes the signals your `external_scanner` returns and opens a position via
  `FEE_OPTIMIZED_LIMIT`. In `rule` mode it opens on every accepted signal; in `llm` mode a decision
  prompt gates it.
- **`POSITION_TRACKER`** consumes `position_tracker` signals and fires the lifecycle hook events the
  DSL monitor and notifications listen to.

| `position_tracker` signal | Hook event fired | Payload |
|---|---|---|
| `POSITION_OPENED` | `ON_POSITION_OPENED` | asset, direction, size, leverage, entry price |
| `POSITION_CLOSED` | `ON_POSITION_CLOSED` | previous snapshot |
| `POSITION_FLIPPED` | `ON_POSITION_FLIPPED` | old + new snapshots, both directions |
| `POSITION_INCREASED` | `ON_POSITION_INCREASED` | both snapshots + sizeDelta |
| `POSITION_DECREASED` | `ON_POSITION_DECREASED` | both snapshots + sizeDelta |

**Why this wiring is mandatory:** the DSL monitor listens for `ON_POSITION_OPENED` to start tracking
a new position. The runtime validates at startup that if `exit.dsl_preset` is set there is at least
one `position_tracker` scanner **and** a `POSITION_TRACKER` action referencing it.

---

## DSL Exit Engine

> The DSL exit engine is **unchanged** in this version. The runtime repo's DSL docs
> (`dsl-reference.md` / `dsl-flow-and-configuration.md`) are the source of truth; this is the
> conceptual summary.

The DSL (Dynamic Stop-Loss) engine is an **autonomous, rule-based trailing stop-loss system**. It
starts tracking a position when `ON_POSITION_OPENED` fires, then evaluates exit conditions every
`interval_seconds` (integer, 5–3600). When a condition is met it closes the position and records the
close reason. No LLM is involved — every decision is deterministic from the configured parameters.

### How a tick works

Every `interval_seconds`, for each tracked position, the DSL engine:

1. Fetches the current mark price.
2. Updates the *high-water mark* if price improved (LONG: higher; SHORT: lower).
3. Recomputes the floor price based on the current phase.
4. Checks exit conditions (phase floor breach → time-based cuts).
5. If a condition fires: closes the position and records the reason.
6. Otherwise: checks whether a new tier is triggered and advances phase if needed.

### Phase 1 — Initial Defense

**Active from:** position open until the first profit tier is triggered.

Phase 1 maintains two floors and uses the stricter one (LONG: the higher price; SHORT: the lower):

- **Absolute loss floor** — from `max_loss_pct`. The position can never lose more than this ROE% of
  margin. The runtime places an **exchange stop-loss at this absolute floor**; a fast wick that skips
  the polling interval is closed on-exchange with reason `exchange_sl_hit`.
- **Trailing retrace floor** — from `retrace_threshold` (ROE%). Tracks the high-water mark and sets
  the floor `retrace_threshold` ROE% below it; ratchets up as the position gains.

**Runtime breach counting:** each tick where price is at/through the effective (stricter) floor
increments a breach counter; at `consecutive_breaches_required` the runtime closes with reason
`dsl_breach`. Any tick that recovers above the floor resets the counter. `consecutive_breaches_required`
filters momentary wicks (1 = exit on first touch; 3 = three consecutive ticks).

### Phase 2 — Profit Lock

**Active from:** when the first tier's `trigger_pct` is crossed (tier index 0).

Each tier defines a `lock_hw_pct`. The floor is:

```
floor_roe   = high_water_roe × (lock_hw_pct / 100)
floor_price = entry_price + floor_roe converted to price
```

The floor **only ratchets up**. In Phase 2 all exits are **exchange-driven**: the runtime places and
continuously updates a stop-loss order at the current floor; when price hits it the exchange executes
and the position closes with reason `exchange_sl_hit`. There is **no breach counting and no retrace
knob in Phase 2** — tier `retrace`/`breaches` and `phase2.retrace_threshold` /
`consecutive_breaches_required` are rejected at load. The runtime's only job here is keeping the SL
order updated as the floor ratchets.

**Example:**

```
Entry $100, 10× LONG, Tier 1: trigger_pct=10, lock_hw_pct=40
ROE +10% → Tier 1 → Phase 2 begins. hw_roe=10% → floor_roe=4% → SL at $100.40
Climbs → hw_roe=18% → floor_roe=7.2% → SL updated to $100.72
Tier 2 (trigger 20, lock 70) → hw_roe=22% → floor_roe=15.4% → SL at $101.54
Price falls to $101.54 → exchange SL executes → close, reason exchange_sl_hit
```

### `retrace_threshold` (Phase 1 only)

`retrace_threshold` is in **ROE percent**, converted to a price distance using leverage:

```
price_retrace = retrace_threshold / 100 / leverage
```

`retrace_threshold: 3` on a 10× LONG = floor 0.3% below the high-water price; at 20× the same `3` =
0.15%. High-leverage positions need smaller values to avoid premature exits from normal volatility.

---

### Time-based cuts

Configured at the **preset root** (siblings of `phase1`/`phase2`), each runs every tick alongside the
phase logic, after Phase 1 floor-breach counting. Their phase behavior:

- **`hard_timeout`, `weak_peak_cut`, and `dead_weight_cut` are all evaluated in any phase** while the
  position is open (`weak_peak_cut`'s guard usually only holds in Phase 1 — see below).

#### `hard_timeout` — any phase

> "Close a position that has been open for at least N minutes."

A preset-level time cut (a `dsl_preset` root key, sibling of `phase1`/`phase2` — **not** nested under
`phase1`). It fires on wall-clock minutes since open **regardless of phase**: once the interval has
elapsed the position is closed whether it is still in Phase 1 or has already advanced into Phase 2.

**Field:** `interval_in_minutes` — wall-clock minutes since open. Must be > 0 (clamped to ≥ the cron
interval).

#### `weak_peak_cut`

> "If the position made a little profit but never reached `min_value` ROE, and has since faded — close it."

After `interval_in_minutes`, closes when `peakROE < min_value` **and** `currentROE < peakROE`. It is
evaluated in any phase, but once a tier triggers (Phase 2) peak ROE has by definition reached the tier
threshold, so when `min_value` sits below the first tier's `trigger_pct` the guard can no longer hold
and it is effectively a Phase 1 cut.

**Fields:** `interval_in_minutes` (> 0); `min_value` — the ROE% the position must have reached to
count as "real profit" (> 0, required when enabled).

#### `dead_weight_cut`

> "If the position has been underwater past N minutes, close it."

`deadWeightCutStartedAt` is set at open and **reset to now on every tick where `currentROE > 0`**. The
cut fires when elapsed since that timer ≥ `interval_in_minutes` **and** `currentROE ≤ 0` on the
current tick — i.e. the position has been continuously non-positive for the interval. Evaluated in any
phase.

**Field:** `interval_in_minutes` — duration of continuous non-positive ROE before exit. Must be > 0.

| | `weak_peak_cut` | `dead_weight_cut` |
|---|---|---|
| Trigger | Profitable but peak < `min_value`, now fading | At/below zero ROE |
| Timer reset | — (peak-based) | On any tick with `currentROE > 0` |
| Scenario | "Worked a little, then faded" | "Went negative and stayed there" |

---

### DSL close reasons

| Reason | Cause |
|---|---|
| `dsl_breach` | Phase 1 floor breached for `consecutive_breaches_required` consecutive ticks |
| `exchange_sl_hit` | Exchange stop-loss filled (Phase 2 floor, or the Phase 1 absolute floor) |
| `hard_timeout` | Open past `hard_timeout.interval_in_minutes` (any phase) |
| `weak_peak_cut` | Peak ROE stayed below `min_value` and price retraced from it |
| `dead_weight_cut` | ROE stayed non-positive past `dead_weight_cut.interval_in_minutes` |
| `flipped` | Position direction reversed (detected by `position_tracker`) |
| `manual_close` | Closed by user or a close-position action |
| `closed_externally` | Closed outside the runtime (e.g. exchange UI); detected by reconciliation |

---

## Scanner → Action → DSL: full flow example

```
[Hyperliquid]
   ↓ (position_tracker every 10s)
position_tracker detects: SOL LONG opened, entry $150, size 10, 10×
   → emits POSITION_OPENED
POSITION_TRACKER action → fires ON_POSITION_OPENED { SOL, LONG, 150, 10× }
DSL monitor → creates state for SOL; absolute floor at max_loss_pct
   ↓ (DSL tick every interval_seconds)
Tick: price $152 → ROE +1.33% → hw $152, retrace floor below → no breach, no tier
Tick: price $165 → ROE +10% → Tier 1 (trigger 10) → ENTER PHASE 2
Tick: price $180 → ROE +20% → Tier 3 → SL ratchets to tier floor
Tick: price $155 → exchange SL at the locked floor executes → CLOSE, reason exchange_sl_hit
   ↓
Telegram notification (if dsl_lifecycle enabled)
```

---

## Field quick reference (trading terms)

| Field | Plain meaning |
|---|---|
| `retrace_threshold` | Phase 1 ROE% pullback from peak that defines the trailing floor. Divide by leverage for price%. |
| `consecutive_breaches_required` | Consecutive ticks below the Phase 1 floor before exit. |
| `max_loss_pct` | Hard absolute floor — never lose more than this ROE% from entry. Positive number. |
| `trigger_pct` (tier) | ROE% that activates this tier and enters Phase 2. Strictly ascending across tiers. |
| `lock_hw_pct` (tier) | % of peak high-water ROE to protect as the Phase 2 trailing floor. Higher = tighter. |
| `hard_timeout` | Max wall-clock minutes open before giving up on the position. Fires in any phase. |
| `weak_peak_cut` | Exits faded positions whose peak never reached `min_value` ROE. |
| `dead_weight_cut` | Exits positions held continuously non-positive past the interval. |
| `interval_seconds` (exit) | How often the DSL evaluates open positions. Integer, 5–3600. |
