# Cron Setup — Fox v0.1

> All references below use `{TELEGRAM}`, `{SCRIPTS}`, `{WORKSPACE}`, `{BUDGET}` as placeholders. Replace at activation time.

## Race Condition Prevention (CRITICAL)

**All cron jobs that can close positions MUST deactivate DSL state immediately after close.** This ensures:

1. Only one job actually closes the position
2. DSL state file is immediately deactivated (or auto-reconciled by v5)
3. All jobs log which one performed the close

Before attempting any close, every job must first verify the position still exists:

```python
def position_exists(asset, wallet):
    """Check if position is still open before attempting close."""
    state = get_clearinghouse_state(wallet)
    return any(p["asset"] == asset for p in state.get("positions", []))
```

**Fox note:** DSL v5.3.1 reconciles against clearinghouse every run. If a position is closed externally, the state file is auto-deleted next tick. This makes phantom closes much rarer than v4.

## 1. Opportunity Scanner (every 15 minutes, main session)

Runs `fox-opportunity-scan-v6.py`. Decision logic:

```
1. Check hourly trend for the asset (MUST align with trade direction)
2. Check max-leverage.json for actual max leverage
3. Check scanner score:
   IF score >= 175 AND trend-aligned AND no disqualifying risks:
     → Size from tiered margin in fox-trade-counter.json
   IF score < 175 OR counter-trend:
     → Skip
4. Check: is this slot worth filling? (empty slot > mediocre position)
```

**Speed filter** — Best moves happen FAST. If a position isn't moving within 30 minutes of entry, it probably won't.

**Erratic rank history = SKIP** — Asset bouncing (#34→#29→#11→#29→#12) is noise. Look for consistent rank improvement.

**Disqualifying risks** (skip regardless of score):
- Extreme RSI: RSI < 20 for SHORTs, or RSI > 80 for LONGs
- Counter-trend on hourly (hard skip) or 4h with strength > 50
- Volume dying (ratio < 0.5 on both TFs)
- Funding heavily against you (> 50% annualized)
- BTC macro headwind > 30 pts

## 2. Emerging Movers (every 3 minutes, main session)

Runs `fox-emerging-movers.py`. Primary entry trigger.

**Entry execution:**
1. **Check hourly trend alignment** (HARD REQUIREMENT)
2. **Check `max-leverage.json`** for actual max leverage
3. Evaluate slot value (empty slot > mediocre position)
4. Score ≥ 6 pts? (≥ 8 for NEUTRAL regime)
6. Determine margin from tiered system in `fox-trade-counter.json`
7. Execute via `create_position` with `orderType: "MARKET"` (**NEVER use `dryRun: true`**)
8. For XYZ assets: include `leverageType: "ISOLATED"`
9. Create DSL v5.3.1 state file
10. Ensure DSL v5.3.1 cron exists for this strategy
11. Update `fox-trade-counter.json`
12. Send Telegram confirmation to `{TELEGRAM}`

## 3. DSL v5.3.1 Monitor (every 3 minutes per strategy, isolated)

Uses DSL v5.3.1 script from `skills/dsl-dynamic-stop-loss/scripts/dsl-v5.py`.

**Agent behavior for DSL output:**
- `closed: true` → alert user (script already deleted state file)
- `pending_close: true` → alert, script retries next tick
- `tier_changed: true` → notify user with tier details
- `strategy_inactive` → remove cron for this strategy
- `status: "error"` + `consecutive_failures >= 3` → alert user

## 4. Smart Money Flip Detector (every 5 minutes, isolated)

Runs `fox-sm-flip-check.py`.

**FLIP_NOW (conviction ≥ 4) — Evaluate before executing:**
```
1. CHECK: SM trader count ≥ 200?
2. CHECK: Aligns with hourly trend?
3. CHECK: 30-minute cooldown since last flip?
4. CHECK: Cumulative flip cost < 3% of margin?

ALL FOUR must pass → execute flip
ANY fail → ignore, log, continue monitoring
```

**CONVICTION COLLAPSE = INSTANT CUT:**
```
IF SM conviction drops from ≥4 to ≤1 within 10 minutes:
   → Cut IMMEDIATELY. Don't wait for next scan.
   → This signals rapid sentiment reversal.
```

**DEAD WEIGHT AT CONVICTION 0 = INSTANT CUT:**
```
IF SM conviction = 0 for your position's direction:
   → Cut immediately. Free the slot.
```

## 5. Watchdog (every 5 minutes, isolated)

Runs `fox-monitor.py`. Per-strategy margin buffer + position health.

- crypto_liq_buffer_pct < 50% → WARNING
- crypto_liq_buffer_pct < 30% → CRITICAL (close weakest position)
- XYZ liq_distance_pct < 15% → alert

## 6. Portfolio Update (every 15 minutes, isolated)

Agent reads `fox-strategies.json`, gets clearinghouse per wallet.

```
📊 FOX | {TIME} UTC | {ACCOUNT_VALUE} | Realized {REALIZED_PNL}
Asset  Dir/Lev    Entry      Now        uPnL       Margin Buffer
{ASSET} {DIR} {LEV}x  {ENTRY}    {PRICE}    {UPNL}     {BUFFER}%

{ENTRIES}/{MAX_ENTRIES} entries • DSL v5.3.1: {DSL_STATUS} • BTC: {BTC_TREND}
Net exposure: {NET_EXPOSURE}% {DOMINANT_DIR} • Hourly trends: {TRENDS}
```

## 7. Market Regime Refresh (every 4 hours, isolated)

Runs `fox-market-regime.py`. Saves to `market-regime-last.json`.

## 8. Health Check (every 10 minutes, isolated)

Runs `fox-health-check.py`. Auto-fixes orphan DSL, missing DSL, direction mismatches.

## Time-Aware Scan Schedule (optional)

| Session | Hours (UTC) | Scan Interval | Notes |
|---|---|---|---|
| High activity | 13-20 | 10 min | US market overlap, highest volume |
| Medium activity | 6-12, 21-22 | 15 min | EU/Asia sessions |
| Low activity | 0-5, 23 | 30 min | Low volume, wider DSL stops |

During low-activity hours, widen DSL Phase 1 retrace by 20% to avoid stops from thin-book wicks.
