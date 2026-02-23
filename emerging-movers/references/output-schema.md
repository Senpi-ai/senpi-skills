## Output JSON Schema

```json
{
  "status": "ok",
  "time": "2026-02-23T14:41:36Z",
  "totalMarkets": 50,
  "scansInHistory": 60,
  "alerts": [ ... ],
  "immediateMovers": [ ... ],
  "deepClimbers": [ ... ],
  "hasImmediate": true,
  "hasEmergingMover": true,
  "hasDeepClimber": true,
  "top5": [ ... ]
}
```

### Alert Fields

| Field | Type | Purpose |
|-------|------|---------|
| `signal` | string | Human-readable: "ASTER SHORT", "ZRO LONG" |
| `direction` | string | "LONG" or "SHORT" — the SM direction |
| `currentRank` | int | Current position in SM profit leaderboard |
| `contribution` | float | % of total SM profit from this asset |
| `contribVelocity` | float | Avg contribution change per scan (last 5) |
| `traders` | int | Number of SM traders positioned |
| `priceChg4h` | float | Price change over 4h rolling window |
| `reasons` | array | Detection signals that fired |
| `reasonCount` | int | Number of signals (more = stronger) |
| `rankHistory` | array | Last 5 ranks + current (null = wasn't ranked) |
| `contribHistory` | array | Last 5 contributions + current |
| `isImmediate` | bool | True if this is a first-jump signal (act NOW) — **v3.1: only if gates pass** |
| `isDeepClimber` | bool | True if climbing from deep ranks (#25+) |
| `erratic` | bool | **v3.1:** True if rank history shows zigzag pattern |
| `lowVelocity` | bool | **v3.1:** True if contribVelocity < 0.03 |

### v3.1 Downgrade Notes in Reasons Array

When an IMMEDIATE is downgraded, the reasons array includes an explanation:

```json
{
  "reasons": [
    "IMMEDIATE_MOVER +18 from #34 in ONE scan",
    "RANK_UP +18 (#34→#16)",
    "CLIMBING +22 over 5 scans",
    "⚠️ DOWNGRADED: erratic rank history (zigzag)",
    "⚠️ DOWNGRADED: low velocity (0.0120 < 0.03)"
  ],
  "isImmediate": false,
  "erratic": true,
  "lowVelocity": true
}
```

