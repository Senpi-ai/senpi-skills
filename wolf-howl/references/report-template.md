[howl-report-template.md](https://github.com/user-attachments/files/25532208/howl-report-template.md)
# 🐺🌙 HOWL — YYYY-MM-DD

## Summary
- Trades closed: X (W wins / L losses)
- Gross PnL: +/- $X
- Net PnL: +/- $X
- Fees: $X (FDR: X%)
- Win rate: X%
- Profit factor: X.Xx gross / X.Xx net
- Account: $X → $X (change: +/- $X)

## Holding Period Buckets
```
Duration     | Trades | WR   | Net PnL  | Fees
< 30 min     | X      | X%   | +/- $X   | $X
30-90 min    | X      | X%   | +/- $X   | $X
> 90 min     | X      | X%   | +/- $X   | $X
```

## Direction Breakdown
```
Direction | Trades | WR   | Net PnL  | PF
LONG      | X      | X%   | +/- $X   | X.Xx
SHORT     | X      | X%   | +/- $X   | X.Xx
```

## Trade Log
```
Asset    Dir    Entry     Exit      Gross   Fees  Net     ROE    Dur    Tier  Signal
XXXX     SHORT  $XX.XX    $XX.XX    +$XXX   $XX   +$XXX   +X.X%  X.Xh   TX    FJ Xr
...
```

## Monster Trades
- Top 3 trades: +$X (X% of total gross PnL)
- 1. [ASSET] [DIR] +$X (Tier X)
- 2. [ASSET] [DIR] +$X (Tier X)
- 3. [ASSET] [DIR] +$X (Tier X)
- Without top 3: net PnL would be +/- $X

## Rotation Analysis
- Rotations: X
- Total rotation cost: $X
- Rotation outcomes: X wins / X losses
- Net rotation PnL: +/- $X

## Signal Quality Breakdown
```
Signal       | Trades | Win Rate | Avg Net PnL
FIRST_JUMP   | X      | X%       | +/- $X
IMMEDIATE    | X      | X%       | +/- $X
CONTRIB_EXPL | X      | X%       | +/- $X
DEEP_CLIMBER | X      | X%       | +/- $X
```

## What Worked
- (bullet points with specific data backing each claim)

## What Didn't Work
- (bullet points with specific data backing each claim)

## Pattern Insights
- (new patterns discovered, with statistical evidence)

## Recommended Improvements

### High Confidence (data strongly supports)
1. [specific change to config/filters/tiers] — because [data]

### Medium Confidence (promising but needs more data)
1. [specific change] — because [data]

### Low Confidence (hypothesis to monitor)
1. [observation] — need X more trades to confirm

### Recurring ⚠️ (suggested 3+ consecutive days)
1. [change] — suggested N consecutive days, not yet implemented

## Config Suggestions
- wolf-strategy.json: [specific parameter changes]
- DSL tiers: [any tier adjustments]
- Scanner filters: [any filter changes]
- FDR gate thresholds: [any gate adjustments]
