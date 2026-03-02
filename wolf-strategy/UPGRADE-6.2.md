# WOLF v6.2 Upgrade — Sniper Mode

Upgrade from WOLF v6.0 to v6.2. Drop-in patch — no reinstall needed.

## What's New

**Sniper Mode** — Fewer trades, higher quality, catch moves early instead of chasing.

### Changes
- **DSL Phase 1 rewrite** (`dsl-combined.py`) — 3-tier auto-cut rules: dead weight (30min, never positive → cut), weak peak (45min, peak <3% and declining → cut), hard cap (90min → cut regardless). Replaces the old 2-rule system.
- **Health Check** (`job-health-check.py`) — Silenced false NO_WALLET alerts when no strategy wallet is set yet
- **Watchdog** (`wolf-monitor.py`) — Same NO_WALLET fix
- **Dynamic Slots** — Profitable days unlock more entries (base 3, +1 per $100 profit, cap 6)
- **Scanner cron prompt** — Sniper mode rules with stricter entry filters

### What's NOT in this patch (prompt-only changes)
The sniper mode entry filters (FIRST_JUMP only, rank ≥20, top 10 block, 4h price filter, etc.) are enforced in the **scanner cron prompt**, not in `emerging-movers.py`. The scanner script is unchanged — it still outputs all signals. The cron prompt tells the agent which signals to act on. This is by design: the scanner stays general-purpose, the cron prompt defines the strategy.

## Files to Replace

Copy these 3 files into your `scripts/` directory, replacing the existing versions:

```
dsl-combined.py       → scripts/dsl-combined.py   (Phase 1 rewrite)
job-health-check.py   → scripts/job-health-check.py
wolf-monitor.py       → scripts/wolf-monitor.py
```

**Note:** The original patch shipped `dsl-v4.py` as a separate standalone file. This corrected patch merges those fixes into the existing `dsl-combined.py` which preserves multi-strategy support, atomic writes via wolf_config, and batch price fetching. Do NOT use the standalone `dsl-v4.py` — it lacks multi-strategy support.

## Config Changes

### 1. Add Dynamic Slots to `wolf-trade-counter.json`

Add this block to your existing `wolf-trade-counter.json`:

```json
{
  "dynamicSlots": {
    "enabled": true,
    "baseMax": 3,
    "absoluteMax": 6,
    "unlockThresholds": [
      { "pnl": 100, "maxEntries": 4 },
      { "pnl": 200, "maxEntries": 5 },
      { "pnl": 300, "maxEntries": 6 }
    ]
  }
}
```

### 2. (Optional) Add Dead Weight Cut Minutes to DSL Config

The new dead weight rule defaults to 30 minutes. To customize per-strategy, add to your strategy's `dsl` config in `wolf-strategies.json`:

```json
{
  "dsl": {
    "deadWeightCutMinutes": 25,
    "weakPeakCutMinutes": 45,
    "weakPeakThreshold": 3.0,
    "phase1MaxMinutes": 90
  }
}
```

### 3. Update Scanner Cron Prompt

Replace your Emerging Movers scanner cron prompt with the v6.2 version below. This adds sniper mode rules, dynamic slot logic, and the `strategy_create_custom_strategy` entry flow.

**Scanner Cron Prompt (v6.2):**

```
WOLF v6 Scanner: Run `PYTHONUNBUFFERED=1 python3 /data/workspace/scripts/emerging-movers.py`, parse JSON.

SLOT GUARD: Read wolf-strategies.json — if wallet is set and 1 active DSL in state/wolf-0af20dcc/ → HEARTBEAT_OK immediately.

ENTRY GATE: Read wolf-trade-counter.json. Check `dynamicSlots`:
- Base max = 3 entries/day
- If cumulativeNetPnl ≥ $100 → max = 4
- If cumulativeNetPnl ≥ $200 → max = 5
- If cumulativeNetPnl ≥ $300 → max = 6 (absolute cap)
- If entries >= effective max → HEARTBEAT_OK

MISSION: Quality over quantity. Fewer trades, catch them EARLY.

RULES (v6.2 — sniper mode):
- **FIRST_JUMP ONLY** as primary signal. CONTRIB_EXPLOSION accepted only if also FJ.
- **Must start from rank ≥20** (currentRank after jump can be <20, but previous rank must be ≥20). We catch movers BEFORE they peak.
- **Skip if priceChg4h > 2% or < -2%** (for the signal direction). If it already moved big, we missed it.
- **NO entries for assets already in top 10.** No exceptions.
- **NO counter-trend entries.** BEARISH market = SHORT only. Check market-regime-last.json.
- **Negative velocity = SKIP.**
- **Min 4 reasons.** Below = skip.
- **Non-erratic only.** erratic=true → skip.

POSITION ENTRY (NEW FLOW):
- Use `strategy_create_custom_strategy` via mcporter. Senpi auto-bridges funds from embedded wallet.
- positions format: JSON array, e.g. `[{"coin":"BTC","direction":"SHORT","leverage":10,"marginAmount":1450}]`
- For XYZ: coin is `xyz:ASSET`, add `"leverageType":"ISOLATED"`
- initialBudget=1500, strategyName="WOLF-{ASSET}"
- After creation: poll strategy_list for new wallet/strategyId. Update wolf-strategies.json.
- Leverage: 10x default, up to 20x for high-conviction (6+ reasons). Min 7x.
- Create DSL state in /data/workspace/state/wolf-0af20dcc/dsl-{ASSET}.json (9-tier config from wolf-strategies.json)
- Phase 1: 90 min hard timeout, 45 min weak peak cut (peak <3% and declining), 25 min dead weight cut (neg ROE + no SM)
- After CLOSING a position: update wolf-trade-counter.json cumulativeNetPnl with realized PnL
- Alert Telegram with entry details

If no qualifying signal → HEARTBEAT_OK.
```

### 4. (Optional) DSL Integrity Check Cron

Safety net cron that catches missed DSL closes. Runs every 3 minutes in the main session.

**Schedule:** `*/3 * * * *` | **Session:** main | **Payload:** systemEvent

```
WOLF DSL Integrity: Quick check — for each active DSL in state/wolf-0af20dcc/dsl-*.json where active=true:
1. Get current ROE via `mcporter call senpi.strategy_get_clearinghouse_state strategy_wallet={wallet}`
2. Verify currentTierIndex matches ROE (T1≥5%, T2≥10%, T3≥20%, etc.)
3. If ROE exceeds next tier trigger but tierIndex is behind → UPDATE the DSL state (advance tier, set new floorPrice)
4. If ROE dropped below current tier's lockPct → position should have been closed by DSL runner. If still open, CLOSE it and alert Telegram
5. Update peakROE if current ROE > peakROE
6. Update lastChecked timestamp

If no active DSL positions or everything looks correct → HEARTBEAT_OK
```

## v6.2 Phase 1 Rules Summary

| Rule | Trigger | Condition |
|------|---------|-----------|
| Dead weight | 30 min | Peak ROE never went positive → cut |
| Weak peak | 45 min | Peak ROE < 3% AND currently declining → cut |
| Hard cap | 90 min | Still in Phase 1 → cut regardless |

All timers are configurable via strategy DSL config. Phase 1 is skipped entirely once a position reaches Tier 1.

## v6.2 Entry Rules Summary

| Filter | Rule |
|--------|------|
| Signal | FIRST_JUMP only (CE accepted if also FJ) |
| Start rank | Must be ≥20 before jump |
| Top 10 | Skip always |
| 4h price move | Skip if >2% in signal direction |
| Velocity | Must be positive |
| Reasons | Min 4 |
| Erratic | Skip if true |
| Counter-trend | Not allowed (BEARISH = SHORT only) |
| Leverage | Min 7x, default 10x, up to 20x for 6+ reasons |
| Dynamic slots | Base 3/day, +1 per $100 profit, cap 6 |
