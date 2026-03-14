# 🐍 MAMBA v2.0 — Range-Bound High Water + Regime Protection

A trading strategy (config override) based on the VIPER skill. Same scanner, same range detection, same support/resistance entries. Three new protective gates from v1.0 live data (37 trades, -31.4% ROI, $136 in fees).

**Base skill:** [VIPER v2.1](https://github.com/Senpi-ai/senpi-skills/tree/viper-2-0/viper)

## What v2.0 Fixes

MAMBA v1.0 lost -$313 on $1,000. The post-mortem revealed three distinct failure modes:

| Failure | Trades | Loss | Fix |
|---|---|---|---|
| Longs in a downtrend (GOLD, PAXG, XYZ100, COPPER) | 14 | -$70+ | **BTC regime gate** |
| Same asset re-entered after losses (GOLD 5x, PAXG 4x, XYZ100 5x) | ~14 | -$80+ | **4-hour per-asset cooldown** |
| 15x desperation bets (BTC, XRP) | 2 | -$126 | **10x leverage hard cap** |

The 9 winning trades (+$143) proved the mean-reversion signal works. The losses came from entering that signal in conditions where mean reversion can't work (trending market, same broken setup, excessive leverage).

## v2.0 Changes — Three Protective Gates

### Gate 1: BTC Regime Filter (NEW — hard block)

Before any entry, check BTC 4H trend structure:

- **BTC 4H BEARISH → block all LONG entries.** Mean reversion longs in a bear market = buying a dip that keeps dipping. This was trades #21-34.
- **BTC 4H BULLISH → block all SHORT entries.** Mean reversion shorts in a bull market = shorting a rip that keeps ripping.
- **BTC 4H NEUTRAL → both directions allowed.** Range-bound macro = range-bound entries work.

**Implementation:** The agent must call `market_get_asset_data` for BTC with `candle_intervals: ["4h"]` before executing any Mamba entry. Check the last 6 candles for higher lows (bullish) or lower highs (bearish). If the regime opposes the signal direction, skip the entry entirely.

This is the single highest-impact change. It would have prevented 14+ losing trades.

### Gate 2: Per-Asset Cooldown After Losses (NEW — 4 hours)

After any losing exit on an asset, that specific asset is blocked for 4 hours.

- GOLD exits at a loss → no GOLD entries for 4 hours
- Other assets unaffected
- After 4 hours, if the setup appears again with the regime gate passing, it's a valid fresh entry

**Why 4 hours (not 2 like Vixen):** Mean-reversion setups take longer to reset than momentum setups. The range needs to re-establish after a breakdown. 4 hours gives enough time for the failed level to either confirm as broken (asset keeps trending) or rebuild (new range forms).

**Implementation:** The agent tracks `{asset: exitTimestamp}` in state. Before executing, check if the asset has a losing exit within the last 4 hours.

### Gate 3: Hard Leverage Cap at 10x (NEW)

- **Maximum leverage: 10x.** No exceptions.
- **Default leverage: 8x.** This is what v1.0 used on most trades and it's appropriate for mean-reversion.
- **Never exceed 10x.** The 15x BTC/XRP shorts lost -$126 combined. Mean reversion captures small moves back to the mean — it doesn't need high leverage.

**Implementation:** If the agent computes leverage > 10x for any reason, cap it at 10.

## MANDATORY: DSL High Water Mode

**MAMBA MUST use DSL High Water Mode. This is not optional.**

Spec: https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md

```json
{
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 5,
  "tiers": [
    {"triggerPct": 5,  "lockHwPct": 30, "consecutiveBreachesRequired": 3},
    {"triggerPct": 10, "lockHwPct": 50, "consecutiveBreachesRequired": 2},
    {"triggerPct": 15, "lockHwPct": 70, "consecutiveBreachesRequired": 2},
    {"triggerPct": 20, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
  ]
}
```

## Config Override File (v2.0)

```json
{
  "basedOn": "viper-2.1",
  "version": "2.0",
  "name": "Mamba",
  "description": "Range-bound entries + High Water trailing + regime protection + per-asset cooldown",

  "entry": {
    "maxBbWidthPct": 4.0,
    "maxAtrPct": 1.5,
    "rsiOversold": 35,
    "rsiOverbought": 65,
    "minScore": 5,
    "marginPct": 0.28,
    "minOiUsd": 5000000,
    "regimeFilter": {
      "enabled": true,
      "asset": "BTC",
      "interval": "4h",
      "lookback": 6,
      "blockLongsInBearish": true,
      "blockShortsInBullish": true,
      "_note": "Check BTC 4H trend before every entry. Block counter-trend mean reversion."
    },
    "assetCooldown": {
      "enabled": true,
      "cooldownMinutesAfterLoss": 240,
      "_note": "4-hour cooldown per asset after a losing exit. Mean reversion needs time to reset."
    },
    "bannedPrefixes": ["xyz:"],
    "_note_banned": "XYZ equities (GOLD, PAXG, NVDA, etc.) accounted for -$80+ in v1.0 losses. SM data is weak for equities on Hyperliquid. Ban until proven otherwise."
  },

  "leverage": {
    "default": 8,
    "min": 5,
    "max": 10,
    "_note": "Hard cap at 10x. Mean reversion captures small moves — doesn't need 15x. The 15x BTC/XRP shorts lost -$126."
  },

  "dsl": {
    "_spec": "https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md",
    "lockMode": "pct_of_high_water",
    "phase2TriggerRoe": 5,
    "phase1RetraceRoe": 10,
    "phase1HardTimeoutMin": 0,
    "phase1WeakPeakMin": 0,
    "phase1DeadWeightMin": 0,
    "convictionTiers": [
      {"minScore": 5, "absoluteFloorRoe": -15, "hardTimeoutMin": 0, "weakPeakCutMin": 0, "deadWeightCutMin": 0},
      {"minScore": 7, "absoluteFloorRoe": -20, "hardTimeoutMin": 0, "weakPeakCutMin": 0, "deadWeightCutMin": 0},
      {"minScore": 9, "absoluteFloorRoe": 0,   "hardTimeoutMin": 0, "weakPeakCutMin": 0, "deadWeightCutMin": 0}
    ],
    "tiers": [
      {"triggerPct": 5,  "lockHwPct": 30, "consecutiveBreachesRequired": 3},
      {"triggerPct": 10, "lockHwPct": 50, "consecutiveBreachesRequired": 2},
      {"triggerPct": 15, "lockHwPct": 70, "consecutiveBreachesRequired": 2},
      {"triggerPct": 20, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
    ],
    "stagnationTp": {
      "enabled": true,
      "roeMin": 8,
      "hwStaleMin": 45
    }
  },

  "risk": {
    "maxEntriesPerDay": 6,
    "maxDailyLossPct": 8,
    "maxDrawdownPct": 18,
    "maxSingleLossPct": 5,
    "maxConsecutiveLosses": 3,
    "cooldownMinutes": 30
  },

  "execution": {
    "entryOrderType": "FEE_OPTIMIZED_LIMIT",
    "entryEnsureTaker": true,
    "exitOrderType": "MARKET",
    "slOrderType": "MARKET",
    "takeProfitOrderType": "FEE_OPTIMIZED_LIMIT"
  }
}
```

## Key Changes from v1.0

| Setting | v1.0 | v2.0 | Impact |
|---|---|---|---|
| BTC regime gate | None | **Hard block** | Prevents longs in bear, shorts in bull |
| Per-asset cooldown | None | **4 hours after loss** | Prevents re-entering failed setups |
| Leverage cap | Uncapped (agent went to 15x) | **Hard cap 10x, default 8x** | Eliminates desperation bets |
| XYZ equities | Allowed | **Banned** | Removes -$80+ in weak-SM-data losses |
| Max entries/day | 8 | **6** | Fewer trades, less fee drag |
| Max consecutive losses | 4 | **3** | Faster cooldown trigger |
| Cooldown after consecutive losses | 20 min | **30 min** | More recovery time |

## v1.0 → v2.0 Simulated Impact

Applying v2.0 rules retroactively to v1.0's 37 trades:

| | v1.0 (actual) | v2.0 (simulated) |
|---|---|---|
| Trades taken | 37 | ~15-18 (regime gate + cooldown blocks ~20 trades) |
| Fees | $136 | ~$55-65 |
| Worst losses | BTC 15x (-$65), XRP 15x (-$61) | Blocked (leverage cap + regime gate) |
| GOLD/PAXG/XYZ100 repeat losses | -$80+ | Blocked after first loss (cooldown) |
| Winners preserved | 9 wins (+$143) | ~7-8 wins preserved (~$120-130) |
| Estimated net | -$313 | **~+$30 to +$60** |

The 9 winning trades were mostly early-session shorts during neutral/bearish conditions — they'd pass the regime gate. Most of the 28 losers were longs during bearish conditions or repeat entries on the same failing asset — they'd be blocked.

## Agent Instructions (v2.0 Entry Checklist)

Before executing any Mamba entry, the agent MUST check ALL of these in order:

1. **Leverage ≤ 10x?** If not, cap at 10x. If exchange max is below 5x, skip entirely.
2. **Asset banned?** If it starts with `xyz:`, skip.
3. **Asset in cooldown?** Check state for losing exit within last 4 hours. If yes, skip.
4. **BTC regime allows this direction?** Fetch BTC 4H candles, check trend. Bearish = no longs. Bullish = no shorts. Neutral = both OK.
5. **All standard Viper entry gates pass?** BB width, ATR, RSI, volume, score.
6. **Slot available?** Max 3 positions.
7. **Daily entry limit?** Max 6/day.

If ALL pass → enter. If any fails → skip, NO_REPLY.

## Notification Policy

**ONLY alert:** Position OPENED or CLOSED, risk guardian triggered, critical error.
**NEVER alert:** Scanner found nothing, regime gate blocked, cooldown blocked, DSL routine check, any reasoning.

## Expected Behavior (v2.0)

| Metric | v1.0 (actual) | v2.0 (expected) |
|---|---|---|
| Trades/day | ~12 | 4-6 |
| Win rate | 24% | ~45-55% (bad entries filtered) |
| Avg winner | +$15.85 | +$15-20 (same good trades preserved) |
| Avg loser | -$11.42 | -$8-12 (no 15x blowups, faster cooldown) |
| Fee drag/day | $45+ | ~$15-20 |
| Net PnL/day | -$100+ | Target breakeven to +$20 |
