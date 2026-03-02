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
  "deadlineDays": 7,
  "startTime": "2026-01-01T00:00:00Z",
  "strategyId": "your-strategy-uuid",
  "strategyWallet": "0xYourStrategyWalletAddress",
  "maxSlots": 3,
  "maxLeverage": 10,
  "minLeverage": 5,
  "maxSingleLossPct": 5.0,
  "maxDailyLossPct": 12.0,
  "maxDrawdownPct": 20.0,
  "bbSqueezePercentile": 35,
  "btcCorrelationMovePct": 2.0,
  "oiCollapseThresholdPct": 25,
  "oiReduceThresholdPct": 10,
  "minConfluenceScore": {
    "CONSERVATIVE": 0.7,
    "NORMAL": 0.40,
    "ELEVATED": 0.4,
    "ABORT": 999
  },
  "trailingLockPct": {
    "CONSERVATIVE": 0.80,
    "NORMAL": 0.60,
    "ELEVATED": 0.40,
    "ABORT": 0.90
  }
}
```

Adjust `budget`, `target`, `deadlineDays`, and wallet fields for your setup.

### 4. Initialize state

#### DSL State File Format

When creating DSL state files for new positions, the file **MUST** include `"active": true` at the top level. Without this field, `dsl-v4.py` returns `{"status": "inactive"}` and will not manage the position.

Minimal DSL state file:
```json
{
  "active": true,
  "asset": "ETH",
  "direction": "LONG",
  "entryPrice": 3500.0,
  "size": 0.1,
  "leverage": 10,
  "wallet": "0xYourWallet",
  "highWaterPrice": 3500.0,
  "phase": 1,
  "currentBreachCount": 0,
  "currentTierIndex": -1,
  "tierFloorPrice": null,
  "phase1": { "retraceThreshold": 0.015, "consecutiveBreachesRequired": 3, "absoluteFloor": 3430.0 },
  "phase2": { "retraceThreshold": 0.012, "consecutiveBreachesRequired": 2 },
  "phase2TriggerTier": 1,
  "tiers": [
    { "triggerPct": 0.05, "lockPct": 0.20, "retrace": 0.015 },
    { "triggerPct": 0.10, "lockPct": 0.50, "retrace": 0.012 },
    { "triggerPct": 0.20, "lockPct": 0.70, "retrace": 0.010 },
    { "triggerPct": 0.35, "lockPct": 0.80, "retrace": 0.008 }
  ],
  "breachDecay": "soft",
  "createdAt": "2026-01-01T00:00:00Z"
}
```

⚠️ **Gotcha**: Forgetting `"active": true` is the #1 setup mistake. DSL silently does nothing without it.
`dsl-v4.py` accepts both decimal and whole-number tier percent inputs, but decimal is canonical.

Invoke DSL with: `DSL_STATE_FILE=/path/to/state.json python3 scripts/dsl-v4.py`

#### Bootstrap with Goal Engine

Run the goal engine to bootstrap state:

```bash
cd $TIGER_WORKSPACE && python3 scripts/goal-engine.py
```

This fetches current balance from clearinghouse and creates `state/tiger-state.json`.

### 5. Set up cron jobs

See [cron-templates.md](cron-templates.md) for all cron definitions. Start with:
1. OI tracker (needs 1h of history before scanners use OI data)
2. Goal engine
3. Risk guardian + exit checker
4. Scanners (after OI tracker has some history)

### 6. Monitor

Check scanner logs in `/tmp/tiger-*.log`. Each outputs JSON with `actionable` count. When a scanner finds actionable signals, the agent evaluates and may open positions.

## Adjusting Parameters

- **More signals**: Lower `minConfluenceScore.NORMAL` (e.g., 0.35). Raise `bbSqueezePercentile` (e.g., 45).
- **Fewer, higher-quality signals**: Raise confluence to 0.50+. 
- **More aggressive**: Increase `maxLeverage`, decrease `trailingLockPct`.
- **More conservative**: Decrease `maxSlots` to 2, reduce `maxSingleLossPct` to 3%.

## Stopping Tiger

1. Set `"halted": true` in `state/tiger-state.json` — scanners will skip
2. Remove scanner crons
3. Let DSL crons manage existing positions to completion
4. Remove DSL crons after all positions close
5. Run final `goal-engine.py` for a summary
