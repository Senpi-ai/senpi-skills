## Real-World Examples

### Example 1: ASTER SHORT — Perfect v3.1 Signal (Feb 22, 2026)

```
Scan N-1: ASTER at #36, contribution 0.16%, 18 traders
Scan N:   ASTER at #13, contribution 0.82%, 65 traders

Signals fired:
  • IMMEDIATE_MOVER +23 from #36 in ONE scan
  • CONTRIB_EXPLOSION 5.3x in one scan (0.16→0.82)
  • CLIMBING +23 over 5 scans
  • ACCEL +0.007 contribution
  • STREAK climbing 23 ranks over 4 checks
  = 6 reasons, isImmediate=true, isDeepClimber=true

v3.1 gates:
  ✅ erratic: false (clean climb)
  ✅ lowVelocity: false (velocity = 0.14, well above 0.03)
  ✅ traders: 65 (≥ 10)
  ✅ max leverage: 10x+
  → OPEN POSITION
```

### Example 2: EIGEN — Correctly Skipped (Feb 23, 2026)

```
IMMEDIATE_MOVER signal fired.
But: only 2 traders.

v3.1 gate check:
  ✅ erratic: false
  ✅ lowVelocity: false
  ❌ traders: 2 (< 10, single whale risk)
  → SKIP
```

### Example 3: Erratic Zigzag — Correctly Downgraded

```
Rank history: [34, 29, 11, 29, 12]
  • Point 2 (rank 11): was climbing (29→11, delta -18), then dropped (11→29, delta +18)
  • Reversal of +18 > threshold of 5 → ERRATIC

v3.1 processing:
  • Original: isImmediate=true (big rank jump)
  • After filter: isImmediate=false, erratic=true
  • Reason added: "⚠️ DOWNGRADED: erratic rank history (zigzag)"
  → NOT acted on, logged for transparency
```

### Example 4: DASH — Low Trader Count Skip (Feb 23, 2026)

```
DASH IMMEDIATE_MOVER from #38→#15 (clean climb, good velocity)
But: only 6 traders.

v3.1: traders < 10 → SKIP
Saved from potential single-whale fake-out.
```

### Example 5: Low Leverage Asset — Skipped

```
PNUT IMMEDIATE_MOVER from #32→#18
max-leverage.json: PNUT = 5x

v3.1: max leverage 5x < 10x threshold → SKIP
Can't size the position properly at low leverage. Not worth the slot.
```

## Proven Skip Patterns (Session Summary)

From the Feb 23 session, 30+ IMMEDIATE signals were correctly skipped:

| Skip Reason | Count | Examples |
|------------|-------|---------|
| Low trader count (<10) | ~10 | EIGEN (2), DASH (6), MON (6) |
| Erratic rank history | ~5 | Zigzag patterns |
| Negative/low velocity | ~8 | Assets with rank jump but no acceleration |
| Low max leverage (<10x) | ~3 | 5x assets |
| Already peaked (neg velocity) | ~4 | SM already rotating out |

**Net result**: 9 of 14 trades were winners (64%), +$750 realized. The skip discipline was as important as the entry signals.

