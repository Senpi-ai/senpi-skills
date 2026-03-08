# Tier Examples

Worked examples showing exact price levels for LONG and SHORT positions.

## LONG Example (10x leverage, entry $28.87, 1890 sz)

| Trigger | Locks | Per-tier retrace | Price triggers at | Floor locks at | Protects |
|---------|-------|--------------------|-------------------|----------------|----------|
| 10% | 5% | (global) | $29.16 | $29.01 | ~$273 |
| 20% | 14% | (global) | $29.45 | $29.27 | ~$764 |
| 30% | 22% | 1.2% | $29.74 | $29.51 | ~$1,201 |
| 50% | 40% | 1.0% | $30.31 | $30.02 | ~$2,183 |
| 75% | 60% | 0.8% | $31.04 | $30.60 | ~$3,274 |
| 100% | 80% | 0.6% | $31.76 | $31.18 | ~$4,366 |

**Trigger price formula (LONG):** `entry × (1 + triggerPct / 100 / leverage)`
- Tier 1: `28.87 × (1 + 10 / 100 / 10)` = `28.87 × 1.01` = $29.16

**Floor price formula (LONG):** `entry + (hw − entry) × lockPct / 100` (lockPct = fraction of entry→hw range to lock)
- Tier 1 (hw at trigger): e.g. `28.87 + (29.16 − 28.87) × 5 / 100` ≈ $28.88 (hw is high water when tier triggers)

**Protected profit:** `(floor - entry) × size`
- Tier 1: `(29.01 - 28.87) × 1890` = ~$273

## SHORT Example (7x leverage, entry $1,955, 3.58 sz)

| Trigger | Locks | Price triggers at | Floor locks at | Protects |
|---------|-------|-------------------|----------------|----------|
| 10% | 5% | $1,927 | $1,941 | ~$50 |
| 20% | 14% | $1,899 | $1,916 | ~$140 |
| 30% | 22% | $1,871 | $1,894 | ~$219 |

**Trigger price formula (SHORT):** `entry × (1 - triggerPct / 100 / leverage)`
- Tier 1: `1955 × (1 - 10 / 100 / 7)` = `1955 × 0.9857` = $1,927

**Floor price formula (SHORT):** `entry − (entry − hw) × lockPct / 100`
- Tier 1 (hw = 1927 at trigger): `1955 − (1955 − 1927) × 5 / 100` = 1955 − 1.4 = $1,953.60 (example; actual hw at trigger may differ)

**Protected profit (SHORT):** `(entry - floor) × size`
- Tier 1: `(1955 - 1941) × 3.58` = ~$50

## Key Concepts

- **Trigger > Lock gap**: The difference between trigger and lock gives breathing room. The floor locks lockPct% of the move from entry to high water (e.g. 5% = lock in 5% of that range). This prevents immediate closure from a minor pullback after hitting a tier.

- **Ratchets never go down**: Once Tier 2 is hit, Tier 1's floor is permanently superseded. The floor can only go up (LONG) or down (SHORT).

- **ROE calculation**: `upnl_pct = (upnl / margin) × 100` where `margin = entry × size / leverage`. This means tier triggers automatically account for leverage.
