### v3.1: Quality Gates on IMMEDIATE Signals

The 🔴 IMMEDIATE signals now pass through **four quality filters** before being actionable:

```
Signal fires as IMMEDIATE
  ├─ Erratic rank history? (>5 rank reversal in zigzag) → DOWNGRADE, erratic: true
  ├─ Low velocity? (contribVelocity < 0.03) → DOWNGRADE, lowVelocity: true
  ├─ Trader count < 10? → SKIP (single whale risk)
  └─ Max leverage < 10x? → SKIP (can't size properly)

ALL FOUR pass → Open position → Set DSL → Alert user
ANY fail → Logged with flags, NOT acted on
```

**Why these gates exist (from session data):**

| Asset | What Happened | Gate That Catches It |
|-------|--------------|---------------------|
| EIGEN | IMMEDIATE from #42→#12, but only 2 traders | Trader count floor (<10) |
| DASH | IMMEDIATE +18 ranks, 6 traders | Trader count floor (<10) |
| MON | IMMEDIATE from #30→#14, 6 traders | Trader count floor (<10) |
| Unknown | Rank bouncing #34→#29→#11→#29→#12 | Erratic rank history filter |
| Various | Rank jump but contribution velocity negative | Velocity gate (<0.03) |
| PNUT | 5x max leverage asset | Max leverage check (<10x) |

### Erratic Rank History Detection

The script counts **rank reversals** — direction changes in the rank history where the reversal magnitude exceeds the threshold (default: 5 positions).

```
Clean climb:   #45 → #38 → #29 → #21 → #14 → #8   ✅ Monotonic, no reversals
Erratic zigzag: #34 → #29 → #11 → #29 → #12        ❌ Reversal of +18 at position 3

Algorithm:
  For each point i in rank history (excluding endpoints):
    prev_delta = rank[i] - rank[i-1]   (negative = climbing)
    next_delta = rank[i+1] - rank[i]
    If was climbing (prev_delta < 0) and then dropped (next_delta > 5): ERRATIC
    If was dropping (prev_delta > 0) and then climbed (next_delta < -5): ERRATIC
```

An erratic pattern means the asset is bouncing around — it might look like a climber on one scan but it's noise, not a sustained rotation.

### Minimum Velocity Gate

`contribVelocity` = average change in contribution % per scan over the last 5 readings.

- **Threshold**: 0.03 (in percentage terms, i.e., 0.0003 in decimal)
- **Below threshold**: `lowVelocity: true`, excluded from IMMEDIATE
- **Why**: A rank jump without velocity means the asset jumped once but SM isn't accelerating into it. It's a blip, not a rotation.

### Max Leverage Check (External)

Before acting on ANY signal, check `/data/workspace/max-leverage.json`:

```python
import json
lev = json.load(open('/data/workspace/max-leverage.json'))
max_lev = lev.get(asset, 0)
if max_lev < 10:
    # SKIP — can't size properly at low leverage
    pass
```

See the **Max Leverage recipe** (`recipes/max-leverage.md`) for how to create and refresh this file. The scanner's `leverage` field is a conservative recommendation — always check the real max.

### Trader Count Floor

**Hard rule: Skip any IMMEDIATE with fewer than 10 traders.**

Low trader count = likely a single whale moving the contribution %, not broad SM consensus. From session data:

- EIGEN: 2 traders → correctly skipped
- DASH: 6 traders → correctly skipped  
- MON: 6 traders → correctly skipped

**Exception for XYZ assets**: XYZ DEX instruments (equities, metals) are newer and have naturally lower trader counts. For XYZ, weight reason count (3+) and rank velocity more heavily than trader count.

### Contribution Velocity

The key metric. Velocity = average change in contribution % per scan over the last 5 readings.

An asset can hold rank #8 for 10 scans — boring. But if its contribution goes 0.5% → 0.8% → 1.2% → 1.8% → 2.5%, that's velocity of +0.5%/scan. SM profits from that asset are compounding fast. Something is happening.

### Signal Evaluation Matrix (v3.1)

**Act immediately (IMMEDIATE signals, all gates pass):**
- `isImmediate: true` AND `erratic: false` AND `lowVelocity: false`
- Trader count ≥ 10
- Max leverage ≥ 10x (from max-leverage.json)
- Positive contribution velocity

**Strong signal (evaluate quickly):**
- `isDeepClimber: true` with 3+ reasons
- Contribution velocity positive and increasing
- Asset in rank #4-20

**Watch only:**
- Single detection signal only
- `erratic: true` or `lowVelocity: true`
- Under 10 SM traders
- Negative contribution velocity despite rank improvement

