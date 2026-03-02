# DSL Skill — Detailed Explanation

This document explains every term, the exact flow, and a worked example for the Dynamic Stop Loss (DSL) skill.

---

## 0. Cron schedule alignment (OpenClaw / interval-based schedulers)

Some cron systems (e.g. OpenClaw) support two schedule types:

- **Interval from anchor** (e.g. `everyMs: 180000`): the scheduler stores an internal “anchor” timestamp when the job is created and computes each “next run” from that. The anchor is **not** aligned to wall-clock (e.g. :00, :03, :06…), so runs can appear at irregular times — e.g. shortly after creation, or ~1 minute apart when comparing to wall clock, or when the system is doing catch-up.
- **Cron expression** (e.g. `kind: "cron", expr: "*/3 * * * *", tz: "UTC"`): runs at fixed clock boundaries (every 3 minutes at :00, :03, :06…).

**Fix from the agent side:** When creating the DSL cron, use a **cron expression** for the 3-minute schedule instead of a raw interval. That gives perfectly regular times without having to remove and re-add the job at a boundary. Example: `"schedule": { "kind": "cron", "expr": "*/3 * * * *", "tz": "UTC" }`.

---

## 1. Glossary (Terms and Meanings)

| Term | Meaning |
|------|--------|
| **DSL** | Dynamic Stop Loss — a **trailing** stop that moves in your favor as price moves. Not a fixed price stop. |
| **High water (HW)** | The best price seen so far in your favor. **LONG:** highest price since entry. **SHORT:** lowest price since entry. Used as the reference for “how far price has moved” and for trailing floors. |
| **Retrace** | How far price is allowed to **pull back** from the high-water mark before we consider it a “breach.” Expressed as a fraction (e.g. 0.03 = 3%). **LONG:** floor = `hw × (1 - retrace)`. **SHORT:** floor = `hw × (1 + retrace)`. |
| **Trailing floor** | A floor derived from high water and retrace: “price must not go below/above this level relative to HW.” **LONG:** `hw × (1 - retrace)`. **SHORT:** `hw × (1 + retrace)`. |
| **Absolute floor** | Phase 1 only. A hard price limit that caps maximum loss (e.g. 3% of margin). **LONG:** below entry. **SHORT:** above entry. The effective floor in Phase 1 is never worse than this. |
| **Tier** | A profit milestone. Each tier has a **trigger** (ROE % at which the tier activates) and a **lock** (% of the entry→HW range to lock as a floor). Tiers ratchet: once you hit Tier 2, Tier 1’s floor is superseded and never goes back. |
| **Tier floor** | The locked price floor for the current tier. **LONG:** `entry + (hw - entry) × lockPct/100`. **SHORT:** `entry - (entry - hw) × lockPct/100`. Ratchets only in the protective direction (up for LONG, down for SHORT). |
| **Trigger (triggerPct)** | ROE % (return on margin) at which this tier activates. Example: 10 means “when unrealized PnL is 10% of margin.” |
| **Lock (lockPct)** | Percentage of the **price range** from entry to high water that we “lock in” as the tier floor. Example: 5 means lock 5% of that range; 80 means lock 80%. |
| **ROE (Return on Equity)** | `uPnL / margin × 100`. Margin = `entry × size / leverage`. So tier triggers are based on % return on margin, not raw price move — leverage is built in. |
| **uPnL** | Unrealized PnL. **LONG:** `(price - entry) × size`. **SHORT:** `(entry - price) × size`. |
| **Effective floor** | The single price level used for breach checks. It is the **best** of: tier floor (Phase 2), trailing floor, and absolute floor (Phase 1). **LONG:** best = max of those. **SHORT:** best = min of those. |
| **Breach** | One check where price is on the wrong side of the effective floor. **LONG:** `price ≤ effective_floor`. **SHORT:** `price ≥ effective_floor`. |
| **Consecutive breaches** | How many checks in a row must be breaches before we close. Phase 1 often uses 3 (patient); Phase 2 often uses 1–2 (quicker exit). |
| **Breach decay** | When price recovers (no longer breached): **hard** = breach count resets to 0; **soft** = count decreases by 1 per check. |
| **Phase 1** | “Let it breathe.” Before the first tier triggers. Wide retrace (e.g. 3%), more consecutive breaches (e.g. 3), absolute floor caps max loss. |
| **Phase 2** | “Lock the bag.” After the configured tier (e.g. Tier 1) is hit. Tighter retrace (e.g. 1.5% or per-tier), fewer breaches (e.g. 2), tier floor + trailing floor. |
| **Reconcile** | Script deletes state files for assets that are no longer in the clearinghouse (position was closed outside DSL). |
| **State file** | One JSON file per position: config (entry, size, tiers, phase config) + runtime state (high water, current tier, breach count, etc.). Path: `{DSL_STATE_DIR}/{strategyId}/{asset}.json`. |

### 1.1 Tier vs retrace (how they differ)

They answer different questions:

| | **Retrace** | **Tier** |
|---|-------------|----------|
| **What it is** | A **single number** (e.g. 3% or 0.03): “How far can price pull back from the high-water mark before we treat it as a breach?” | A **profit step** in a ladder: “When my profit hits X% (trigger), lock in a floor at Y% of the move (lock).” |
| **What it sets** | The **trailing floor**: “Floor = HW minus (for LONG) or plus (for SHORT) this %.” So retrace = **distance from HW to the floor**. | The **tier floor**: a locked price that depends on entry, HW, and lockPct. “Lock in this much of the profit.” |
| **When it’s used** | Every run, in both Phase 1 and Phase 2, to compute the trailing floor from current HW. | Only in Phase 2, when you’ve hit at least one tier. The tier floor is then combined with the trailing floor. |
| **Analogy** | “The stop is always N% behind the high water.” (Like a trailing stop distance.) | “At 10% profit I lock a floor; at 20% I lock a higher floor; …” (Like profit-taking steps.) |

**They work together in Phase 2:**  
You have both a **tier floor** (locked from when you hit the tier) and a **trailing floor** (HW × (1 ± retrace)). The **effective floor** is the **best** of the two: for LONG we use the **higher** of tier floor and trailing floor; for SHORT the **lower**. So:

- **Retrace** = how closely the stop trails the high water (one number, or per-tier override).
- **Tier** = which profit step you’re on and what locked floor that step gives you (trigger + lock; optionally its own retrace for the trailing part).

**Simple picture (LONG):**  
- Entry $100, HW $110. Retrace 3% → trailing floor = 110 × 0.97 = $106.70.  
- You hit Tier 1 (trigger 10%, lock 5%) → tier floor = 100 + (110−100)×5/100 = $100.50.  
- Effective floor = max(100.50, 106.70) = **$106.70** (the trailing floor is higher, so it wins).  
If price later pulls back to $107, HW stays $110; if it pulls back to $106, you’re below the floor → breach.

---

## 2. Exact Flow

### 2.1 Strategy-level (each cron run)

1. **Strategy active?**  
   Call MCP `strategy_get(strategy_id)`.
   - If **not** ACTIVE/PAUSED and we **know** it (confirmed inactive): delete all state files in the strategy dir, print `strategy_inactive`, exit 0. Agent removes cron and runs cleanup.
   - If **error** (timeout, no wallet, etc.): print `strategy_get_failed`, exit 1. **Do not** delete any state.

2. **Active positions**  
   Get wallet from strategy; call MCP `strategy_get_clearinghouse_state(wallet)` → set of coins (main + xyz).

3. **Reconcile**  
   Delete any state file whose asset is **not** in that set (position closed elsewhere).

4. **Per position**  
   For each coin that **has** a state file: run `process_one_position(state_file, strategy_id, now)`.

5. **No positions**  
   If no position was processed (no state files or none matched), print one line: `status: "no_positions"`.

### 2.2 Per-position (process_one_position)

For one state file:

1. Load state; normalize phase config if needed.
2. If not `active` and not `pendingClose` → print `inactive`, return.
3. **Fetch price** via MCP (market_get_prices / allMids). On failure: increment fetch failures; if ≥ max, set `active=false`; print error, return.
4. **Update high water:**  
   LONG: if price > hw then hw = price.  
   SHORT: if price < hw then hw = price.
5. **uPnL and ROE:**  
   `upnl = (price - entry)*size` (LONG) or `(entry - price)*size` (SHORT).  
   `upnl_pct = upnl / margin * 100` (margin = entry×size/leverage).
6. **Tier upgrades:**  
   For each tier whose `triggerPct` ≤ current `upnl_pct`: upgrade to that tier; set tier floor = entry + (hw−entry)×lockPct/100 (LONG) or entry − (entry−hw)×lockPct/100 (SHORT). **Ratchet:** never set a tier floor less protective than the stored one. If we cross `phase2TriggerTier`, switch to Phase 2 and reset breach count.
7. **Effective floor:**  
   - **Phase 1:** trailing_floor = hw×(1−retrace) (LONG) or hw×(1+retrace) (SHORT); effective_floor = max(absolute_floor, trailing_floor) (LONG) or min(absolute_floor, trailing_floor) (SHORT).  
   - **Phase 2:** same trailing from hw; effective_floor = max(tier_floor, trailing_floor) (LONG) or min(tier_floor, trailing_floor) (SHORT).
8. **Breach check:**  
   breached = (price ≤ effective_floor) for LONG or (price ≥ effective_floor) for SHORT.
9. **Breach count:**  
   If breached: count += 1. If not breached: hard → count=0; soft → count = max(0, count−1).
10. **Should close?**  
    If breach_count ≥ breaches_needed, or `pendingClose` → try close via MCP `close_position` (with retries). On success: delete state file. On failure: set `pendingClose=true`, save state.
11. **Persist** (if not deleted): write state (lastCheck, lastPrice, floorPrice, etc.).
12. **Print** one JSON line (ndjson) for this position.

---

## 3. Worked Example (LONG)

**Setup:** 10x leverage, entry **$28.87**, size **1890**, asset HYPE (main dex).

- **Phase 1:** retrace 3%, 3 consecutive breaches, absolute floor $28.00.
- **Phase 2:** starts at Tier 1; retrace 1.5%; 2 consecutive breaches.
- **Tiers:**  
  Tier 1: trigger 10%, lock 5%  
  Tier 2: trigger 20%, lock 14%  
  (simplified; more tiers in real config)

**Run 1 — Price = $28.50**

- HW = 28.87 (entry).
- uPnL = (28.50 − 28.87)×1890 = −698.43; margin = 28.87×1890/10 ≈ 5460.43; **upnl_pct ≈ −12.8%**.
- No tier (all triggerPct > 0). Phase 1.
- Trailing floor = 28.87×(1−0.03) ≈ 28.00; effective_floor = max(28.00, 28.00) = **28.00**.
- Price 28.50 > 28.00 → not breached; breach_count = 0.
- No close. State saved; one JSON line printed.

**Run 2 — Price = $29.20**

- HW updates to **29.20**.
- uPnL = (29.20−28.87)×1890 ≈ 623.7; upnl_pct ≈ **11.4%**.
- 11.4% ≥ 10% → **Tier 1** activates. phase2TriggerTier=1 → **Phase 2**.
- Tier floor = 28.87 + (29.20−28.87)×5/100 = 28.87 + 0.0165 ≈ **28.89**.
- Trailing floor = 29.20×(1−0.015) ≈ **28.76**.
- Effective floor = max(28.89, 28.76) = **28.89**.
- Price 29.20 > 28.89 → not breached; breach_count = 0.
- No close. State saved; output includes `tier_changed: true`, Tier 1.

**Run 3 — Price = $29.10**

- HW stays **29.20** (price didn’t make a new high).
- uPnL = (29.10−28.87)×1890 ≈ 434.7; upnl_pct ≈ 8%. Still Tier 1.
- Tier floor still **28.89**; trailing = 29.20×(1−0.015) ≈ **28.76**.
- Effective floor = max(28.89, 28.76) = **28.89**.
- Price 29.10 > 28.89 → not breached. Breach count stays 0.

**Run 4 — Price = $28.85**

- HW still **29.20**.
- Effective floor still **28.89**.
- Price **28.85 ≤ 28.89** → **breached**. breach_count = 1.
- breaches_needed = 2 → no close yet. State saved; output shows breached: true, breach 1/2.

**Run 5 — Price = $28.80**

- Still breached (28.80 ≤ 28.89). breach_count = 2.
- 2 ≥ 2 → **should_close**. Script calls `close_position`; on success deletes state file and prints `closed: true`. Agent alerts user.

---

## 4. Example: Strategy with Three Positions (ETH long, BTC short, xyz:SILVER short)

One strategy has three positions: **ETH LONG** (main dex), **BTC SHORT** (main dex), **xyz:SILVER SHORT** (xyz dex). One cron runs the script for the whole strategy; the script discovers all three from the clearinghouse and their state files.

### 4.1 Layout

- **Cron:** one job per strategy, e.g. every 3 min:
  - `DSL_STATE_DIR=/data/workspace/dsl DSL_STRATEGY_ID=strat-abc-123 python3 scripts/dsl-v5.py`
- **State directory:** `$DSL_STATE_DIR/strat-abc-123/`
- **State files (one per position):**

| Asset       | Direction | Dex  | Filename        | Path (example)                              |
|------------|-----------|------|-----------------|---------------------------------------------|
| ETH        | LONG      | main | `ETH.json`      | `.../strat-abc-123/ETH.json`                 |
| BTC        | SHORT     | main | `BTC.json`      | `.../strat-abc-123/BTC.json`                 |
| xyz:SILVER | SHORT     | xyz  | `xyz--SILVER.json` | `.../strat-abc-123/xyz--SILVER.json`     |

Naming: main dex uses the symbol as filename; xyz dex uses the colon replaced by double-dash (`xyz:SILVER` → `xyz--SILVER.json`).

### 4.2 One cron run (what happens)

1. **Strategy check** — MCP `strategy_get("strat-abc-123")` → status ACTIVE, wallet `0x…`.
2. **Positions** — MCP `strategy_get_clearinghouse_state(wallet)` → e.g. `{ "ETH", "BTC", "xyz:SILVER" }` (main + xyz).
3. **Reconcile** — Any state file whose asset is not in that set is deleted (e.g. if SOL was closed elsewhere, `SOL.json` would be removed).
4. **Per position** — For each of ETH, BTC, xyz:SILVER that has a state file:
   - Load state → fetch price (main vs xyz via asset prefix) → update HW, tiers, effective floor → breach check → close if needed → save or delete → **print one JSON line**.
5. **Output** — Three lines (ndjson), one per position, or one strategy-level line if none had state files.

### 4.3 Snapshot of the three positions (one run)

Plausible numbers for a single run:

| Asset       | Direction | Entry   | Size  | Price (run) | HW      | Effective floor | Breach (LONG: price≤floor; SHORT: price≥floor) | Breaches |
|------------|-----------|---------|-------|--------------|---------|------------------|--------------------------------------------------|----------|
| **ETH**    | LONG      | 3,400   | 0.5   | 3,420        | 3,430   | 3,380 (trailing) | 3,420 > 3,380 → no                               | 0/3      |
| **BTC**    | SHORT     | 67,000  | 0.01  | 66,200       | 65,800  | 66,787 (trailing)| 66,200 < 66,787 → no                              | 0/3      |
| **xyz:SILVER** | SHORT | 28.50   | 100   | 28.55        | 28.00   | 28.42 (trailing) | 28.55 ≥ 28.42 → **yes** (price rose above floor for SHORT) | 1/3      |

- **ETH LONG:** HW = highest price seen; floor below HW (trailing); breach when price ≤ floor. Price above floor → no breach.
- **BTC SHORT:** HW = lowest price seen; floor above HW (trailing); breach when price ≥ floor. Price below floor → no breach.
- **xyz:SILVER SHORT:** Same as BTC; breach when price ≥ floor. Price 28.55 rose above the trailing floor 28.42 → breach (1/3). Two more consecutive runs with price ≥ floor would trigger close (if 3 required).

### 4.4 Direction and floor (reminder)

|        | LONG (ETH)     | SHORT (BTC, xyz:SILVER)  |
|--------|----------------|---------------------------|
| **HW** | Highest price  | Lowest price              |
| **Floor** | Below entry/HW | Above entry/HW         |
| **Breach** | price ≤ floor | price ≥ floor           |
| **Tier floor** | entry + (hw−entry)×lockPct/100 | entry − (entry−hw)×lockPct/100 |

Each position has its own state file (entry, size, leverage, tiers, phase, breach count). The script processes them independently; one position breaching and closing does not change the others.

---

## 5. Summary

- **High water** = best price in your favor; **retrace** = allowed pullback from HW; **tier** = profit milestone with trigger (ROE %) and lock (% of entry→HW range).
- **Effective floor** = best of tier floor, trailing floor, and (in Phase 1) absolute floor. **Breach** = price on wrong side of that floor; after enough **consecutive** breaches (with optional **decay**), the script **closes** the position and **deletes** the state file.
- Flow: strategy active → clearinghouse positions → reconcile state → for each position with state: fetch price → update HW → tier upgrades (ratcheted) → effective floor → breach count → close if needed → save or delete → one JSON line per position (or one strategy-level line when none processed).
