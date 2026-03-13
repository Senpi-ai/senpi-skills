# 🐍 VIPER v1.1 — Range-Bound Liquidity Sniper

The chop predator. Detects assets in tight ranges (low ATR, tight BBands, declining volume), enters at support/resistance with tight stops. Works when trending strategies are sitting idle.

## v1.1 Changes
- **Full capital deployment:** marginPct raised from 12% to 28% per trade (3 slots × 28% = 84% max utilization)
- **USD-weighted OI filtering:** Candidates filtered by real dollar OI (markPx × openInterest), not raw coin count. Min $5M USD OI.
- **Dead weight cut disabled:** Positions aren't cut for being flat. Hard timeout handles real duds.
- **Wider Phase 1 floor:** 6% → 8% ROE retrace. Lets range trades breathe through boundary noise.
- **Longer timeouts:** Hard timeout 60→75min, weak peak 30→35min. Range trades need patience.
- **Stagnation TP:** Close at 5%+ ROE if high water stale for 30 min. Don't round-trip green into red.
- **Higher leverage default:** 7x → 8x.
- **SL always MARKET:** Explicit in config. Never ALO for stop losses.

## Architecture

| Script | Freq | Purpose |
|--------|------|---------|
| `viper-scanner.py` | 5 min | Scan top 30 assets (by USD OI) for range setups: BB/RSI/ATR analysis at boundaries |
| DSL v5.3.1 (shared) | 3 min | Trailing stops with 6-tier ratchet |

## Capital Allocation
- 3 concurrent slots at 28% margin each = 84% max deployment
- 16% reserve for drawdown buffer
- At $1,000 budget: ~$280 per trade at 8x leverage = $2,240 notional per position

## Notification Policy
Only alert on position OPENED or CLOSED. All crons run isolated. Silence = hunting.

## Setup
1. Set `VIPER_WALLET` and `VIPER_STRATEGY_ID` env vars (or fill `viper-config.json`)
2. Create cron: scanner every 5 min (isolated) + DSL v5.3.1 every 3 min (isolated)
3. Top up strategy to full budget — VIPER deploys capital aggressively
