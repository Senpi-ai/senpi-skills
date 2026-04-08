# Runtime Concepts — How the Senpi Trading Runtime Works

This document explains the conceptual behavior of every major component in `runtime.yaml`: what the position tracker scanner and action do at runtime, how the DSL exit engine makes exit decisions, and what each DSL field controls in trading terms.

---

## Top-Level Fields

These fields sit at the root of every `runtime.yaml` and identify the skill to the runtime.

| Field | Description |
|---|---|
| `name` | Unique identifier for this skill/tracker. Used by the runtime to reference and manage the running instance. Should be a short, descriptive slug. |
| `version` | Runtime schema version. Fixed at `1.0.0` — every skill must use this value. Tells the runtime which configuration format and feature set to expect. |
| `description` | Human-readable summary of the skill's strategy and tuning philosophy. Informational only — not used by the runtime engine. |

### `strategy`

The core configuration block that defines the trading context:

| Field | Description |
|---|---|
| `wallet` | The on-chain wallet address holding positions and executing trades. Keep this as `${WALLET_ADDRESS}`; the skill resolves and auto-fills it at link time by validating an existing strategy wallet (`strategy_list`) or creating a new strategy wallet (`strategy_create_custom_strategy`) and then linking that strategy wallet to the runtime. This is the actual trading wallet the runtime monitors and acts on. |
| `enabled` | Boolean flag to activate or pause the skill. When `false`, the runtime loads the config but takes no action. |

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

1. **Exchange SL at the absolute floor** — The runtime places a stop-loss order on the exchange at the `max_loss_pct` price level. If price hits that level directly (e.g., a fast wick that skips the runtime's polling interval), the exchange executes the SL and closes the position with reason `EXCHANGE_SL_HIT`. This is the hard backstop.

2. **Runtime breach counting** — Each tick, if the current price is at or below (LONG) / at or above (SHORT) the effective floor (the stricter of the absolute and retrace floors):
   - Breach counter increments.
   - Once counter reaches `consecutive_breaches_required` → runtime closes the position with reason `DSL_BREACH`.
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

In Phase 2, all exits happen on the exchange. The runtime places and continuously updates a stop-loss order on the exchange at the current floor price. When price hits the floor, the exchange executes the SL and closes the position with reason `EXCHANGE_SL_HIT`. The runtime itself does not count breaches or trigger the close — it only manages the SL order placement and ratcheting.

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

Price falls to $101.54 → exchange SL executes → close, reason: EXCHANGE_SL_HIT
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

These run every tick alongside the phase logic and can trigger exits independently of breach counting.

---

#### `hard_timeout`

> "If the position is still in Phase 1 after N minutes, close it."

Prevents tying up capital in a position that never developed enough profit to trigger a tier. Once Phase 2 is entered, `hard_timeout` is disabled — the position is profitable, so let it run.

**Field:** `interval_in_minutes` — time from position open. Must be > 0.

---

#### `dead_weight_cut`

> "If the position has been in negative ROE continuously for N minutes, close it."

Catches entries that turned immediately unprofitable and never recovered. The timer starts when ROE goes to zero or below and resets every time ROE goes positive again.

**Field:** `interval_in_minutes` — duration of continuous negative ROE before exit. Must be > 0.

---

#### `weak_peak_cut`

> "If the position made some profit but never exceeded `min_value` ROE, and has since declined from its peak, and N minutes have elapsed — close it."

Catches a specific scenario: the trade worked a little (price moved in your favor) but not enough to trigger a tier, and now it's fading. Without this cut, you could sit in a position that peaked at +1% and is now at +0.2% indefinitely.

The timer starts when ROE first goes positive. Exit condition (all must be true):
- `peakROE < min_value` — position never crossed the minimum profit threshold
- `currentROE < peakROE` — it's retreating from that weak peak
- Elapsed time ≥ `interval_in_minutes`

**Fields:**
- `interval_in_minutes` — how long to wait before cutting. Must be > 0.
- `min_value` — minimum ROE% the position must have reached to be considered "made real profit". If the peak exceeds this, the position enters Phase 2 via a tier and `weak_peak_cut` no longer applies. Must be > 0.

---

### Weak Peak Cut vs Dead Weight Cut

| | `weak_peak_cut` | `dead_weight_cut` |
|---|---|---|
| Trigger ROE | Position was profitable but peak < `min_value` | Position is at or below zero |
| Timer reset | When ROE goes positive | When ROE goes positive |
| Scenario | "Trade worked a little but faded" | "Trade went negative and stayed there" |
| Purpose | Exit slow faders before they erase profit | Exit soured entries before they deepen losses |

---

### DSL Close Reasons

| Reason | Cause |
|---|---|
| `DSL_BREACH` | Consecutive breach count reached threshold (Phase 1 or 2) |
| `HARD_TIMEOUT` | Position stayed in Phase 1 past `hard_timeout.interval_in_minutes` |
| `WEAK_PEAK_CUT` | Position peaked below `min_value` and then declined |
| `DEAD_WEIGHT_CUT` | Position stayed in negative ROE past `dead_weight_cut.interval_in_minutes` |
| `EXCHANGE_SL_HIT` | Exchange stop-loss triggered (Phase 2 floor hit externally) |
| `MANUAL` | Position closed manually on the exchange |
| `FLIPPED` | Position direction reversed (detected by position tracker) |

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
            → CLOSE, reason: EXCHANGE_SL_HIT
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
| `dead_weight_cut` | Maximum minutes to stay in negative ROE before cutting the loss. |
| `weak_peak_cut` | Exits fading positions whose peak never exceeded `min_value` ROE, after `interval_in_minutes`. |
| `interval_seconds` | How often the DSL evaluates all open positions. Must be 5–3600. |
