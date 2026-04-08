# TIGER v4 Optimization Plan

Based on 24 trades across 5 missions. Data-driven changes only.

---

## The Numbers Tell the Story

```
Pattern       W/L    WR%   Total PnL   Avg PnL
COMPRESSION   4/1    80%   +$183       +$37
MOMENTUM      4/4    50%   +$157       +$20
CORRELATION   3/7    30%   -$466       -$47
REVERSION     1/0   100%   +$84        +$84

Score         W/L    WR%   Total PnL
≥0.85         6/4    60%   +$362
0.70-0.79     4/6    40%   -$142
<0.70         0/2     0%   -$335

Hold Time     W/L    WR%   Total PnL
≤1 hour       2/8    20%   -$409
>1 hour      10/4    71%   +$368

DSL Tier Hit  Count  Avg ROE   Total PnL
Tier 0 (SL)    9     -6.8%     -$729
Tier 1         3     -0.7%     +$60
Tier 2         5     +9.9%     +$188
Tier 3+        4     +26.4%    +$578
```

---

## 1. SELECT MORE WINNERS — Fix Correlation Scanner

**Problem:** Correlation lag is 3W/7L (-$466). It's the biggest PnL drain.
Every loss shared the same pattern: stale signal + low score + wrong direction.

**Root cause analysis:**
- Score <0.80: 1W/5L = -$502. Score ≥0.85: 2W/2L = +$37
- 4 of 7 losses were from stale 24h signals while 4h was reversing
- FARTCOIN LONG at score 0.65 with RSI 70.4 = -$333 (worst single trade)

**Changes:**

### 1a. Raise correlation minimum score to 0.85 (was 0.65-0.80)
```json
"pattern_confluence_overrides": {
    "CORRELATION_LAG": 0.85
}
```
This would have filtered out 5 of 7 losses (-$502), keeping only SOL and one DOGE loss.
Cost: Also filters the OP SHORT win (+$77). Net savings: ~$425.

### 1b. Hard-reject when 4h diverges from dominant window
Already implemented (`timeframe_aligned` check) but make it a HARD REJECT, not a score penalty.
If 4h is moving opposite to the signal direction → skip entirely.

### 1c. RSI filter on correlation entries
Add RSI check: reject LONG if RSI > 65, reject SHORT if RSI < 35.
FARTCOIN LONG at RSI 70.4 = dead on arrival. This is a momentum exhaustion signal, not a lag opportunity.

### 1d. Reduce correlation alt list
Trim to only high-liquidity, high-correlation alts. Remove low-volume names that gap:
- BTC list: ETH, SOL, DOGE, AVAX, LINK, XRP, SUI, APT, HYPE (9 — was 23)
- ETH list: OP, ARB, MATIC, AAVE, UNI, LDO, PENDLE (7 — was 20)

**Expected impact:** Correlation WR 30% → 55-65%. PnL swing: +$400-500.

---

## 2. HOLD WINNERS LONGER — DSL & Exit Adjustments

**Problem:** Winners that reached T2+ averaged +$192. The FARTCOIN T6 trade (+$343, 12hr hold) shows the system CAN produce runners. But too many winners exit early.

**Key insight:** DOGE peaked at +10.1% ROE (T2) but bled to -2.8%. The DSL didn't catch it because exchange SL wasn't set fast enough. Meanwhile FARTCOIN at T6 ran for 12 hours and returned +68.7% ROE.

**The fix isn't looser trailing — it's smarter trailing:**

### 2a. T1 warmup period: 10min → 5min
Current 5min warmup for T1 exchange SL is fine. But after warmup, set the SL IMMEDIATELY.
Problem was DOGE: hit T1, started warm-up, price reversed, SL never got set.

### 2b. Widen Phase 1 retrace for high-conviction entries
Trades with score ≥ 0.85 get more room to breathe:
```python
retrace = base_retrace  # 2.0%
if tracked.get("score", 0) >= 0.85:
    retrace *= 1.5  # 3.0% — lets high-conviction trades absorb noise
```
The best winners (FARTCOIN +68.7%, ZEC +19.3%, ATOM +16.3%) all had moments where they would've been shaken out by a 2% retrace.

### 2c. Remove stagnation exit for T2+ positions
Current: close if ROE stagnant for 2+ hours.
Problem: This kills runners. ATOM was "stagnant" at +10% for hours before running to +16%.
New rule: Only stagnation-exit T0/T1 positions. T2+ positions have proven momentum — let DSL manage them.

### 2d. Remove daily target exit for T3+ positions
Current: if uPnL ≥ daily target, take profit.
Problem: This caps winners. A position at T3 (+20% ROE) that still has momentum should NOT be closed just because it hit the $45/day target.
New rule: Daily target exit only applies to T0/T1 positions. T2+ let DSL trail.

### 2e. Phase 2 per-tier retrace tightening
Add explicit retrace values per tier so higher tiers trail tighter:
```json
"dsl_tiers": [
    {"triggerPct": 0.05, "lockPct": 0.02, "retrace": 0.025},
    {"triggerPct": 0.10, "lockPct": 0.06, "retrace": 0.020},
    {"triggerPct": 0.15, "lockPct": 0.11, "retrace": 0.018},
    {"triggerPct": 0.20, "lockPct": 0.16, "retrace": 0.015},
    {"triggerPct": 0.30, "lockPct": 0.25, "retrace": 0.012},
    {"triggerPct": 0.40, "lockPct": 0.34, "retrace": 0.010},
    {"triggerPct": 0.50, "lockPct": 0.44, "retrace": 0.008},
    {"triggerPct": 0.65, "lockPct": 0.57, "retrace": 0.008},
    {"triggerPct": 0.80, "lockPct": 0.72, "retrace": 0.006},
    {"triggerPct": 1.00, "lockPct": 0.90, "retrace": 0.005}
]
```
Low tiers: 2.5% retrace (breathe). High tiers: 0.5% retrace (lock tight).

**Expected impact:** Avg winner ROE 15.3% → 20-25%. Fewer early exits on good trades.

---

## 3. GET OUT OF LOSERS FASTER

**Problem:** 9 trades hit Tier 0 (SL) with avg -6.8% ROE, totaling -$729.
Hold time ≤1h = 20% WR. Most losses are fast — they never go green.

### 3a. Faster time stop: 30min → 15min for never-green trades
Current: cut losers after 30min if ROE < -2%.
New: if trade NEVER went positive (high_water_roe ≤ 0), cut at 15min regardless of ROE.
Thesis: if the trade hasn't gone your way in 15 minutes, the signal was wrong.

### 3b. Tighter initial SL for low-conviction entries
Score 0.80-0.84 → SL at -2.5% ROE (was -3%)
Score 0.85-0.89 → SL at -3.0% ROE (unchanged)
Score ≥0.90 → SL at -3.0% ROE (unchanged, high conviction gets room)

### 3c. Momentum pattern: kill if no follow-through in 2 candles
Momentum breakouts should CONTINUE breaking out. If 2 consecutive 5-min candles close opposite to entry direction → close immediately. Don't wait for SL.

### 3d. Set exchange SL immediately on entry (not just on DSL tier)
CRITICAL: Right now exchange-level SL only gets set when DSL T1 triggers.
Change: Set exchange SL at -3% ROE (or tiered per 3b) IMMEDIATELY on position open.
This is the safety net for everything — API failures, zombie processes, cron crashes.

**Expected impact:** Avg loss -6.8% → -3.5% ROE. Total loss reduction: ~$300-400.

---

## 4. USE ALO AS MUCH AS POSSIBLE

**Current state:** `FEE_OPTIMIZED_LIMIT` for entries, `MARKET` for exits.

### 4a. ALO for ALL entries (already done, verify)
Saves ~8 bps per entry. At 24 trades × avg $15K notional = ~$290 saved.

### 4b. ALO for DSL tier SL updates (NOT breach exits)
When updating exchange SL price on tier upgrade → use LIMIT order type.
The SL isn't being triggered, just set. This can be a limit order.

### 4c. ALO for stagnation/time-stop exits
These are non-urgent exits. Use `FEE_OPTIMIZED_LIMIT` + 30s timeout.
Only use MARKET for:
- DSL breach closures (urgent)
- Risk guardian hard stops (urgent)
- Daily loss limit hits (urgent)

### 4d. Track fee savings explicitly
Add `entry_fee` and `exit_fee` fields to trade log. Report maker vs taker fills.
Current fee tracking is estimated. With explicit data, we can measure ALO effectiveness and adjust.

**Expected impact:** Fee reduction from ~1.8% to ~1.0% of margin per round-trip.
On 24 trades with avg $1K margin = ~$190 saved.

---

## 5. IMPLEMENTATION PRIORITY

### Phase 1 — Immediate (config only, no code)
1. ✅ Raise correlation min score to 0.85
2. ✅ Add per-tier retrace values to DSL config
3. ✅ Set `t1_warmup_seconds` to 300 (keep)

### Phase 2 — Code changes (scanners + exit logic)
4. Correlation scanner: hard-reject on timeframe divergence
5. Correlation scanner: RSI filter (reject LONG if RSI > 65)
6. Correlation scanner: trim alt lists
7. Tiger-exit: remove stagnation/daily-target exit for T2+
8. Tiger-exit: faster time stop (15min never-green)
9. Tiger-exit: momentum no-follow-through kill
10. Entry flow: set exchange SL immediately on position open

### Phase 3 — ALO optimization
11. ALO for tier SL updates
12. ALO for non-urgent exits
13. Fee tracking in trade log

### Phase 4 — DSL code
14. Score-based retrace widening in DSL
15. Per-tier retrace reading from config in dsl-v4.py

---

## Expected Combined Impact

| Metric | Before (v3) | After (v4) | Change |
|--------|------------|------------|--------|
| Win Rate | 50% | 60-65% | +10-15pp |
| Avg Winner ROE | +15.3% | +20-25% | +5-10pp |
| Avg Loser ROE | -6.8% | -3.5% | +3.3pp |
| Fee drag/trade | 1.8% margin | 1.0% margin | -44% |
| Correlation WR | 30% | 55-65% | +25-35pp |
| Expected daily | ~0% | +1.5-3% | Sustainable |

Conservative estimate: These changes turn -$41 over 24 trades into +$400-600.
The biggest single lever is fixing correlation (-$466 → ~$0).
