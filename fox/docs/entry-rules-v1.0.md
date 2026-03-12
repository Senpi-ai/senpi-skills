# FOX v1.6 Entry Rules

## Entry Filters (ALL must pass)

1. **isFirstJump=true** — MANDATORY (waived for re-entries only)
2. **Previous rank ≥ 20** — must start from outside top 20
3. **rankJumpThisScan ≥ 15 OR contribVelocity > 15** — meaningful move
4. **priceChg4h**: score < 10 → |priceChg4h| ≤ 2%; score ≥ 10 → ≤ 4%
5. **Negative velocity → SKIP**
6. **erratic=true → SKIP**
7. **contribVelocity > 7** — MANDATORY hard gate
8. **XYZ assets require score ≥ 10**

## Scoring System

| Signal | Points |
|--------|--------|
| FIRST_JUMP | +3 (mandatory) |
| IMMEDIATE_MOVER | +2 |
| contribVelocity > 10 | +2 |
| CONTRIB_EXPLOSION | +2 |
| DEEP_CLIMBER | +1 |
| RANK_UP | +1 |
| CLIMBING | +1 |
| ACCEL | +1 |
| STREAK | +1 |
| UTC hour 04:00-13:59 | +1 |
| BTC 1h bias aligned | +1 |

**Minimum score: 7** (8 for NEUTRAL regime, 5 for re-entries)

## Market Regime

- **BEARISH** (bear ≥ 60, conf ≥ 50): SHORT only
- **BULLISH** (bull ≥ 60, conf ≥ 50): LONG only
- **NEUTRAL**: Both directions if score ≥ 8
- **Stale regime** (> 1h old): Treated as NEUTRAL

## Confirmation Filter

- **Explosive signals** (score ≥ 10 OR contribExplosion=true): Enter immediately
- **Normal signals** (score < 10): Track for 0.3% price confirmation
  - Drop after 3 scans or 0.5% adverse move
  - State tracked in `fj-last-seen.json`

## Position Sizing

- **Flat $950 margin** per trade
- **Default 10x leverage** (min(10, asset max))
- Score 8+ can use up to asset max leverage
- Minimum 3x to enter
- **Max 3 entries per day**

## Entry Execution

- Use `strategy_create_custom_strategy` for each trade (auto-funds wallet)
- Order type: `FEE_OPTIMIZED_LIMIT` with `ensureExecutionAsTaker: true`
- XYZ assets: `coin="xyz:ASSET"`, `leverageType="ISOLATED"`
- **Post-entry verification**: Check clearinghouse state for position size correctness

## Blacklist

- XPL (builder fee trap — 5.1% fee rate)
- kPEPE (Senpi uppercases to KPEPE, invalid on Hyperliquid)

## Re-Entry Rules

- Asset must have been exited within 2 hours
- Still showing strong signals in SAME direction
- Price must have moved FURTHER in our direction
- contribVelocity > 5, must be in top 20
- 75% of normal margin (reduced size)
- Score requirement: 5 pts (relaxed, FJ not required)
- Skip if first attempt lost > 15% ROE
