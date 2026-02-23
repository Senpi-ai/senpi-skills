## Step 5: Daily Monitoring (Cron)

Set up a cron job to run once daily (recommended: 00:00 UTC).

### Cron job config

```json
{
  "name": "whale-index-daily",
  "schedule": { "kind": "cron", "expr": "0 0 * * *", "tz": "UTC" },
  "sessionTarget": "isolated",
  "payload": {
    "kind": "agentTurn",
    "message": "Run the Whale Index daily rebalance. Read recipes/whale-index/RECIPE.md Step 5 and whale-index-state.json for current state."
  },
  "delivery": { "mode": "announce" }
}
```

### Daily check procedure

**For each active slot:**

1. **Re-fetch trader stats:**
   ```
   discovery_get_trader_state(traderAddress)
   discovery_get_trader_history(traderAddress)
   ```

2. **Check health indicators:**
   - Still in top 50 on 30d Discovery? 
   - Consistency label unchanged or improved?
   - Max drawdown within 2× their historical average?
   - Active in last 48h (has placed trades)?

3. **Classify each slot:**

   | Status | Criteria |
   |--------|----------|
   | ✅ **HOLD** | Top 50, consistency stable, actively trading |
   | ⚠️ **WATCH** | Dropped to rank 30–50, OR consistency dropped one tier, OR inactive 24–48h |
   | 🔄 **SWAP** | See swap criteria below |

### Swap criteria (ALL must be true)

A trader is swapped only when:
1. **Degraded:** Dropped below rank 50 on 30d Discovery, OR consistency fell to STREAKY/CHOPPY, OR inactive 48h+, OR drawdown exceeded 2× historical avg
2. **Sustained:** Has been in WATCH status for 2+ consecutive daily checks (tracked via `watchCount` in state file)
3. **Better alternative exists:** A replacement candidate scores ≥ 15% higher than the current trader
4. **User's SL not hit:** If strategy SL already triggered, the slot is already closed — just reassign it

This prevents knee-jerk swaps. A trader has at minimum 2 days of underperformance before being replaced.

### Swap execution

1. `strategy_close_positions(strategyId, coins: [])` — close all positions in the old strategy
2. Wait for positions to close
3. `strategy_close(strategyId)` — close the strategy, funds return to main wallet
4. Run Step 2 (discover) scoped to finding one replacement
5. `strategy_create(...)` for the new trader
6. Update `whale-index-state.json`

### Rebalance (no swap needed)

If all traders are healthy but allocations have drifted significantly (one slot grew to >40% of total due to PnL):
- `strategy_top_up` the underweight slots from withdrawable balance
- Or flag for user if withdrawable balance is insufficient

### Daily report

Send to user via Telegram after each daily check:

```
🐋 WHALE INDEX — Daily Report
Feb 20, 2026

Portfolio value: $10,280 (+2.8% since inception)

Slot  Trader       30d Rank  Status   Today PnL  Total PnL
#1    0xabc...1234  #3 ↑     ✅ HOLD   +$62       +$180
#2    0xdef...5678  #10      ✅ HOLD   -$18       +$94
#3    0x123...abcd  #9 ↑     ✅ HOLD   +$41       +$122
#4    0x456...ef01  #38 ↓    ⚠️ WATCH  -$31       -$56
      ↳ Day 1 of watch. Swap candidate: 0xnew (#7, ELITE, +18% score gap)

Est. monthly fee drag: ~$28
Next check: Feb 21 @ 00:00 UTC
```

If nothing changed and all slots are HOLD, send a shorter version:

```
🐋 WHALE INDEX — All clear
Feb 20 | $10,280 (+2.8%) | All 4 slots holding | Next check tomorrow
```

---

