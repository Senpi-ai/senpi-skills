# Tiger Setup Guide

## Prerequisites

- Senpi account with MCP access (mcporter CLI configured)
- Hyperliquid strategy wallet (created via Senpi)
- Funded wallet with starting capital

## Step-by-Step Setup

### 1. Create workspace

```bash
export TIGER_WORKSPACE="$HOME/tiger"
mkdir -p $TIGER_WORKSPACE/state/scan-history
```

### 2. Copy scripts

Copy all scripts from this skill's `scripts/` directory into `$TIGER_WORKSPACE/scripts/`.

### 3. Create config

Create `$TIGER_WORKSPACE/tiger-config.json`:

```json
{
  "budget": 1000,
  "target": 2000,
  "deadline_days": 7,
  "start_time": "2026-01-01T00:00:00Z",
  "strategy_id": "your-strategy-uuid",
  "strategy_wallet": "0xYourWalletAddress",
  "max_slots": 3,
  "max_leverage": 10,
  "min_leverage": 5,
  "max_single_loss_pct": 5.0,
  "max_daily_loss_pct": 12.0,
  "max_drawdown_pct": 20.0,
  "min_bb_squeeze_percentile": 35,
  "btc_correlation_move_pct": 2.0,
  "min_confluence_score": {
    "CONSERVATIVE": 0.7,
    "NORMAL": 0.40,
    "ELEVATED": 0.4,
    "ABORT": 999
  },
  "trailing_lock_pct": {
    "CONSERVATIVE": 0.80,
    "NORMAL": 0.60,
    "ELEVATED": 0.40,
    "ABORT": 0.90
  }
}
```

Adjust `budget`, `target`, `deadline_days`, and wallet fields for your setup.

### 4. Initialize state

Run the goal engine to bootstrap state:

```bash
cd $TIGER_WORKSPACE && python3 scripts/goal-engine.py
```

This fetches current balance from clearinghouse and creates `state/tiger-state.json`.

### 5. Set up cron jobs

See [cron-setup.md](cron-setup.md) for all cron definitions. Start with:
1. OI tracker (needs 1h of history before scanners use OI data)
2. Goal engine
3. Risk guardian + exit checker
4. Scanners (after OI tracker has some history)

### 6. Monitor

Check scanner logs in `/tmp/tiger-*.log`. Each outputs JSON with `actionable` count. When a scanner finds actionable signals, the agent evaluates and may open positions.

## Adjusting Parameters

- **More signals**: Lower `min_confluence_score.NORMAL` (e.g., 0.35). Lower `min_bb_squeeze_percentile` (e.g., 25).
- **Fewer, higher-quality signals**: Raise confluence to 0.50+. 
- **More aggressive**: Increase `max_leverage`, decrease `trailing_lock_pct`.
- **More conservative**: Decrease `max_slots` to 2, increase `max_single_loss_pct` to 3%.

## Stopping Tiger

1. Set `"halted": true` in `state/tiger-state.json` — scanners will skip
2. Remove scanner crons
3. Let DSL crons manage existing positions to completion
4. Remove DSL crons after all positions close
5. Run final `goal-engine.py` for a summary
