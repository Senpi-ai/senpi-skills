[report-template.md](https://github.com/user-attachments/files/25506627/report-template.md)
# Report Template

## Full Report (saved to memory/howl-YYYY-MM-DD.md)

```markdown
# WOLF HOWL — YYYY-MM-DD

## Summary
- Trades closed: X (W wins / L losses)
- Net PnL: +/- $X
- Win rate: X%
- Profit factor: X.Xx (total_wins / total_losses)
- Account: $X → $X (change: +/- $X)
- Slot utilization: ~X% of time filled
- Active positions at EOD: X

## Trade Log
(use code block for Telegram-safe formatting)

Asset    Dir    Entry     Exit      PnL     ROE     Dur    Tier  Signal (reasons)
HYPE     SHORT  $31.09    $26.86    +$560   +15.5%  4.2h   T4    IMMEDIATE (5)
XRP      SHORT  $1.361    $1.256    +$303   +7.7%   0.3h   T3    IMMEDIATE (4)
MU       LONG   $92.50    $90.12    -$58    -2.9%   1.1h   --    DEEP_CLIMBER (2)

## What Worked
- [pattern]: [specific evidence]
  e.g. "IMMEDIATE signals with 5+ reasons: 3/3 wins, avg +$376"

## What Didn't Work
- [pattern]: [specific evidence]
  e.g. "Entries with < 3 reasons: 1/4 wins, avg -$47"

## Pattern Insights
- [new finding with data]

## Signal Quality Breakdown

Reasons  Trades  Win Rate  Avg PnL   Best         Worst
2        X       X%        +/- $X    ASSET +$X    ASSET -$X
3        X       X%        +/- $X    ASSET +$X    ASSET -$X
4        X       X%        +/- $X    ASSET +$X    ASSET -$X
5+       X       X%        +/- $X    ASSET +$X    ASSET -$X

## DSL Performance

Tier Reached   Count   Avg PnL    Avg Duration
Phase 1 (no T) X       -$X        Xmin
Tier 1         X       +$X        Xmin
Tier 2         X       +$X        Xmin
Tier 3         X       +$X        Xmin
Tier 4         X       +$X        Xmin

## Recommended Improvements

### High Confidence
1. [change] — [data reason]

### Medium Confidence
1. [change] — [data reason]

### Low Confidence
1. [hypothesis] — need more data

## Config Suggestions
- [parameter]: current=X → suggested=Y — [reason]
```

## Telegram Summary Format

```
🔍 WOLF HOWL — YYYY-MM-DD

X trades | Y% win rate | +/- $Z net
Best: [ASSET] +$X | Worst: [ASSET] -$X
Profit factor: X.Xx

💡 Top insight: [one key finding]
📋 Full report: memory/howl-YYYY-MM-DD.md

Suggested changes: [brief summary]
```
