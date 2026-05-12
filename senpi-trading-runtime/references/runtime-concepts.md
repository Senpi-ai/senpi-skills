# Runtime Concepts — How the Senpi Trading Runtime Works

This document explains the conceptual behavior of every major component in a runtime YAML: what the position tracker scanner and action do at runtime, how the DSL exit engine makes exit decisions, and what each DSL field controls in trading terms.

For field-level schema details see [yaml-schema.md](yaml-schema.md). For the full DSL configuration reference see [dsl-configuration.md](dsl-configuration.md).

---

## Top-Level Fields

These fields sit at the root of every runtime YAML and identify the runtime to the system.

| Field | Description |
|---|---|
| `name` | Unique identifier for this runtime. Used by the plugin to reference and manage the running instance. Should be a short, descriptive slug. |
| `version` | Runtime schema version. Informational. |
| `description` | Human-readable summary of the runtime's strategy and tuning philosophy. Informational only — not used by the runtime engine. |

### `strategy`

The core configuration block that defines the trading context:

| Field | Description |
|---|---|
| `wallet` | The on-chain wallet address holding positions and executing trades. Use `${WALLET_ADDRESS}` so the wallet is supplied from the environment at link time. This is the actual trading wallet the runtime monitors and acts on — it must already exist (wallet creation happens outside the runtime via Senpi MCP). |
| `enabled` | Boolean flag to activate or pause the runtime. When `false`, the runtime loads the config but takes no action. |

Other `strategy` fields (`slots`, `margin_per_slot`, `margin_pct`, `trading_risk`, `default_leverage`) shape sizing and risk — see [yaml-schema.md](yaml-schema.md#define-your-strategy) for the full field table.

---

## The Big Picture

The runtime operates as a three-layer pipeline:

```
position_tracker scanner  →  POSITION_TRACKER action  →  DSL Monitor
      (observe)                     (react)                (manage exits)
```

- The **`position_tracker` scanner** watches the wallet on-chain and detects position changes.
- The **`POSITION_TRACKER` action** translates those changes into lifecycle events that the DSL monitor reacts to.
- The **DSL monitor** listens for those events and autonomously manages trailing stop-loss exits.

---

## Scanner: `position_tracker`

The `position_tracker` scanner is a **periodic job that polls your wallet on Hyperliquid and detects position changes** since the last scan. It runs on its configured `interval` (e.g., `10s`).

On each tick it compares the current position snapshot to the previous one and emits one signal per detected change:

| Delta / Signal type | Meaning |
|---|---|
| `POSITION_OPENED` | A new position appeared in the wallet |
| `POSITION_CLOSED` | A position vanished from the wallet |
| `POSITION_FLIPPED` | Direction reversed (LONG → SHORT or vice versa) |
| `POSITION_INCREASED` | Size grew in the same direction |
| `POSITION_DECREASED` | Size shrank in the same direction |

Each signal carries both the previous and current position snapshots (asset, direction, size, leverage, entry price, ROE, liquidation price) as metadata.

The scanner does not make any trading decisions — it only reports what changed.

**Why it's required for DSL:** The DSL monitor needs to know exactly when a position opens (to start tracking it) and closes (to clean up state). Without this scanner, the DSL never learns about new positions.

---

## Action: `POSITION_TRACKER`

The `POSITION_TRACKER` action **consumes signals from the `position_tracker` scanner and fires the corresponding lifecycle hook events** that the DSL monitor (and notifications) listen to.

| Scanner signal | Hook event fired | Payload |
|---|---|---|
| `POSITION_OPENED` | `ON_POSITION_OPENED` | asset, direction, size, leverage, entry price |
| `POSITION_CLOSED` | `ON_POSITION_CLOSED` | previous snapshot |
| `POSITION_FLIPPED` | `ON_POSITION_FLIPPED` | old + new snapshots, both directions |
| `POSITION_INCREASED` | `ON_POSITION_INCREASED` | both snapshots + sizeDelta |
| `POSITION_DECREASED` | `ON_POSITION_DECREASED` | both snapshots + sizeDelta |

**Why this wiring is mandatory:** The DSL monitor listens for `ON_POSITION_OPENED` to start tracking a new position. Without this action firing that event, DSL never activates for any position. This is why the runtime validates at startup that:
1. There is at least one `position_tracker` scanner.
2. There is at least one `POSITION_TRACKER` action referencing it.

---

## DSL Exit Engine

The DSL (Dynamic Stop-Loss) engine is an **autonomous, rule-based trailing stop-loss system**. It starts tracking a position when `ON_POSITION_OPENED` fires, then evaluates exit conditions every `interval_seconds`. When a condition is met it closes the position and records the close reason.

There is no LLM involved — every decision is deterministic from the configured parameters.

---

### How a tick works

Every `interval_seconds`, for each tracked position, the DSL engine:

1. Fetches the current mark price.
2. Updates the *high-water mark* if price has improved (LONG: higher; SHORT: lower).
3. Recomputes the floor price based on the current phase.
4. Checks all exit conditions in order (phase breach → hard timeout → dead weight cut → weak peak cut).
5. If any condition fires: closes the position and records the reason.
6. If no condition fires: checks whether a new tier is triggered and advances phase if needed.

---

### Phase 1 — Initial Defense

**Active from:** Position open until the first profit tier is triggered.

**Purpose:** Protect against immediate losses while allowing the position to develop.

**How the floor is computed:**

Phase 1 maintains two floors simultaneously and uses the stricter one (for a LONG: the higher price; for a SHORT: the lower price):

- **Absolute loss floor** — derived from `max_loss_pct`. Converts the maximum allowed loss into a price level. The position can never lose more than this percentage of margin.
- **Trailing retrace floor** — derived from `retrace_threshold`. Tracks the running high-water mark and sets the floor at `retrace_threshold` ROE% below it. As the position gains, the floor ratchets up.

**Exit mechanisms — two independent paths:**

1. **Exchange SL at the absolute floor** — The runtime places a stop-loss order on the exchange at the `max_loss_pct` price level. If price hits that level directly (e.g., a fast wick that skips the runtime's polling interval), the exchange executes the SL and closes the position with reason `exchange_sl_hit`. This is the hard backstop.

2. **Runtime breach counting** — Each tick, if the current price is at or below (LONG) / at or above (SHORT) the effective floor (the stricter of the absolute and retrace floors):
   - Breach counter increments.
   - Once counter reaches `consecutive_breaches_required` → runtime closes the position with reason `dsl_breach`.
   - If any tick recovers above the floor → counter resets to 0.

The exchange SL and the runtime breach counter are complementary: the SL guarantees the absolute floor even if the runtime misses a fast move, while breach counting handles slower retracements that the runtime observes tick by tick.

`consecutive_breaches_required` filters out momentary wicks. Setting it to 1 exits on the first touch; setting it to 3 requires three consecutive ticks below the floor.

---

### Phase 2 — Profit Lock

**Active from:** When the first tier's `trigger_pct` is crossed.

**Purpose:** Lock in accumulated gains with a trailing floor that tightens as price makes new highs.

**How the floor is computed:**

Each tier defines a `lock_hw_pct`. The floor is:

```
floor_roe = high_water_roe × (lock_hw_pct / 100)
floor_price = entry_price + floor_roe converted to price
```

The floor **only moves up** (ratchets). When price makes a new high-water the floor tightens; when price retraces the floor stays fixed.

**Exit mechanism — exchange SL only:**

In Phase 2, all exits happen on the exchange. The runtime places and continuously updates a stop-loss order on the exchange at the current floor price. When price hits the floor, the exchange executes the SL and closes the position with reason `exchange_sl_hit`. The runtime itself does not count breaches or trigger the close — it only manages the SL order placement and ratcheting.

**Example:**

```
Entry: $100, 10× leverage, Tier 1: trigger_pct=10, lock_hw_pct=40

Position hits ROE +10% → Tier 1 activates → Phase 2 begins
  high_water_roe = 10%
  floor_roe = 10 × 0.40 = 4%
  floor_price = $100.40  → exchange SL placed at $100.40

Position climbs further → high_water_roe = 18%
  floor_roe = 18 × 0.40 = 7.2%
  floor_price = $100.72  → exchange SL updated to $100.72

Tier 2: trigger_pct=20, lock_hw_pct=70 activates
  high_water_roe = 22%
  floor_roe = 22 × 0.70 = 15.4%
  floor_price = $101.54  → exchange SL updated to $101.54

Price falls to $101.54 → exchange SL executes → close, reason: exchange_sl_hit
```

**Key difference from Phase 1:** Phase 1 uses runtime breach counting (with configurable tolerance via `consecutive_breaches_required`). Phase 2 is entirely exchange-driven — the runtime's role is only to keep the SL order updated as the floor ratchets up.

---

### `retrace_threshold` — what it actually means

`retrace_threshold` is in **ROE percent** (not price percent). The engine converts it to a price distance using leverage:

```
price_retrace = retrace_threshold / 100 / leverage
```

Example: `retrace_threshold: 3` on a 10× LONG position — the floor is 0.3% below the high-water price. At 20× leverage, the same `3` ROE% = only 0.15% price distance.

Set this based on your position's typical noise relative to leverage. High-leverage positions need smaller `retrace_threshold` values to avoid premature exits from normal volatility.

---

### `consecutive_breaches_required` — what it actually means

Each monitor tick (`interval_seconds`) where price is at or below the floor counts as one breach. Consecutive means there must be no recovery tick in between.

- `1` → exit immediately on first breach (tight, no tolerance for wicks)
- `2` → two ticks in a row below floor (filters single-candle wicks)
- `3` → three consecutive ticks (more tolerant, useful for volatile assets)

The breach counter resets on any tick where price is above the floor.

---

### Time-Based Exit Conditions

These run every tick alongside the phase logic and can trigger exits independently of breach counting. They evaluate in **both Phase 1 and Phase 2** — they are outer-bound protections, not Phase-1-only patience knobs. The only exception is `hard_timeout`, which skips the exact tick a position crosses into Phase 2 so a boundary hit cannot lose to the clock before the tier advance runs.

`weak_peak_cut` is a special case: it evaluates in both phases by code, but its guard `peakROE < min_value` becomes unsatisfiable in Phase 2 whenever `min_value` is set below the first tier's `trigger_pct` (entering Phase 2 implies `peakROE ≥ trigger_pct`). In that common configuration it is *effectively* Phase-1-only. Set `min_value` above the first tier if you want it to remain active in Phase 2.

---

#### `hard_timeout`

> "Close any position that has been open for at least N minutes."

An outer-bound protection against capital being tied up indefinitely. Fires in both phases — even a profitable Phase 2 position will be closed once it crosses the timeout. Tune the interval high enough to give your strategy room (e.g. 6–72h), not as a Phase 1 patience knob.

**Field:** `interval_in_minutes` — time from position open. Must be > 0.

---

#### `dead_weight_cut`

> "If the position has been in negative ROE continuously for N minutes, close it."

Catches entries that turned immediately unprofitable and never recovered. The internal timer (`deadWeightCutStartedAt`) **resets every tick while `currentROE > 0`**, and the cut fires when elapsed time since that timer ≥ `interval_in_minutes` **and** `currentROE ≤ 0` on the current tick. In plain terms: the timer starts (or restarts) when ROE dips to zero or below, and any positive-ROE tick resets it.

**Field:** `interval_in_minutes` — duration of continuous non-positive ROE before exit. Must be > 0.

---

#### `weak_peak_cut`

> "If the position made some profit but never exceeded `min_value` ROE, and has since declined from its peak, and N minutes have elapsed — close it."

Catches a specific scenario: the trade worked a little (price moved in your favor) but not enough to trigger a tier, and now it's fading. Without this cut, you could sit in a position that peaked at +1% and is now at +0.2% indefinitely.

Exit condition (all must be true after `interval_in_minutes` elapsed):
- `peakROE < min_value` — position never crossed the minimum profit threshold
- `currentROE < peakROE` — it's retreating from that weak peak

**Fields:**
- `interval_in_minutes` — how long to wait before cutting. Must be > 0.
- `min_value` — minimum ROE% the position must have reached to be considered "made real profit". The cut evaluates in both phases, but if `min_value` sits below the first tier's `trigger_pct` it cannot fire in Phase 2 (peak ROE will already exceed `min_value` by the time the tier triggered). Must be > 0.

---

### Weak Peak Cut vs Dead Weight Cut

| | `weak_peak_cut` | `dead_weight_cut` |
|---|---|---|
| Trigger ROE | Position was profitable but peak < `min_value` | Position is at or below zero |
| Timer reset | On tick with ROE above peak threshold | On any tick with `currentROE > 0` |
| Scenario | "Trade worked a little but faded" | "Trade went negative and stayed there" |
| Purpose | Exit slow faders before they erase profit | Exit soured entries before they deepen losses |

---

### DSL Close Reasons

| Reason | Cause |
|---|---|
| `dsl_breach` | Consecutive breach count reached threshold (Phase 1 only) |
| `hard_timeout` | Position open longer than `hard_timeout.interval_in_minutes` (fires in both phases) |
| `weak_peak_cut` | Position peaked below `min_value` and then declined (fires in both phases; practically Phase 1 only when `min_value` < first tier `trigger_pct`) |
| `dead_weight_cut` | Position stayed in non-positive ROE past `dead_weight_cut.interval_in_minutes` (fires in both phases) |
| `exchange_sl_hit` | Exchange stop-loss triggered (Phase 2 floor hit externally, or Phase 1 absolute floor) |
| `manual_close` | Position closed by user or action |
| `closed_externally` | Position closed outside the runtime (e.g., exchange UI) |
| `flipped` | Position direction reversed (detected by position tracker) |
| `position_increased` / `position_decreased` | Position size changed (size-change event) |

See [dsl-configuration.md](dsl-configuration.md#close-reasons) for the complete close-reason table.

---

## Scanner → Action → DSL: Full Flow Example

```
[Hyperliquid exchange]
       ↓ (every 10s)
position_tracker scanner polls wallet
  → Detects: SOL LONG opened, entry=$150, size=10, leverage=10×
  → Emits: POSITION_OPENED signal
       ↓
POSITION_TRACKER action receives signal
  → Fires: ON_POSITION_OPENED { asset: SOL, direction: LONG, entryPrice: 150, leverage: 10 }
       ↓
DSL monitor receives ON_POSITION_OPENED
  → Creates DslState for SOL
  → Sets absolute loss floor at max_loss_pct=4% → floor ROE = -4% → floor price = $149.40
       ↓ (every 30s)
DSL tick evaluates SOL

  Tick 1: price=$152.00 → ROE=+1.33% → hw=$152, floor=$148.95 (retrace 7% ROE = 0.7% price)
           No breach, no tier → continue

  Tick 8: price=$165.00 → ROE=+10% → tier 1 triggered (trigger_pct=7)
           → ENTER PHASE 2
           → lock_hw_pct=40 → floor_roe=10×0.40=4% → floor_price=$150.60
           → hard_timeout disabled

  Tick 15: price=$180.00 → ROE=+20% → tier 3 triggered (trigger_pct=20)
            → lock_hw_pct=75 → floor_roe=20×0.75=15% → floor_price=$152.25

  Tick 18: price=$155.00 → ROE=+3.3% → exchange SL at $152.25 executes
            → CLOSE, reason: exchange_sl_hit
       ↓
Telegram notification sent
```

---

## Field Quick Reference (Trading Terms)

| Field | Plain meaning |
|---|---|
| `retrace_threshold` | ROE% pullback from peak that defines the trailing floor in Phase 1. Divide by leverage to get price%. |
| `consecutive_breaches_required` | Number of consecutive monitor ticks below the floor before Phase 1 exits. |
| `max_loss_pct` | Hard absolute floor — never let the position lose more than this ROE% from entry. Enter as a positive number. |
| `trigger_pct` (tier) | ROE% that must be reached to activate this tier and enter Phase 2. Tiers must be in ascending order. |
| `lock_hw_pct` (tier) | What % of peak high-water ROE to protect as the Phase 2 trailing floor. Higher = tighter stop. |
| `hard_timeout` | Maximum minutes to remain in Phase 1 before giving up on an undeveloped position. |
| `dead_weight_cut` | Maximum minutes to stay in non-positive ROE before cutting the loss. |
| `weak_peak_cut` | Exits fading positions whose peak never exceeded `min_value` ROE, after `interval_in_minutes`. |
| `interval_seconds` | How often the DSL evaluates all open positions. Must be 5–3600. |
