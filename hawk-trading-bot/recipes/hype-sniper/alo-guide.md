# Senpi Fee Optimization (ALO) — Guide for Trading Agents

## Fee Comparison

| | Taker (MARKET) | Maker (ALO) |
|---|---|---|
| Hyperliquid fee (Tier 0) | 4.5 bps (0.045%) | 1.5 bps (0.015%) |
| Senpi builder fee | 5 bps | 5 bps |
| **Round-trip cost** | **~19 bps** | **~13 bps** |

## How To Use

### 1. Aggressive (MARKET) — immediate fill, highest fees
```
orderType: "MARKET"
```

### 2. Fee-optimized with guaranteed fill (recommended for entries)
```
orderType: "FEE_OPTIMIZED_LIMIT"
ensureExecutionAsTaker: true
```
Places maker order, falls back to market after 60s. Blocks for up to 60s.

### 3. Fee-optimized resting (patient)
```
orderType: "FEE_OPTIMIZED_LIMIT"
ensureExecutionAsTaker: false
```
Stays on book until filled or cancelled. Monitor via `strategy_get_open_orders`.

## Constraints
- `limitPrice`, `timeInForce`, `slippagePercent` CANNOT be used with `FEE_OPTIMIZED_LIMIT`
- TP/SL can still be set alongside ALO orders
- `strategy_close` always uses market internally
- `edit_position` also supports `FEE_OPTIMIZED_LIMIT`

## Recommended Hybrid Approach
```
Entries:  FEE_OPTIMIZED_LIMIT + ensureExecutionAsTaker: true
Closes:   MARKET (for stops/emergencies), ALO (for take-profits)
SL/TP:    Always MARKET
```

Saves ~3 bps on every entry (33% reduction in HL fees).

At 10x on $1K margin ($10K notional): saves $3/round-trip.
At 5 trades/day = $15/day or ~$450/month.

## Detection
Response includes `executionAsMaker: true/false` in `mainOrder` object.
