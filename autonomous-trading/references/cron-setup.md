## Step 5: Set Up Cron Jobs

### Race Condition Prevention (v6 — CRITICAL)

**All cron jobs that can close positions MUST use `close_position_safe()`** (see Known Bugs & Gotchas). This ensures:

1. Only one job actually closes the position
2. DSL state file is immediately deactivated
3. DSL cron is immediately disabled
4. All jobs log which one performed the close

Before attempting any close, every job must first verify the position still exists:

```python
def position_exists(asset, wallet):
    """Check if position is still open before attempting close."""
    state = get_clearinghouse_state(wallet)
    return any(p["asset"] == asset for p in state.get("positions", []))
```

### 1. Opportunity Scanner (every 10-30 minutes, time-aware)

Runs the Scanner v5 recipe. Decision logic:

```
1. Check hourly trend for the asset (MUST align with trade direction)
2. Check max-leverage.json for actual max leverage
3. Check scanner score:
   IF score >= 200 AND trend-aligned AND no disqualifying risks AND exposure guard passes:
     → Full position size
   IF score 175-199 AND trend-aligned AND no disqualifying risks AND exposure guard passes:
     → Reduced position size (60-80%)
   IF score < 175 OR counter-trend:
     → Skip
4. Check: is this slot worth filling? (empty slot > mediocre position)
```

**v6: Speed filter** — Best moves happen FAST. XRP hit Tier 3 in 19 min, XMR hit Tier 2 in 37 min. If a position isn't moving within 30 minutes of entry, it probably won't. Factor this into re-entry decisions.

**v6: Erratic rank history = SKIP** — If an asset's rank is bouncing (#34→#29→#11→#29→#12), that's noise, not signal. Look for consistent rank improvement (e.g., #45→#30→#18→#10).

**Exposure check before execution:**

```python
def check_exposure(new_direction, new_notional, active_trades, budget, max_pct):
    long_ntl = sum(t["margin"] * t["leverage"] for t in active_trades if t["direction"] == "LONG")
    short_ntl = sum(t["margin"] * t["leverage"] for t in active_trades if t["direction"] == "SHORT")
    if new_direction == "LONG":
        long_ntl += new_notional
    else:
        short_ntl += new_notional
    net = abs(long_ntl - short_ntl)
    net_pct = net / budget * 100
    if net_pct > max_pct:
        if new_direction == ("LONG" if long_ntl > short_ntl else "SHORT"):
            return "reduce"  # same direction as dominant — reduce size 50%
        else:
            return "ok"  # opposite direction — always allowed
    return "ok"
```

**Disqualifying risks** (skip regardless of score):

- Extreme RSI: RSI < 20 for SHORTs, or RSI > 80 for LONGs
- Counter-trend on hourly (hard skip) or 4h with strength > 50
- Volume dying (ratio < 0.5 on both TFs)
- Funding heavily against you (> 50% annualized)
- BTC macro headwind > 30 pts
- Erratic rank history (bouncing ranks = noise)

**Trade execution:**

1. **Check hourly trend alignment** (HARD REQUIREMENT)
2. **Check `max-leverage.json`** for actual max leverage
3. Check directional exposure
4. Evaluate slot value (is this position worth the slot?)
5. Describe the trade plan to the user (unless `autonomousExecution` is true)
6. Execute via `create_position` with `orderType: "MARKET"` (**NEVER use `dryRun: true`**)
7. For XYZ assets: include `leverageType: "ISOLATED"`
8. Create DSL v4 state file for the new position
9. Enable DSL cron
10. Log trade with full scanner snapshot to `auto-strategy.json`
11. Send Telegram confirmation

### 2. DSL Monitor (every 2-3 minutes per position)

Uses DSL v4 recipe (script: `/data/workspace/scripts/dsl-v4.py`). Stagger crons to avoid overlapping.

**Agent behavior for DSL output:**

- `velocity_toward_floor > 0.5` → consider increasing DSL cron frequency to 1 min
- `pending_close = true` → alert user, attempt close via `close_position_safe()`
- `consecutive_failures >= 3` → alert user about API issues
- `tier_changed = true` → notify user with details
- `distance_to_next_tier_pct < 2` → position approaching next tier lock

**v6: When DSL closes a position**, it must immediately deactivate its own state file and disable its own cron. Other jobs (scanner, SM flip) must not try to close the same position.

### 3. Smart Money Flip Detector (every 5 minutes)

**v6: Hardened SM flip rules with conviction collapse detection.**

SM signals are checked every 5 minutes. When a flip signal is detected:

**FLIP_NOW (conviction ≥ 4) — Evaluate before executing:**

```
1. CHECK: SM trader count ≥ 200? (conviction alone isn't enough — 130 traders at conv 4 is noise)
2. CHECK: Aligns with hourly trend? (NEVER flip counter-trend on hourly)
3. CHECK: 30-minute cooldown since last flip on this asset? (prevents whipsaw)
4. CHECK: Cumulative flip cost for this asset < 3% of margin? (fee circuit breaker)

ALL FOUR must pass → execute flip
ANY fail → ignore the signal, log it, continue monitoring
```

**v6: CONVICTION COLLAPSE = INSTANT CUT:**

```
IF SM conviction drops from ≥4 to ≤1 within 10 minutes
   (e.g., 220 traders → 24 traders):
   → Cut the position IMMEDIATELY. Don't wait for next scan.
   → This signals a rapid sentiment reversal.
   → Use close_position_safe() to prevent race conditions.
```

**v6: DEAD WEIGHT AT CONVICTION 0 = INSTANT CUT:**

```
IF SM conviction = 0 for your position's direction:
   → Cut immediately. Don't wait 2 hours. Don't wait for DSL.
   → Free the slot for better opportunities.
   → Empty slot > dead weight.
```

**FLIP_WARNING (conviction 2-3):**
- Alert user once, then suppress duplicates for 30 minutes
- Note in portfolio updates instead of spamming separate alerts
- If conviction persists at 3 for >1 hour, it's a stronger signal — still requires hourly trend check

**Why these rules exist:**
- BTC SM conviction 4 flipped direction 3 times in 2 hours on one session — pure chop on 1-minute noise
- Each flip costs close + open fees (~1.26% of margin at 7x round-trip)
- 3 flips = ~3.8% margin lost to fees alone, plus slippage
- Conviction 4 with 130 traders (short-term noise) ≠ conviction 4 with 400+ traders (real sentiment shift)

### v6: Dead Weight Cutting (Enhanced)

Proactively close positions that are going nowhere:

```
IF SM conviction = 0 for position direction:
   → INSTANT CUT. No waiting.

IF SM conviction drops from ≥4 to ≤1 within 10 minutes:
   → INSTANT CUT. Conviction collapse.

IF position has SM conviction ≤ 1 for the opposing direction
   AND SM direction is misaligned for > 2 hours
   AND position uPnL is negative:
   → Close the position. It's dead weight consuming margin.
```

**v6 mindset: Every slot must maximize ROI.** An empty slot is worth more than a mediocre position. Don't reflexively re-enter after cutting dead weight — wait for a genuine high-conviction opportunity.

### 4. Portfolio Update (every 15 minutes)

```
📊 AUTO v6 | 12:51 UTC | $2,999 | Realized +$14.51
Asset  Dir/Lev    Entry      Now        uPnL       Margin Buffer
ETH    SHORT 7x   $1,955.20  $1,949.65  +$20.41    89.7%
SOL    LONG 7x    $80.99     $81.70     +$49.35    89.7%

2/3 slots • DSL v4: no breaches • BTC: down (macro: -20 LONG)
Net exposure: 65% SHORT • Hourly trends: ETH↓ SOL↑
Max lev: ETH=25x SOL=20x (using 7x each)
```

Additions over v5:
- Cross-margin buffer percentage
- Max leverage available vs used
- Conviction collapse warnings

### Time-Aware Scan Schedule (optional)

If `rules.schedule` is configured, the scanner cron interval adjusts by hour:

| Session | Hours (UTC) | Scan Interval | Notes |
|---|---|---|---|
| High activity | 13-20 | 10 min | US market overlap, highest volume |
| Medium activity | 6-12, 21-22 | 15 min | EU/Asia sessions |
| Low activity | 0-5, 23 | 30 min | Low volume, wider DSL stops |

During low-activity hours, widen DSL Phase 1 retrace by 20% to avoid stops from thin-book wicks.

---

